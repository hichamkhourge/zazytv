"""
LayerSeven IPTV - Automated Trial Account Creation

Automates the LayerSeven WHMCS checkout flow and extracts Xtream credentials from
client-area email history.

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
LAYERSEVEN_BASE_URL = os.getenv("LAYERSEVEN_BASE_URL", "https://layerseveniptv.io").rstrip("/")
LAYERSEVEN_CART_URL = os.getenv(
    "LAYERSEVEN_CART_URL",
    f"{LAYERSEVEN_BASE_URL}/billing/cart.php?a=confproduct&i=0",
)
LAYERSEVEN_DEVICE_TYPE = os.getenv("LAYERSEVEN_DEVICE_TYPE", "Smart TV (Samsung/Sony/LG)")
LAYERSEVEN_BOUQUET_MODE = os.getenv("LAYERSEVEN_BOUQUET_MODE", "all").lower()
LAYERSEVEN_PORTAL_HOST = os.getenv("LAYERSEVEN_PORTAL_HOST", "")
EMAIL_POLL_SECONDS = int(os.getenv("LAYERSEVEN_EMAIL_POLL_SECONDS", "10"))
EMAIL_MAX_WAIT_SECONDS = int(os.getenv("LAYERSEVEN_EMAIL_MAX_WAIT_SECONDS", "420"))
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"

READY_EMAIL_SUBJECT = "Your LayerSeven IPTV Account is Ready - Start Streaming Now!"
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY else None


def get_driver():
    options = Options()
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

    return webdriver.Chrome(service=service, options=options)


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


def select_dropdown_value(select_el, preferred_text):
    select = Select(select_el)
    preferred = preferred_text.lower()
    for option in select.options:
        text = option.text.strip().lower()
        value = (option.get_attribute("value") or "").strip().lower()
        if preferred in text or preferred in value or "smart tv" in text:
            select.select_by_visible_text(option.text)
            return option.text
    if select.options:
        select.select_by_index(1 if len(select.options) > 1 else 0)
        return select.first_selected_option.text
    return None


def dump_debug(driver, label):
    print(f"\n--- [{label}] URL: {driver.current_url} TITLE: {driver.title} ---")
    for el in driver.find_elements(By.XPATH, "//a|//button|//input[@type='submit' or @type='button']")[:80]:
        text = el.text or el.get_attribute("value") or el.get_attribute("href") or ""
        if text.strip():
            print(f"  {text.strip()[:160]}")
    print("---\n")


def click_bouquet_wizard_until_applied(driver):
    """
    LayerSeven's bouquet picker is a multi-step modal. The same #savebqbtn
    advances through content types and eventually applies the selection.
    """
    for step in range(1, 8):
        try:
            btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.ID, "savebqbtn"))
            )
        except Exception:
            print("[*] Bouquet wizard button is no longer visible")
            return

        label = (btn.get_attribute("value") or btn.text or "").strip()
        print(f"[*] Bouquet wizard step {step}: {label or 'button'}")
        safe_click(driver, btn)
        time.sleep(1.5)

        if "apply" in label.lower():
            try:
                WebDriverWait(driver, 8).until_not(
                    EC.visibility_of_element_located((By.ID, "savebqbtn"))
                )
            except Exception:
                pass
            return


def ensure_product_configuration_page(driver):
    """If WHMCS redirected to the trial category, open the actual trial product."""
    has_device_select = any(
        "smart tv" in " ".join(option.text for option in Select(select_el).options).lower()
        for select_el in driver.find_elements(By.TAG_NAME, "select")
    )
    if has_device_select:
        return

    product_links = driver.find_elements(
        By.XPATH,
        "//a[contains(@class, 'btn-order-now') or contains(., 'Order Now') or contains(@href, '/36-hours-trial')]",
    )
    for link in product_links:
        href = link.get_attribute("href") or ""
        if "36-hours-trial" in href or "trial-products" in href:
            print(f"[*] Opening trial product from category page: {href}")
            safe_click(driver, link)
            WebDriverWait(driver, 20).until(
                lambda d: "confproduct" in d.current_url
                or "36-hours-trial" in d.current_url
                or "smart tv" in d.page_source.lower()
            )
            time.sleep(2)
            print(f"[*] Product configuration URL: {driver.current_url}")
            return


def configure_product(driver):
    print(f"[*] Navigating to LayerSeven cart: {LAYERSEVEN_CART_URL}")
    driver.get(LAYERSEVEN_CART_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    print(f"[*] Cart URL: {driver.current_url}")

    ensure_product_configuration_page(driver)

    print(f"[*] Selecting device type: {LAYERSEVEN_DEVICE_TYPE}")
    selected = False
    for select_el in driver.find_elements(By.TAG_NAME, "select"):
        option_text = " ".join(option.text for option in Select(select_el).options).lower()
        name = (select_el.get_attribute("name") or "").lower()
        if "smart tv" in option_text or "device" in name:
            chosen = select_dropdown_value(select_el, LAYERSEVEN_DEVICE_TYPE)
            print(f"[OK] Device selected: {chosen}")
            selected = True
            break
    if not selected:
        print("[!] Device dropdown not found. Continuing; product may already be configured.")

    print("[*] Opening bouquet selector...")
    try:
        try:
            bouquet_button = driver.find_element(By.ID, "selectbouquetsbtn")
        except Exception:
            bouquet_button = find_clickable_by_text(driver, ["select bouquets", "bouquets"], timeout=10)
        safe_click(driver, bouquet_button)
        time.sleep(2)
    except Exception as exc:
        print(f"[!] Could not open bouquet selector: {exc}")

    print("[*] Selecting bouquets...")
    click_bouquet_wizard_until_applied(driver)

    print("[*] Continuing to checkout...")
    try:
        try:
            continue_btn = driver.find_element(By.ID, "btnCompleteProductConfig")
        except Exception:
            continue_btn = find_clickable_by_text(driver, ["continue", "checkout"], timeout=12)
        safe_click(driver, continue_btn)
    except Exception as exc:
        dump_debug(driver, "CONTINUE NOT FOUND")
        raise RuntimeError(f"Could not continue from product configuration: {exc}")

    WebDriverWait(driver, 20).until(
        lambda d: "a=view" in d.current_url
        or "a=checkout" in d.current_url
        or "checkout" in d.current_url.lower()
        or "firstname" in d.page_source.lower()
    )

    if "a=view" in driver.current_url:
        print("[*] Cart review page reached; clicking Checkout...")
        checkout_btn = find_clickable_by_text(driver, ["checkout"], timeout=12)
        safe_click(driver, checkout_btn)

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


def find_ready_email_row(driver):
    target = normalize_subject(READY_EMAIL_SUBJECT)
    rows = driver.find_elements(By.XPATH, "//table//tbody//tr|//table//tr")
    for row in rows:
        text = normalize_subject(row.text)
        if target in text:
            return row
    return None


def wait_for_ready_email(driver):
    emails_url = f"{LAYERSEVEN_BASE_URL}/billing/clientarea.php?action=emails"
    deadline = time.time() + EMAIL_MAX_WAIT_SECONDS
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        print(f"[*] Checking LayerSeven email history (attempt {attempt})...")
        driver.get(emails_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(2)

        body_text = driver.find_element(By.TAG_NAME, "body").text
        match = re.search(r"Showing\s+\d+\s+to\s+\d+\s+of\s+(\d+)\s+entries", body_text, re.I)
        if match:
            print(f"[*] Email entries visible: {match.group(1)}")

        row = find_ready_email_row(driver)
        if row:
            print("[OK] Ready email found")
            return row

        print(f"[*] Ready email not found yet. Waiting {EMAIL_POLL_SECONDS}s before refresh...")
        time.sleep(EMAIL_POLL_SECONDS)

    raise TimeoutError(f"Ready email not received after {EMAIL_MAX_WAIT_SECONDS} seconds")


def open_email_row(driver, row):
    print("[*] Opening ready email message...")
    controls = row.find_elements(
        By.XPATH,
        ".//a[contains(., 'View Message') or contains(@href, 'viewemail')]"
        "|.//button[contains(., 'View Message')]"
        "|.//input[contains(@value, 'View Message') or contains(@onclick, 'viewemail')]",
    )
    for control in controls:
        href = control.get_attribute("href") or ""
        onclick = control.get_attribute("onclick") or ""
        match = re.search(r"(viewemail\.php\?id=\d+)", href + " " + onclick)
        if match:
            email_url = f"{LAYERSEVEN_BASE_URL}/billing/{match.group(1)}"
            print(f"[*] Opening email URL: {email_url}")
            driver.get(email_url)
            return

        existing_windows = set(driver.window_handles)
        safe_click(driver, control)
        time.sleep(1)
        new_windows = [handle for handle in driver.window_handles if handle not in existing_windows]
        if new_windows:
            driver.switch_to.window(new_windows[0])
        return

    safe_click(driver, row)


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
        "portal_url": [r"Portal\s+URL\s*:\s*(https?://[^\s<>'\"]+)", r"Portal\s*:\s*(https?://[^\s<>'\"]+)"],
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
            if "layerseveniptv.io" not in lowered and "maxcdn" not in lowered:
                portal_url = url.rstrip(".,)")
                break

    return username, password, portal_url


def extract_credentials_from_ready_email(driver):
    row = wait_for_ready_email(driver)
    open_email_row(driver, row)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)

    body_text = driver.find_element(By.TAG_NAME, "body").text
    username, password, portal_url = extract_credentials_from_text(body_text)

    if not (username and password and portal_url):
        print("[!] Direct text extraction incomplete; searching page source")
        username2, password2, portal2 = extract_credentials_from_text(driver.page_source)
        username = username or username2
        password = password or password2
        portal_url = portal_url or portal2

    if LAYERSEVEN_PORTAL_HOST and not portal_url:
        portal_url = LAYERSEVEN_PORTAL_HOST

    if not username or not password or not portal_url:
        print("[*] Email text preview:")
        print(body_text[:1000])
        raise RuntimeError("Could not extract username, password, and portal URL from ready email")

    portal_url = portal_url.rstrip("/")
    m3u_url = f"{portal_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

    print("\n" + "=" * 60)
    print("LAYERSEVEN CREDENTIALS FOUND:")
    print("=" * 60)
    print(f"[*] Portal URL: {portal_url}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {password}")
    print(f"[*] M3U URL: {m3u_url}")
    print("=" * 60 + "\n")

    return portal_url, username, password, m3u_url


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

    headers = {"Content-Type": "application/json", "User-Agent": "LayerSeven-Automation/1.0"}
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

    print("\n[*] Starting LayerSeven automation...")
    print(f"[*] User ID: {user_id if user_id else 'N/A'}")
    print(f"[*] Callback URL: {callback_url if callback_url else 'N/A'}")
    print(f"[*] Laravel integration mode: {is_laravel_mode}")

    try:
        configure_product(driver)
        fill_checkout_form(driver)
        complete_order(driver)
        host, username, password, m3u_url = extract_credentials_from_ready_email(driver)

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
            notifier.notify_success(m3u_url, username, None)

        print("[OK] LayerSeven automation complete")
    except Exception as exc:
        import traceback

        error_traceback = traceback.format_exc()
        print(f"\n[!] LayerSeven automation failed: {exc}")
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
        description="LayerSeven IPTV - Automated Account Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python layerseven_automation.py
  python layerseven_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/layerseven-automation
        """,
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    args = parser.parse_args()
    main(user_id=args.user_id, callback_url=args.callback_url)

