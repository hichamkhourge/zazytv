"""
ViewTVY - Automated Trial Account Creation

Automates the ViewTVY WHMCS free-trial checkout flow
(https://viewtvy.com/billing/index.php?rp=/store/free-trial) and extracts Xtream credentials
from the client-area email history, then optionally pushes them into an IBO Player playlist.

ViewTVY's store is a single free "24hr Free Trial" product with no device/bouquet configuration,
so the flow is: add trial to cart -> register a fresh account at checkout -> complete order ->
poll client-area Email History for the credentials email -> save to IBO Player.

Install deps: pip install selenium webdriver-manager 2captcha-python python-dotenv requests
"""

import argparse
import html
import json
import os
import random
import re
import string
import time
from datetime import datetime
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait
from twocaptcha import TwoCaptcha
from webdriver_manager.chrome import ChromeDriverManager

load_dotenv()

import iboplayer_auth

try:
    from telegram_notifier import notifier
except ImportError:
    class DummyNotifier:
        def notify_success(self, *args, **kwargs):
            return False

        def notify_error(self, *args, **kwargs):
            return False

    notifier = DummyNotifier()

TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY")
VIEWTVY_BASE_URL = os.getenv("VIEWTVY_BASE_URL", "https://viewtvy.com").rstrip("/")
VIEWTVY_BILLING_PATH = os.getenv("VIEWTVY_BILLING_PATH", "/billing").strip("/")
VIEWTVY_TRIAL_URL = os.getenv(
    "VIEWTVY_TRIAL_URL",
    f"{VIEWTVY_BASE_URL}/{VIEWTVY_BILLING_PATH}/index.php?rp=/store/free-trial/24hr-free-trial",
)
EMAIL_POLL_SECONDS = int(os.getenv("VIEWTVY_EMAIL_POLL_SECONDS", "60"))
EMAIL_MAX_WAIT_SECONDS = int(os.getenv("VIEWTVY_EMAIL_MAX_WAIT_SECONDS", "3600"))
# Must stay below Selenium's 120s HTTP transport timeout so a hung navigation raises
# a catchable TimeoutException instead of killing the chromedriver connection.
PAGE_LOAD_TIMEOUT_SECONDS = int(os.getenv("VIEWTVY_PAGE_LOAD_TIMEOUT_SECONDS", "90"))
PAGE_LOAD_RETRIES = int(os.getenv("VIEWTVY_PAGE_LOAD_RETRIES", "3"))
VIEWTVY_PORTAL_HOST = os.getenv("VIEWTVY_PORTAL_HOST", "")
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"

# IBO Player playlist update (optional): push the extracted Xtream credentials into an
# existing IBO Player playlist via their savePlaylist API. Authentication uses the
# device-login bearer token from iboplayer_auth (IBOPLAYER_MAC_ADDRESS /
# IBOPLAYER_DEVICE_KEY / TWOCAPTCHA_API_KEY).
VIEWTVY_IBOPLAYER_ENABLED = os.getenv("VIEWTVY_IBOPLAYER_ENABLED", "False").lower() == "true"
VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID = os.getenv("VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID", "")
VIEWTVY_IBOPLAYER_PLAYLIST_NAME = os.getenv("VIEWTVY_IBOPLAYER_PLAYLIST_NAME", "ViewTVY")

# ViewTVY's credentials email subject is not publicly documented, so by default we scan the
# email-history rows for any credential-bearing subject. Set VIEWTVY_READY_EMAIL_SUBJECT to
# pin an exact subject once it is known.
READY_EMAIL_SUBJECT = os.getenv("VIEWTVY_READY_EMAIL_SUBJECT", "").strip()
READY_EMAIL_KEYWORDS = [
    "trial",
    "access",
    "login",
    "account",
    "iptv",
    "details",
    "welcome",
    "subscription",
]
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY else None


def get_driver():
    options = Options()
    # Return from driver.get() at DOMContentLoaded instead of waiting for every slow
    # third-party resource; all navigations are followed by explicit WebDriverWait calls.
    options.page_load_strategy = "eager"
    headless_mode = os.getenv("HEADLESS", "True").lower() == "true"

    if headless_mode:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        print("[*] Running in HEADLESS mode")
    else:
        options.add_argument("--start-maximized")
        options.add_experimental_option("detach", True)
        print("[*] Running in GUI mode")

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    if os.path.exists(chromedriver_path):
        print(f"[*] Using pre-installed ChromeDriver at {chromedriver_path}")
        service = Service(chromedriver_path)
    else:
        print("[*] Downloading/verifying ChromeDriver...")
        service = Service(ChromeDriverManager().install())

    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_SECONDS)
    return driver


def load_page(driver, url, retries=None):
    """Navigate to url with a bounded page-load timeout and retries.

    WHMCS stores sometimes hang while loading; without this, driver.get() blocks past
    Selenium's transport timeout and the whole run dies with an unrecoverable
    ReadTimeoutError. Retrying the navigation recovers those runs.
    """
    retries = PAGE_LOAD_RETRIES if retries is None else retries
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            driver.get(url)
            return
        except Exception as exc:
            last_error = exc
            print(f"[!] Page load failed (attempt {attempt}/{retries}) for {url}: {exc}")
            try:
                driver.execute_script("window.stop();")
            except Exception:
                pass
            if attempt < retries:
                time.sleep(5 * attempt)
    raise last_error


def safe_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.4)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)


def find_clickable_by_text(driver, terms, timeout=15):
    terms = [term.lower() for term in terms]
    end = time.time() + timeout
    while time.time() < end:
        candidates = driver.find_elements(
            By.XPATH,
            "//a|//button|//input[@type='submit' or @type='button' or @type='checkbox']|//label|//*[@role='button']",
        )
        for el in candidates:
            if not el.is_displayed() or not el.is_enabled():
                continue
            text = " ".join(
                filter(
                    None,
                    [
                        el.text,
                        el.get_attribute("value"),
                        el.get_attribute("title"),
                        el.get_attribute("aria-label"),
                        el.get_attribute("href"),
                    ],
                )
            ).lower()
            if any(term in text for term in terms):
                return el
        time.sleep(0.5)
    raise TimeoutError(f"Could not find clickable element containing: {terms}")


def type_if_present(driver, by, selector, value):
    try:
        el = driver.find_element(by, selector)
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        el.clear()
        el.send_keys(value)
        return True
    except Exception:
        return False


def select_by_name_if_present(driver, name, value=None, text_contains=None, fallback_index=1):
    try:
        select_el = driver.find_element(By.NAME, name)
        select = Select(select_el)
        if value is not None:
            try:
                select.select_by_value(value)
                return True
            except Exception:
                pass
        if text_contains is not None:
            needle = text_contains.lower()
            for option in select.options:
                if needle in option.text.strip().lower():
                    select.select_by_visible_text(option.text)
                    return True
        if len(select.options) > fallback_index:
            select.select_by_index(fallback_index)
            return True
    except Exception:
        return False
    return False


def dump_debug(driver, label):
    print(f"\n--- [{label}] URL: {driver.current_url} TITLE: {driver.title} ---")
    for el in driver.find_elements(By.XPATH, "//a|//button|//input[@type='submit' or @type='button']")[:80]:
        text = el.text or el.get_attribute("value") or el.get_attribute("href") or ""
        if text.strip():
            print(f"  {text.strip()[:160]}")
    print("---\n")


def add_trial_to_cart(driver):
    """Add the free-trial product to the cart and advance to the checkout form.

    ViewTVY's trial product has no device/bouquet configuration, so this is a plain WHMCS
    store flow: open the product, click Order Now / Add to Cart, then Checkout / Continue
    until the client-details form is present.
    """
    print(f"[*] Navigating to ViewTVY trial product: {VIEWTVY_TRIAL_URL}")
    load_page(driver, VIEWTVY_TRIAL_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    print(f"[*] Landing URL: {driver.current_url}")

    # Visiting the free-trial product URL auto-adds it to the cart and redirects to the
    # cart review (cart.php?a=view); a separate "Order Now" click is only needed if we're
    # still on a product/store page.
    already_in_cart = (
        "cart.php?a=view" in driver.current_url
        or "a=checkout" in driver.current_url
        or "firstname" in driver.page_source.lower()
    )
    if not already_in_cart:
        print("[*] Adding trial to cart...")
        try:
            order_btn = find_clickable_by_text(
                driver, ["order now", "add to cart", "get free trial", "order"], timeout=15
            )
            safe_click(driver, order_btn)
            WebDriverWait(driver, 20).until(
                lambda d: "a=view" in d.current_url
                or "a=checkout" in d.current_url
                or "checkout" in d.current_url.lower()
                or "firstname" in d.page_source.lower()
            )
            time.sleep(2)
        except Exception as exc:
            dump_debug(driver, "ORDER BUTTON NOT FOUND")
            raise RuntimeError(f"Could not find the trial order button: {exc}")
    else:
        print("[*] Trial already in cart (auto-added by product URL)")

    # Advance from the cart review to the checkout/client-details form.
    if "firstname" not in driver.page_source.lower() and "a=checkout" not in driver.current_url:
        print("[*] Cart review reached; clicking Checkout/Continue...")
        try:
            checkout_btn = find_clickable_by_text(
                driver, ["checkout", "continue", "complete order"], timeout=15
            )
            safe_click(driver, checkout_btn)
        except Exception as exc:
            dump_debug(driver, "CHECKOUT NOT FOUND")
            raise RuntimeError(f"Could not continue to checkout: {exc}")

    WebDriverWait(driver, 20).until(
        lambda d: "a=checkout" in d.current_url
        or "checkout" in d.current_url.lower()
        or "firstname" in d.page_source.lower()
    )
    time.sleep(2)
    print(f"[*] Checkout URL: {driver.current_url}")


def generate_password(length=16):
    chars = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    required = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%^&*()-_=+"),
    ]
    required.extend(random.choice(chars) for _ in range(length - len(required)))
    random.shuffle(required)
    return "".join(required)


def generated_user_data():
    rnd = "".join(random.choices(string.ascii_lowercase + string.digits, k=7))
    first = random.choice(["John", "James", "Michael", "Robert", "David", "William", "Chris", "Daniel"])
    last = random.choice(["Smith", "Johnson", "Brown", "Miller", "Davis", "Wilson", "Taylor", "Anderson"])
    email = f"{first.lower()}.{last.lower()}.{rnd}@gmail.com"
    return {
        "firstname": first,
        "lastname": last,
        "email": email,
        "phonenumber": "2125551234",
        "address1": "123 Main St",
        "city": "New York",
        "state": "NY",
        "postcode": "10001",
        "password": generate_password(),
    }


def fill_checkout_form(driver):
    print("[*] Filling checkout/client details...")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    data = generated_user_data()

    for name, value in data.items():
        if name == "password":
            continue
        type_if_present(driver, By.NAME, name, value)

    try:
        country_el = driver.find_element(By.NAME, "country")
        Select(country_el).select_by_value("US")
    except Exception:
        pass

    select_by_name_if_present(driver, "state", value="NY", text_contains="New York")
    select_by_name_if_present(driver, "securityqid", fallback_index=1)
    type_if_present(driver, By.NAME, "securityqans", "blue")

    password_inputs = driver.find_elements(By.XPATH, "//input[@type='password']")
    for field in password_inputs:
        try:
            field.clear()
            field.send_keys(data["password"])
        except Exception:
            driver.execute_script(
                "arguments[0].value = arguments[1]; arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
                field,
                data["password"],
            )

    for selector in ("accepttos", "tos", "terms"):
        try:
            checkbox = driver.find_element(By.ID, selector)
            if not checkbox.is_selected():
                safe_click(driver, checkbox)
        except Exception:
            pass

    for checkbox in driver.find_elements(By.XPATH, "//input[@type='checkbox']"):
        try:
            name_value = " ".join(
                filter(None, [checkbox.get_attribute("name"), checkbox.get_attribute("id")])
            ).lower()
            if any(term in name_value for term in ["tos", "terms", "accept"]):
                if not checkbox.is_selected():
                    safe_click(driver, checkbox)
        except Exception:
            pass

    print(f"[OK] Checkout form filled for {data['email']}")
    print(f"[*] Client password: {data['password']}")
    return data


def solve_recaptcha_v2(driver, timeout=120, max_retries=2):
    if not solver:
        print("[!] 2captcha solver not configured; set TWOCAPTCHA_API_KEY or solve manually in GUI mode")
        return False

    site_key = None
    for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
        src = iframe.get_attribute("src") or ""
        if "recaptcha" in src and "api2/anchor" in src and "k=" in src:
            site_key = src.split("k=", 1)[1].split("&", 1)[0]
            break

    if not site_key:
        print("[*] No reCAPTCHA iframe found")
        return True

    print(f"[*] Solving reCAPTCHA with site key: {site_key}")
    for attempt in range(1, max_retries + 1):
        try:
            result = solver.recaptcha(sitekey=site_key, url=driver.current_url, version="v2", invisible=0)
            token = result["code"]
            driver.execute_script(
                """
                var textarea = document.getElementById('g-recaptcha-response');
                if (textarea) {
                    textarea.value = arguments[0];
                    textarea.innerHTML = arguments[0];
                    textarea.dispatchEvent(new Event('change', {bubbles:true}));
                }
                var el = document.querySelector('.g-recaptcha');
                if (el) {
                    var callback = el.getAttribute('data-callback');
                    if (callback && typeof window[callback] === 'function') {
                        window[callback](arguments[0]);
                    }
                }
                """,
                token,
            )
            print("[OK] reCAPTCHA token injected")
            time.sleep(2)
            return True
        except Exception as exc:
            print(f"[!] reCAPTCHA attempt {attempt}/{max_retries} failed: {exc}")
            if attempt < max_retries:
                time.sleep(5)
    return False


def complete_order(driver):
    print("[*] Completing order...")
    has_recaptcha = any("recaptcha" in (iframe.get_attribute("src") or "") for iframe in driver.find_elements(By.TAG_NAME, "iframe"))
    if has_recaptcha and not solve_recaptcha_v2(driver):
        if os.getenv("HEADLESS", "True").lower() == "true":
            raise RuntimeError("reCAPTCHA present but automatic solving failed in headless mode")
        input("Solve the CAPTCHA in the browser, then press ENTER here...")

    try:
        btn = driver.find_element(By.ID, "btnCompleteOrder")
    except Exception:
        btn = find_clickable_by_text(driver, ["complete order", "checkout", "order now"], timeout=15)

    safe_click(driver, btn)
    print("[OK] Complete Order clicked")
    WebDriverWait(driver, 40).until(
        lambda d: "complete" in d.current_url.lower()
        or "clientarea" in d.current_url.lower()
        or "invoice" in d.current_url.lower()
        or "order confirmation" in d.page_source.lower()
    )
    time.sleep(4)
    print(f"[*] After order URL: {driver.current_url}")


def normalize_subject(text):
    return " ".join((text or "").split()).lower()


def find_ready_email_rows(driver):
    """Return candidate email-history rows that likely contain the trial credentials.

    If VIEWTVY_READY_EMAIL_SUBJECT is set, only rows matching that subject are returned.
    Otherwise any row whose subject contains a credential keyword is treated as a candidate.
    """
    rows = driver.find_elements(By.XPATH, "//table//tbody//tr|//table//tr")
    candidates = []
    if READY_EMAIL_SUBJECT:
        target = normalize_subject(READY_EMAIL_SUBJECT)
        for row in rows:
            if target in normalize_subject(row.text):
                candidates.append(row)
        return candidates

    for row in rows:
        text = normalize_subject(row.text)
        if not text:
            continue
        if any(keyword in text for keyword in READY_EMAIL_KEYWORDS):
            candidates.append(row)
    return candidates


def open_email_row(driver, row):
    print("[*] Opening email message...")
    controls = row.find_elements(
        By.XPATH,
        ".//a[contains(., 'View Message') or contains(@href, 'viewemail')]"
        "|.//button[contains(., 'View Message')]"
        "|.//input[contains(@value, 'View Message') or contains(@onclick, 'viewemail')]",
    )
    billing_base = f"{VIEWTVY_BASE_URL}/{VIEWTVY_BILLING_PATH}"
    for control in controls:
        href = control.get_attribute("href") or ""
        onclick = control.get_attribute("onclick") or ""
        match = re.search(r"(viewemail\.php\?id=\d+)", href + " " + onclick)
        if match:
            email_url = f"{billing_base}/{match.group(1)}"
            print(f"[*] Opening email URL: {email_url}")
            load_page(driver, email_url)
            return True

        existing_windows = set(driver.window_handles)
        safe_click(driver, control)
        time.sleep(1)
        new_windows = [handle for handle in driver.window_handles if handle not in existing_windows]
        if new_windows:
            driver.switch_to.window(new_windows[0])
        return True

    safe_click(driver, row)
    return True


def read_open_email_body(driver):
    """Collect text from the current email-view page, including any message iframe."""
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    texts = [driver.find_element(By.TAG_NAME, "body").text]
    for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            driver.switch_to.frame(iframe)
            texts.append(driver.find_element(By.TAG_NAME, "body").text)
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    return "\n".join(t for t in texts if t)


def extract_credentials_from_text(text):
    normalized = text or ""
    for _ in range(3):
        unescaped = html.unescape(normalized)
        if unescaped == normalized:
            break
        normalized = unescaped

    normalized = re.sub(r"<br\s*/?>", "\n", normalized, flags=re.I)
    normalized = re.sub(r"</p\s*>", "\n", normalized, flags=re.I)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)

    username = None
    password = None
    portal_url = None

    patterns = {
        "username": [r"Username\s*:\s*([^\s\n<]+)", r"User\s*:\s*([^\s\n<]+)"],
        "password": [r"Password\s*:\s*([^\s\n<]+)", r"Pass\s*:\s*([^\s\n<]+)"],
        "portal_url": [
            r"Portal\s+URL\s*:\s*(https?://[^\s<>'\"]+)",
            r"Portal\s*:\s*(https?://[^\s<>'\"]+)",
            r"(?<!PLAYLIST )(?<!EPG )\bURL\s*:\s*(https?://[^\s<>'\"/]+(?::\d+)?)",
        ],
    }

    for pattern in patterns["username"]:
        match = re.search(pattern, normalized, re.I)
        if match:
            username = match.group(1).strip()
            break

    for pattern in patterns["password"]:
        match = re.search(pattern, normalized, re.I)
        if match:
            password = match.group(1).strip()
            break

    for pattern in patterns["portal_url"]:
        match = re.search(pattern, normalized, re.I)
        if match:
            portal_url = match.group(1).strip().rstrip(".,)")
            break

    if not portal_url:
        urls = re.findall(r"https?://[^\s<>'\"]+", normalized)
        for url in urls:
            lowered = url.lower()
            if "viewtvy.com" not in lowered and "maxcdn" not in lowered:
                portal_url = url.rstrip(".,)")
                break

    return username, password, portal_url


def extract_credentials_from_ready_email(driver):
    emails_url = f"{VIEWTVY_BASE_URL}/{VIEWTVY_BILLING_PATH}/clientarea.php?action=emails"
    deadline = time.time() + EMAIL_MAX_WAIT_SECONDS
    attempt = 0
    last_body_preview = ""

    while time.time() < deadline:
        attempt += 1
        print(f"[*] Checking ViewTVY email history (attempt {attempt})...")
        try:
            load_page(driver, emails_url)
        except Exception as exc:
            # The order already went through at this point; keep polling until the
            # deadline instead of failing the whole run on a transient load error.
            print(f"[!] Could not load email history: {exc}")
            print(f"[*] Retrying in {EMAIL_POLL_SECONDS}s...")
            time.sleep(EMAIL_POLL_SECONDS)
            continue
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        body_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r"Showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s+entries", body_text, re.I)
        if match:
            print(f"[*] Email entries visible: {match.group(1)}")

        candidates = find_ready_email_rows(driver)
        print(f"[*] {len(candidates)} candidate credential email(s) found")
        for index in range(len(candidates)):
            # Re-fetch rows each iteration: opening an email navigates away and stales handles.
            rows = find_ready_email_rows(driver)
            if index >= len(rows):
                break
            open_email_row(driver, rows[index])
            body = read_open_email_body(driver)
            username, password, portal_url = extract_credentials_from_text(body)
            if not (username and password and portal_url):
                username2, password2, portal2 = extract_credentials_from_text(driver.page_source)
                username = username or username2
                password = password or password2
                portal_url = portal_url or portal2

            if username and password and (portal_url or VIEWTVY_PORTAL_HOST):
                portal_url = (portal_url or VIEWTVY_PORTAL_HOST).rstrip("/")
                m3u_url = f"{portal_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"
                print("\n" + "=" * 60)
                print("VIEWTVY CREDENTIALS FOUND:")
                print("=" * 60)
                print(f"[*] Portal URL: {portal_url}")
                print(f"[*] Username: {username}")
                print(f"[*] Password: {password}")
                print(f"[*] M3U URL: {m3u_url}")
                print("=" * 60 + "\n")
                return portal_url, username, password, m3u_url

            last_body_preview = body[:1000]
            # Go back to the history list before trying the next candidate.
            load_page(driver, emails_url)
            WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
            time.sleep(1)

        print(f"[*] Credentials not found yet. Waiting {EMAIL_POLL_SECONDS}s before refresh...")
        time.sleep(EMAIL_POLL_SECONDS)

    if last_body_preview:
        print("[*] Last inspected email preview:")
        print(last_body_preview)
    raise TimeoutError(
        f"Credentials email not received/parsed after {EMAIL_MAX_WAIT_SECONDS} seconds"
    )


def save_to_iboplayer(username, password, hostname, max_retries=3):
    """
    Update the IBO Player playlist with the extracted Xtream credentials.

    Uses the same savePlaylist API as the other providers; current_playlist_url_id targets
    the existing playlist to update. Authenticated with a device-login bearer token
    (obtained/cached by iboplayer_auth); a 401/403 triggers a token refresh + one retry.

    Args:
        username: ViewTVY Xtream username
        password: ViewTVY Xtream password
        hostname: ViewTVY server/portal URL (e.g. http://server.example:8080)
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        bool: True if successful, False otherwise
    """
    if not VIEWTVY_IBOPLAYER_ENABLED:
        print("[*] IBO Player integration is disabled (VIEWTVY_IBOPLAYER_ENABLED=False)")
        return False

    if (
        not VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID
        or not iboplayer_auth.IBOPLAYER_MAC_ADDRESS
        or not iboplayer_auth.IBOPLAYER_DEVICE_KEY
        or not iboplayer_auth.TWOCAPTCHA_API_KEY
    ):
        print("[!] IBO Player integration enabled but missing required credentials:")
        print(f"    - IBOPLAYER_MAC_ADDRESS: {'Set' if iboplayer_auth.IBOPLAYER_MAC_ADDRESS else 'Missing'}")
        print(f"    - IBOPLAYER_DEVICE_KEY: {'Set' if iboplayer_auth.IBOPLAYER_DEVICE_KEY else 'Missing'}")
        print(f"    - TWOCAPTCHA_API_KEY: {'Set' if iboplayer_auth.TWOCAPTCHA_API_KEY else 'Missing'}")
        print(f"    - VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID: {'Set' if VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID else 'Missing'}")
        return False

    api_url = "https://iboplayer.com/frontend/device/savePlaylist"

    # Authenticate with a device-login bearer token (obtained/cached by
    # iboplayer_auth). A 401/403 below triggers a token refresh + one retry.
    try:
        headers = iboplayer_auth.authed_headers()
    except Exception as e:
        print(f"[!] Could not obtain IBO Player bearer token: {e}")
        return False

    playlist_url = hostname.rstrip("/")
    payload = {
        "current_playlist_url_id": VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID,
        "password": password,
        "pin": "",
        "playlist_name": VIEWTVY_IBOPLAYER_PLAYLIST_NAME,
        "playlist_type": "xc",  # Xtream Codes format
        "playlist_url": playlist_url,
        "protect": "false",
        "username": username,
        "xml_url": "",
    }

    print("\n" + "=" * 60)
    print("[*] Saving playlist to IBO Player...")
    print("=" * 60)
    print(f"[*] API URL: {api_url}")
    print(f"[*] Playlist Name: {VIEWTVY_IBOPLAYER_PLAYLIST_NAME}")
    print(f"[*] Playlist URL: {playlist_url}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {password}")
    print("=" * 60)

    relogin_attempted = False
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
            print(f"[*] IBO Player response status: {response.status_code}")

            if response.status_code == 200:
                # HTTP 200 alone is NOT success: IBO Player returns
                # {"status":"error"} in the body when it rejects the save
                # (e.g. the current_playlist_url_id isn't owned by this device).
                try:
                    response_data = response.json()
                except Exception:
                    response_data = None
                print(f"[*] IBO Player response: {response_data if response_data is not None else response.text[:300]}")

                if isinstance(response_data, dict) and response_data.get("status") == "success":
                    print("[OK] Playlist saved to IBO Player successfully!")
                    return True

                print("[!] IBO Player rejected the save (status != success).")
                print("[!] Most common cause: VIEWTVY_IBOPLAYER_PLAYLIST_URL_ID is not a "
                      "playlist owned by the logged-in device "
                      f"({iboplayer_auth.IBOPLAYER_MAC_ADDRESS}). Verify the device MAC/key and "
                      "playlist URL ID match the same IBO Player device.")
                return False

            if response.status_code in (401, 403):
                # Expired/invalid bearer token - refresh it once and retry.
                print(f"[!] IBO Player auth rejected ({response.status_code}): {response.text[:200]}")
                if not relogin_attempted:
                    relogin_attempted = True
                    print("[*] Refreshing bearer token and retrying...")
                    iboplayer_auth.clear_cached_token()
                    try:
                        headers = iboplayer_auth.authed_headers(force_refresh=True)
                    except Exception as e:
                        print(f"[!] Re-login failed: {e}")
                        return False
                    continue
                print("[!] Re-login already attempted - giving up.")
                return False

            if 400 <= response.status_code < 500:
                print(f"[!] IBO Player API error {response.status_code}: {response.text[:200]}")
                print("[!] This is a configuration error - please check your IBO Player credentials")
                return False

            print(f"[!] IBO Player server error {response.status_code}: {response.text[:200]}")
        except requests.RequestException as exc:
            print(f"[!] IBO Player request failed (attempt {attempt}/{max_retries}): {exc}")
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    print(f"[!] Failed to save playlist to IBO Player after {max_retries} attempts")
    return False


def send_webhook_callback(callback_url, user_id, status, username=None, password=None, host=None, m3u_url=None, error=None, max_retries=3):
    if not callback_url:
        print("[*] No callback URL provided, skipping webhook")
        return False

    payload = {
        "user_id": user_id,
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }
    if status == "success":
        payload.update({"username": username, "password": password, "host": host, "m3u_url": m3u_url})
    else:
        payload["error"] = error

    headers = {"Content-Type": "application/json", "User-Agent": "ViewTVY-Automation/1.0"}
    webhook_token = os.getenv("WEBHOOK_AUTH_TOKEN", "")
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"

    print(f"[*] Sending webhook: {json.dumps({**payload, 'password': '***' if password else None}, indent=2)}")
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
            print(f"[*] Webhook response status: {response.status_code}")
            if response.status_code in (200, 201, 202):
                print("[OK] Webhook sent successfully")
                return True
            if 400 <= response.status_code < 500:
                print(f"[!] Webhook client error: {response.text[:500]}")
                return False
        except requests.RequestException as exc:
            print(f"[!] Webhook request failed: {exc}")
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    return False


def main(user_id=None, callback_url=None):
    driver = get_driver()
    is_laravel_mode = bool(user_id and callback_url)

    print("\n[*] Starting ViewTVY automation...")
    print(f"[*] User ID: {user_id if user_id else 'N/A'}")
    print(f"[*] Callback URL: {callback_url if callback_url else 'N/A'}")
    print(f"[*] Laravel integration mode: {is_laravel_mode}")

    try:
        add_trial_to_cart(driver)
        fill_checkout_form(driver)
        complete_order(driver)
        host, username, password, m3u_url = extract_credentials_from_ready_email(driver)

        # Push the credentials into the configured IBO Player playlist (no-op if disabled).
        save_to_iboplayer(username, password, host)

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="success",
                username=username,
                password=password,
                host=host,
                m3u_url=m3u_url,
            )
        else:
            notifier.notify_success(m3u_url, username, None, host=host, password=password)

        print("[OK] ViewTVY automation complete")
    except Exception as exc:
        import traceback

        error_traceback = traceback.format_exc()
        print(f"\n[!] ViewTVY automation failed: {exc}")
        print(error_traceback)
        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="failed",
                error=f"{exc}\n\n{error_traceback}",
            )
        else:
            notifier.notify_error(str(exc), error_traceback, None)
        try:
            driver.quit()
        except Exception:
            pass
        raise SystemExit(1)
    finally:
        if AUTO_EXIT:
            try:
                driver.quit()
                print("[*] Browser closed")
            except Exception:
                pass
        else:
            print("[*] AUTO_EXIT disabled; browser will remain open")
            while True:
                time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ViewTVY - Automated Free-Trial Account Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python viewtvy_automation.py
  python viewtvy_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/viewtvy-automation
        """,
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    args = parser.parse_args()

    main(user_id=args.user_id, callback_url=args.callback_url)
