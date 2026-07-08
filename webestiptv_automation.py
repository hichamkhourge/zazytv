"""
WEBESTIPTV - Automated Account Registration

Fills and submits the account-creation form at
https://webestiptv.com/portal/register/ using Selenium + undetected-chromedriver.

Registration is a two-step flow: submitting the form creates the account and redirects
to an OTP verification step (?step=verify) while emailing a 6-digit code. This script
completes both steps: it polls the temp inbox for the code and submits it on the verify
form (in the same browser session), landing on the dashboard.

Design (matches the rest of this repo's automations):
  * Random user data via generate_random_user_data() (reused from iptvvcanada).
  * A receiving inbox via the temp-mail backend already built into the codebase
    (defaults to procmail, with an automatic fallback to mail.tm) for the OTP code.
  * Source IP rotated each run via free public proxies (free, no signup): a proxy
    list is fetched from a public API (ProxyScrape by default), each candidate is
    validated against api.ipify.org, and the browser egresses through a working one.
    A new proxy/IP is used per registration attempt.

Config (env vars, all optional):
  * WEBEST_USE_PROXY=True|False     - route through a free proxy (default True).
  * WEBEST_PROXY_PROTOCOL=http      - http | socks4 | socks5.
  * WEBEST_PROXY_LIST_URL=<url>     - proxy-list API ({proto} placeholder substituted).
  * WEBEST_PROXY_MAX_TRIES=30       - candidate proxies to test per attempt.
  * WEBEST_EMAIL_BACKEND=procmail   - procmail | mailtm (sets the preferred provider;
    the other is used as an automatic fallback). procmail reuses PROCMAIL_API_BASE.
  * Needs PySocks (already in requirements) only when PROXY_PROTOCOL is socks4/socks5.

Run (headed, first test):
  HEADLESS=False WEBEST_USE_PROXY=True python webestiptv_automation.py

Set WEBEST_USE_PROXY=False to register on the host's direct connection.
"""
import os
import random
import re
import string
import time

from dotenv import load_dotenv

load_dotenv()

# The temp-mail backend is selected in iptvvcanada at import time from IPTVV_EMAIL_BACKEND.
# This script wants a disposable inbox, so set its own WEBEST_EMAIL_BACKEND *before* importing
# the module. Default is "procmail" (api.procmail.xyz) — no domain/account round-trips — with
# an automatic fallback to mail.tm if procmail is unavailable (see create_inbox()).
os.environ["IPTVV_EMAIL_BACKEND"] = os.getenv("WEBEST_EMAIL_BACKEND", "procmail")

import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Reuse the battle-tested helpers from the IPTVV automation rather than re-implementing.
# These exist on every branch.
from iptvvcanada_automation import (
    create_mailtm_account,
    create_procmail_inbox,
    find_clickable_by_text,
    generate_random_user_data,
    get_mailtm_message_by_id,
    get_mailtm_messages,
    get_procmail_messages,
    get_random_user_agent,
    safe_click,
    save_page_debug_artifacts,
)

# Telegram notifier (configured via TELEGRAM_* in .env). Fall back to a no-op so the script
# still runs if the module/deps are unavailable.
try:
    from telegram_notifier import notifier
except Exception:
    class _DummyNotifier:
        enabled = False

        def send_notification(self, *args, **kwargs):
            return False

    notifier = _DummyNotifier()


def _new_procmail_session():
    """Allocate a procmail inbox. Returns a session dict or None."""
    address = create_procmail_inbox()
    if not address:
        return None
    return {"backend": "procmail", "address": address}


def _new_mailtm_session():
    """Allocate a mail.tm inbox. Returns a session dict or None."""
    address, password, token = create_mailtm_account()
    if not address:
        return None
    return {"backend": "mailtm", "address": address, "password": password, "token": token}


def create_inbox():
    """Allocate a disposable inbox with an automatic provider fallback.

    Default order is procmail then mail.tm; setting WEBEST_EMAIL_BACKEND=mailtm reverses it.
    Returns a session dict carrying 'backend' and 'address' (mail.tm also carries 'token'),
    or None only if every provider fails.
    """
    prefer_mailtm = os.getenv("WEBEST_EMAIL_BACKEND", "procmail").strip().lower() == "mailtm"
    if prefer_mailtm:
        providers = (("mail.tm", _new_mailtm_session), ("procmail", _new_procmail_session))
    else:
        providers = (("procmail", _new_procmail_session), ("mail.tm", _new_mailtm_session))

    for idx, (name, factory) in enumerate(providers):
        if idx:
            print(f"[*] Falling back to {name} for the receiving inbox...")
        session = factory()
        if session:
            print(f"[OK] Receiving inbox ready via {name}: {session['address']}")
            return session
        print(f"[!] {name} inbox creation failed.")
    return None

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════
WEBEST_BASE_URL = os.getenv("WEBEST_BASE_URL", "https://webestiptv.com").rstrip("/")
WEBEST_REGISTER_URL = os.getenv(
    "WEBEST_REGISTER_URL", f"{WEBEST_BASE_URL}/portal/register/"
)
WEBEST_LOGIN_URL = os.getenv("WEBEST_LOGIN_URL", f"{WEBEST_BASE_URL}/portal/login")
WEBEST_CREDENTIALS_URL = os.getenv(
    "WEBEST_CREDENTIALS_URL", f"{WEBEST_BASE_URL}/portal/credentials/"
)

HEADLESS = os.getenv("HEADLESS", "True").lower() == "true"
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"
PAGE_LOAD_RETRIES = int(os.getenv("WEBEST_PAGE_LOAD_RETRIES", "3"))

# Free public-proxy IP rotation.
WEBEST_USE_PROXY = os.getenv("WEBEST_USE_PROXY", "True").lower() == "true"
# Protocol of the proxies to fetch/use: http, socks4 or socks5.
PROXY_PROTOCOL = os.getenv("WEBEST_PROXY_PROTOCOL", "http").lower()
# Public proxy-list API (ProxyScrape by default). {proto} is filled with PROXY_PROTOCOL.
PROXY_LIST_URL = os.getenv(
    "WEBEST_PROXY_LIST_URL",
    "https://api.proxyscrape.com/v2/?request=getproxies"
    "&protocol={proto}&timeout=10000&country=all&ssl=all&anonymity=all",
)
# Seconds to allow when validating a candidate proxy.
PROXY_VALIDATE_TIMEOUT = int(os.getenv("WEBEST_PROXY_VALIDATE_TIMEOUT", "8"))
# Max candidate proxies to test before giving up on finding a working one.
PROXY_MAX_TRIES = int(os.getenv("WEBEST_PROXY_MAX_TRIES", "60"))
# Require a stable exit IP across two requests. Proxies that rotate the exit IP per
# request break the WordPress nonce/session (-> "The link you followed has expired").
WEBEST_PROXY_STICKY = os.getenv("WEBEST_PROXY_STICKY", "True").lower() == "true"
# Require the live register page to be reachable through the proxy (proves the browser
# load will work, not just an ipify ping).
WEBEST_PROXY_SITE_CHECK = os.getenv("WEBEST_PROXY_SITE_CHECK", "True").lower() == "true"
# How many register attempts (each with a fresh proxy/IP) before giving up on a block.
WEBEST_MAX_ATTEMPTS = int(os.getenv("WEBEST_MAX_ATTEMPTS", "5"))

# OTP email verification: how long to wait for the code, and how often to poll.
# OTPs arrive fast and expire, so the default wait is short.
WEBEST_OTP_WAIT = int(os.getenv("WEBEST_OTP_WAIT", "180"))
OTP_POLL_SECONDS = int(os.getenv("WEBEST_OTP_POLL_SECONDS", "10"))

# After verification lands on the dashboard, click the "Request Trial" button.
WEBEST_REQUEST_TRIAL = os.getenv("WEBEST_REQUEST_TRIAL", "True").lower() == "true"

# After the trial, open /portal/credentials/ and extract the IPTV access details.
WEBEST_FETCH_CREDENTIALS = os.getenv("WEBEST_FETCH_CREDENTIALS", "True").lower() == "true"
# Trial provisioning is async: the credentials page shows "Pending Activation" until the
# line is created. Poll the page (and the inbox) until the access details appear.
WEBEST_CREDENTIALS_WAIT = int(os.getenv("WEBEST_CREDENTIALS_WAIT", "120"))
CREDENTIALS_POLL_SECONDS = int(os.getenv("WEBEST_CREDENTIALS_POLL_SECONDS", "15"))

# Push the extracted Xtream credentials into IBO Player (reuses iptvv's save_to_iboplayer).
WEBEST_IBOPLAYER_ENABLED = os.getenv("WEBEST_IBOPLAYER_ENABLED", "True").lower() == "true"
# Shared IBO Player session cookie (falls back to the repo-wide IBOPLAYER_COOKIE).
WEBEST_IBOPLAYER_COOKIE = os.getenv("WEBEST_IBOPLAYER_COOKIE") or os.getenv("IBOPLAYER_COOKIE", "")
WEBEST_IBOPLAYER_PLAYLIST_URL_ID = os.getenv(
    "WEBEST_IBOPLAYER_PLAYLIST_URL_ID", "6a2fdb66d1bd9a61f3b466f1"
)
WEBEST_IBOPLAYER_PLAYLIST_NAME = os.getenv("WEBEST_IBOPLAYER_PLAYLIST_NAME", "WEBESTIPTV")


# ═══════════════════════════════════════════════════════════
# Free public-proxy rotation
# ═══════════════════════════════════════════════════════════
def fetch_free_proxies():
    """Fetch a list of free public proxies ('host:port') from the configured API."""
    import requests

    url = PROXY_LIST_URL.format(proto=PROXY_PROTOCOL)
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
    except Exception as exc:
        print(f"[!] Failed to fetch proxy list: {exc}")
        return []

    proxies = []
    for line in resp.text.splitlines():
        line = line.strip()
        # Accept "host:port" and tolerate an embedded scheme.
        if "://" in line:
            line = line.split("://", 1)[1]
        if re.match(r"^\d{1,3}(\.\d{1,3}){3}:\d{2,5}$", line):
            proxies.append(line)

    random.shuffle(proxies)
    print(f"[*] Fetched {len(proxies)} candidate {PROXY_PROTOCOL} proxies")
    return proxies


def _requests_proxy_dict(proxy):
    """requests-style proxy dict for a 'host:port' using PROXY_PROTOCOL."""
    scheme = "socks5h" if PROXY_PROTOCOL in ("socks5", "socks4") else "http"
    if PROXY_PROTOCOL == "socks4":
        scheme = "socks4"
    url = f"{scheme}://{proxy}"
    return {"http": url, "https": url}


def validate_proxy(proxy):
    """Return the exit IP if the proxy is usable for the WP register flow, else None.

    All checks must pass: HTTPS works; the exit IP is stable across two requests (a
    rotating exit IP breaks the WordPress nonce -> "link has expired"); and the live
    register page is reachable through the proxy and still contains the form (proves a
    real browser load will work, not just an ipify ping).
    """
    import requests

    proxies = _requests_proxy_dict(proxy)
    try:
        ip1 = requests.get(
            "https://api.ipify.org", proxies=proxies, timeout=PROXY_VALIDATE_TIMEOUT
        ).text.strip()
        if not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", ip1):
            return None

        if WEBEST_PROXY_STICKY:
            ip2 = requests.get(
                "https://api.ipify.org", proxies=proxies, timeout=PROXY_VALIDATE_TIMEOUT
            ).text.strip()
            if ip2 != ip1:
                return None  # per-request rotation would break the nonce/session

        if WEBEST_PROXY_SITE_CHECK:
            resp = requests.get(
                WEBEST_REGISTER_URL,
                proxies=proxies,
                timeout=PROXY_VALIDATE_TIMEOUT,
                headers={"User-Agent": get_random_user_agent()},
            )
            if resp.status_code != 200 or 'name="first_name"' not in resp.text:
                return None

        return ip1
    except Exception:
        return None


def pick_working_proxy():
    """Fetch + validate proxies until one works. Returns (proxy, exit_ip) or (None, None)."""
    candidates = fetch_free_proxies()
    tried = 0
    for proxy in candidates:
        if tried >= PROXY_MAX_TRIES:
            break
        tried += 1
        ip = validate_proxy(proxy)
        if ip:
            print(f"[OK] Working proxy {proxy} (exit IP {ip}) after testing {tried}")
            return proxy, ip
    print(f"[!] No working proxy found after testing {tried} candidate(s)")
    return None, None


# ═══════════════════════════════════════════════════════════
# Browser
# ═══════════════════════════════════════════════════════════
def _detect_chrome():
    """Locate a Chrome/Chromium binary and its major version.

    Multiple browsers may be installed (e.g. Google Chrome + snap Chromium); pinning
    one binary + its major version keeps undetected-chromedriver from pairing a driver
    with the wrong browser. Override the binary with WEBEST_CHROME_BINARY.
    """
    import shutil
    import subprocess

    binary = os.getenv("WEBEST_CHROME_BINARY")
    if not binary:
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            binary = shutil.which(name)
            if binary:
                break
    if not binary:
        return None, None

    major = None
    try:
        out = subprocess.check_output([binary, "--version"], text=True, timeout=10)
        m = re.search(r"(\d+)\.\d+\.\d+", out)
        if m:
            major = int(m.group(1))
    except Exception as exc:
        print(f"[!] Could not read Chrome version from {binary}: {exc}")
    return binary, major


def build_proxy_driver(proxy):
    """undetected-chromedriver routed through a free public proxy ('host:port').

    Mirrors the repo's standard anti-detection flags. Free public proxies are
    unauthenticated, so we pass them with --proxy-server (no auth extension needed).
    When proxy is None the browser uses a direct connection.
    """
    options = uc.ChromeOptions()

    if HEADLESS:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        print("[*] Running in HEADLESS mode")
    else:
        options.add_argument("--start-maximized")
        print("[*] Running in GUI mode")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    random_ua = get_random_user_agent()
    options.add_argument(f"--user-agent={random_ua}")
    print(f"[*] Using User-Agent: {random_ua[:80]}...")

    # Anti-detection (same set the repo's get_driver() uses).
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-notifications")

    if proxy:
        scheme = PROXY_PROTOCOL if PROXY_PROTOCOL in ("socks5", "socks4") else "http"
        options.add_argument(f"--proxy-server={scheme}://{proxy}")
        print(f"[*] Browser egress via free proxy: {scheme}://{proxy}")
    else:
        options.add_argument("--no-proxy-server")
        print("[*] Using direct connection (no proxy)")

    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
    }
    options.add_experimental_option("prefs", prefs)

    # Pin the browser binary + major version so uc fetches a matching ChromeDriver
    # (avoids "only supports Chrome version N" mismatches when several browsers exist).
    binary, major = _detect_chrome()
    if binary:
        options.binary_location = binary
        print(f"[*] Chrome binary: {binary}" + (f" (v{major})" if major else ""))

    try:
        print("[*] Initializing undetected-chromedriver...")
        uc_kwargs = {"options": options, "use_subprocess": False}
        if major:
            uc_kwargs["version_main"] = major
        driver = uc.Chrome(**uc_kwargs)
        print("[OK] undetected-chromedriver initialized successfully")
    except Exception as exc:
        print(f"[!] undetected-chromedriver failed: {exc}; falling back to ChromeDriver")
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
        service = (
            Service(chromedriver_path)
            if os.path.exists(chromedriver_path)
            else Service(ChromeDriverManager().install())
        )
        regular_options = Options()
        for arg in options.arguments:
            regular_options.add_argument(arg)
        driver = webdriver.Chrome(service=service, options=regular_options)

    driver._proxy_ext_dir = None
    return driver


class ProxyDeadError(Exception):
    """Raised when the browser can't load a page because the proxy failed."""


# Chrome interstitial markers that mean the proxy (not the site) failed.
_PROXY_ERROR_MARKERS = [
    "err_proxy_connection_failed", "err_tunnel_connection_failed", "err_connection",
    "err_empty_response", "err_timed_out", "err_name_not_resolved",
    "err_address_unreachable", "err_socks_connection", "this site can",
    "can’t be reached", "can't be reached", "no internet",
]


def _page_is_proxy_error(driver):
    """True if the current page is a Chrome proxy/connection error interstitial."""
    try:
        blob = ((driver.title or "") + " " + (driver.page_source or "")).lower()
    except Exception:
        return False
    return any(marker in blob for marker in _PROXY_ERROR_MARKERS)


def _cache_bust(url):
    """Append a unique query param so LiteSpeed/CDN serves a fresh (uncached) page.

    The register page is LiteSpeed-cached and otherwise hands out a frozen, stale
    _wpnonce -> the POST then fails with "The link you followed has expired". A query
    string bypasses the cache and yields a valid, current nonce.
    """
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}_cb={random.randint(10**6, 10**9)}"


def load_page(driver, url, retries=PAGE_LOAD_RETRIES):
    """Navigate to url with a bounded page-load and retries (mirrors iptvtune)."""
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


# ═══════════════════════════════════════════════════════════
# Form filling
# ═══════════════════════════════════════════════════════════
def generate_password(length=14):
    """Strong password satisfying the form's minlength=8 (mixed classes)."""
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    while True:
        pwd = "".join(random.choices(alphabet, k=length))
        if (any(c.islower() for c in pwd) and any(c.isupper() for c in pwd)
                and any(c.isdigit() for c in pwd) and any(c in "!@#$%&*" for c in pwd)):
            return pwd


def _type_field(driver, name, value):
    """Fill an input by name with a native send_keys, JS fallback on failure."""
    try:
        field = driver.find_element(By.NAME, name)
    except Exception:
        print(f"[!] Field '{name}' not found")
        return False

    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
        time.sleep(0.2)
        field.clear()
        field.send_keys(value)
    except Exception:
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            field,
            value,
        )
    shown = value if name != "password" else "*" * len(value)
    print(f"[OK] Filled {name}: {shown}")
    return True


def fill_register_form(driver, user_data, email, password):
    """Load the register page, fill all fields, submit, and return the post-submit URL."""
    # Cache-bust: the register page is LiteSpeed-cached and serves a stale _wpnonce.
    load_page(driver, _cache_bust(WEBEST_REGISTER_URL))

    # Fast-fail on a dead proxy instead of waiting out the 25s element timeout.
    if _page_is_proxy_error(driver):
        raise ProxyDeadError("proxy failed to load the register page")
    try:
        WebDriverWait(driver, 15).until(
            EC.presence_of_element_located((By.NAME, "first_name"))
        )
    except Exception:
        if _page_is_proxy_error(driver):
            raise ProxyDeadError("proxy failed to load the register page")
        raise
    print(f"[*] Register page loaded: {driver.current_url}")
    time.sleep(1)

    _type_field(driver, "first_name", user_data["first_name"])
    _type_field(driver, "last_name", user_data["last_name"])
    _type_field(driver, "phone", user_data["phone"])
    _type_field(driver, "email", email)
    _type_field(driver, "password", password)

    # Submit: the button has no id/name, target by its visible text.
    try:
        btn = driver.find_element(
            By.XPATH, "//button[contains(normalize-space(.), 'Create Account')]"
        )
    except Exception:
        btn = driver.find_element(By.CSS_SELECTOR, "form.auth-form button.btn-primary")

    print("[*] Submitting registration form...")
    before_url = driver.current_url
    safe_click(driver, btn)

    # Let the POST + any redirect settle (wait for navigation away from the loaded URL,
    # which carries the ?_cb cache-buster, or for a result banner to appear).
    try:
        WebDriverWait(driver, 20).until(
            lambda d: d.current_url != before_url or _page_has_message(d)
        )
    except Exception:
        pass
    time.sleep(2)
    return driver.current_url


def _page_has_message(driver):
    """True if a success/error banner appears (so we stop waiting on a same-URL response)."""
    try:
        body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        return False
    markers = [
        "already", "exist", "invalid", "error", "success", "verify",
        "verification", "welcome", "created", "disposable", "not allowed",
    ]
    return any(m in body for m in markers)


def classify_result(driver):
    """Inspect the current page. Returns (status, detail).

    status is one of:
      'needs_otp' - landed on the email-verification step (OTP code required)
      'success'   - account active (left /portal/register for dashboard/login, or success text)
      'failure'   - inline error, or still sitting on the registration form
    """
    url = (driver.current_url or "")
    url_l = url.lower()
    try:
        body = (driver.find_element(By.TAG_NAME, "body").text or "")
    except Exception:
        body = ""
    body_l = body.lower()

    # OTP verification step (same /portal/register path, so detect it explicitly).
    on_verify = (
        "step=verify" in url_l
        or bool(driver.find_elements(By.NAME, "otp_code"))
        or "verify email" in body_l
    )
    if on_verify:
        return "needs_otp", f"verification required: {url}"

    error_markers = [
        "already", "exists", "invalid", "incorrect", "disposable", "not allowed",
        "blocked", "failed", "try again",
    ]
    hit_error = next((m for m in error_markers if m in body_l), None)
    if hit_error:
        snippet = body.strip().replace("\n", " ")[:200]
        return "failure", f"page reported an error ('{hit_error}'): {snippet}"

    # Success: navigated away from the registration flow, or an explicit success marker.
    moved_off = "/portal/register" not in url_l
    success_markers = ["dashboard", "welcome", "verified", "success", "logged in"]
    hit_success = next((m for m in success_markers if m in body_l), None)
    if moved_off or hit_success:
        return "success", (f"reached {url}" if moved_off else f"success marker '{hit_success}'")

    snippet = body.strip().replace("\n", " ")[:200]
    return "failure", f"no clear success signal; still on register page: {snippet}"


# ═══════════════════════════════════════════════════════════
# OTP email verification
# ═══════════════════════════════════════════════════════════
def extract_otp(text):
    """Pull a 6-digit verification code out of email text, most-specific pattern first."""
    if not text:
        return None
    patterns = [
        r"verification code is:?\s*([0-9]{6})",
        r"code[:\s]+([0-9]{6})",
        r"\b([0-9]{6})\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1)
    return None


def _inbox_messages(session):
    """Return inbox messages (each a dict with subject/text/html), branch/backend-agnostic."""
    backend = session.get("backend")
    if backend == "procmail":
        # procmail returns messages with bodies inline; no per-id fetch needed.
        return get_procmail_messages(session["address"])

    # mail.tm: list endpoint returns summaries; fetch each body by id.
    token = session["token"]
    full = []
    for summary in get_mailtm_messages(token):
        body = get_mailtm_message_by_id(token, summary["id"]) or {}
        merged = dict(summary)
        for key in ("text", "html"):
            if body.get(key) is not None:
                merged[key] = body[key]
        full.append(merged)
    return full


def _message_text(msg):
    """Flatten a message's subject/intro/text/html into one searchable string."""
    parts = [msg.get("subject") or "", msg.get("intro") or "", msg.get("text") or ""]
    html_part = msg.get("html")
    if isinstance(html_part, list):
        html_part = " ".join(html_part)
    if html_part:
        parts.append(re.sub(r"<[^>]+>", " ", html_part))
    return " ".join(parts)


def wait_for_otp_code(session, max_wait_seconds=WEBEST_OTP_WAIT):
    """Poll the inbox until the OTP code arrives. Returns the code string or None."""
    print(f"[*] Waiting for OTP email (max {max_wait_seconds}s)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking inbox for OTP (attempt {attempt}, {remaining}s remaining)...")
        try:
            messages = _inbox_messages(session)
        except Exception as exc:
            print(f"[!] Inbox check failed: {exc}")
            messages = []

        for msg in messages:
            code = extract_otp(_message_text(msg))
            if code:
                print(f"[OK] OTP {code} found in email: {msg.get('subject', '')}")
                return code

        if messages:
            print(f"[*] {len(messages)} email(s) so far, no OTP code yet")
        time.sleep(OTP_POLL_SECONDS)

    print(f"[!] No OTP email within {max_wait_seconds}s")
    return None


def submit_otp(driver, code):
    """Fill otp_code on the already-loaded verify page and submit. Returns post-verify URL."""
    WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.NAME, "otp_code"))
    )
    _type_field(driver, "otp_code", code)

    try:
        btn = driver.find_element(
            By.XPATH, "//button[contains(normalize-space(.), 'Verify')]"
        )
    except Exception:
        btn = driver.find_element(By.CSS_SELECTOR, "form.auth-form button.btn-primary")

    print("[*] Submitting OTP verification code...")
    safe_click(driver, btn)

    # Success leaves the verify step; failure reloads it with an error.
    try:
        WebDriverWait(driver, 20).until(
            lambda d: "step=verify" not in (d.current_url or "").lower()
        )
    except Exception:
        pass
    time.sleep(2)
    return driver.current_url


# ═══════════════════════════════════════════════════════════
# Dashboard: request trial
# ═══════════════════════════════════════════════════════════
# Candidate labels for the trial button ("Request Trial" expected; synonyms as fallback).
# Deliberately excludes the paid order-flow buttons ("Subscribe Now", "Complete Secure Order").
TRIAL_BUTTON_TERMS = [
    "request trial", "request a trial", "free trial", "start trial",
    "claim trial", "get trial", "try free", "activate trial",
]
TRIAL_SUCCESS_MARKERS = [
    "trial", "active", "expires", "username", "password", "m3u",
    "playlist", "line created", "your line", "credentials",
]


def request_trial(driver):
    """On the dashboard, find + click the trial button and capture the result.

    Non-fatal: returns (clicked: bool, detail: str). Saves an HTML+screenshot snapshot to
    logs/ either way so the outcome (and the real dashboard markup) is inspectable.
    """
    try:
        WebDriverWait(driver, 20).until(
            EC.presence_of_element_located((By.TAG_NAME, "body"))
        )
    except Exception:
        pass
    time.sleep(2)  # let the dashboard finish rendering

    try:
        btn = find_clickable_by_text(driver, TRIAL_BUTTON_TERMS, timeout=20)
    except Exception:
        save_page_debug_artifacts(driver, "webest_dashboard")
        print("[!] No trial button found on the dashboard (saved snapshot to logs/)")
        return False, "trial button not found"

    label = (btn.text or btn.get_attribute("value") or "").strip()
    print(f"[*] Clicking trial button: '{label}'")
    safe_click(driver, btn)
    time.sleep(3)  # trial activates instantly per the flow

    artifacts = save_page_debug_artifacts(driver, "webest_dashboard_after_trial")
    try:
        body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
    except Exception:
        body = ""
    hit = next((m for m in TRIAL_SUCCESS_MARKERS if m in body), None)
    if hit:
        print(f"[OK] Trial appears active (marker '{hit}'); snapshot: {artifacts.get('html')}")
        return True, f"activated (marker '{hit}')"
    print(f"[OK] Trial button clicked; snapshot saved: {artifacts.get('html')}")
    return True, "clicked (verify snapshot)"


# ═══════════════════════════════════════════════════════════
# Credentials: open access + extract IPTV access details
# ═══════════════════════════════════════════════════════════
# Noise lines on the credentials panel that are not values.
_CRED_NOISE = {"copy", "copy all", "email me", "download pdf", ""}
# Label -> the synonyms we match (lower-cased, exact line) to find each field's value.
_CRED_LABELS = {
    "username": ["username", "user name", "user"],
    "password": ["password", "pass"],
    "host": ["dns / host url", "dns/host url", "host url", "dns / host", "host", "dns"],
    "samsung_dns": ["samsung / lg dns", "samsung/lg dns", "samsung / lg", "samsung", "lg dns"],
    "m3u_url": ["m3u playlist", "m3u", "playlist url", "m3u url"],
}


def _value_after_label(lines, label_opts):
    """Return the first non-noise line following a line that equals/starts a label."""
    for i, line in enumerate(lines):
        low = line.lower().rstrip(":").strip()
        if any(low == opt or low.startswith(opt) for opt in label_opts):
            for nxt in lines[i + 1:]:
                if nxt.lower().strip() not in _CRED_NOISE:
                    return nxt.strip()
    return None


def extract_iptv_credentials(driver):
    """Parse the credentials panel into {username, password, host, samsung_dns, m3u_url}.

    Each field is extracted independently from the page (label-based text + data-copy
    attributes), with M3U-URL parsing only as a last-resort fallback for missing pieces.
    """
    try:
        body = driver.find_element(By.TAG_NAME, "body").text or ""
    except Exception:
        body = ""
    lines = [l.strip() for l in body.splitlines() if l.strip()]

    creds = {key: _value_after_label(lines, opts) for key, opts in _CRED_LABELS.items()}

    # Secondary source: the Copy buttons carry the raw value in a data-copy attribute.
    copy_values = []
    try:
        for el in driver.find_elements(By.CSS_SELECTOR, "[data-copy]"):
            val = (el.get_attribute("data-copy") or "").strip()
            if val:
                copy_values.append(val)
    except Exception:
        pass

    # Cross-source / regex fallbacks.
    blob = body + "\n" + "\n".join(copy_values)
    if not creds.get("m3u_url"):
        m = re.search(r"https?://\S+get\.php\?\S+", blob)
        creds["m3u_url"] = m.group(0) if m else None
    if not creds.get("samsung_dns"):
        m = re.search(r"https?://\S*smartdns\S*", blob)
        creds["samsung_dns"] = m.group(0) if m else None

    # Last resort: derive username/password/host from the M3U URL itself.
    if creds.get("m3u_url"):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(creds["m3u_url"])
        qs = parse_qs(parsed.query)
        if not creds.get("username") and qs.get("username"):
            creds["username"] = qs["username"][0]
        if not creds.get("password") and qs.get("password"):
            creds["password"] = qs["password"][0]
        if not creds.get("host"):
            creds["host"] = f"{parsed.scheme}://{parsed.netloc}"

    return creds


def parse_credentials_from_text(text):
    """Regex-extract IPTV creds from free text (e.g. the credentials email body)."""
    creds = {"username": None, "password": None, "host": None,
             "samsung_dns": None, "m3u_url": None}
    if not text:
        return creds

    m = re.search(r"https?://[^\s\"'<>]+get\.php\?[^\s\"'<>]+", text)
    if m:
        creds["m3u_url"] = m.group(0)
    m = re.search(r"https?://[^\s\"'<>]*smartdns[^\s\"'<>]*", text)
    if m:
        creds["samsung_dns"] = m.group(0)
    m = re.search(r"user\s*name\s*[:=]\s*([^\s<\"']+)", text, re.I)
    if m:
        creds["username"] = m.group(1)
    m = re.search(r"pass\s*word\s*[:=]\s*([^\s<\"']+)", text, re.I)
    if m:
        creds["password"] = m.group(1)

    if creds["m3u_url"]:
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(creds["m3u_url"])
        qs = parse_qs(parsed.query)
        creds["username"] = creds["username"] or (qs.get("username", [None])[0])
        creds["password"] = creds["password"] or (qs.get("password", [None])[0])
        creds["host"] = creds["host"] or f"{parsed.scheme}://{parsed.netloc}"
    return creds


def _creds_complete(creds):
    """True once we have enough to use the line (an M3U URL, or username+password)."""
    if not creds:
        return False
    return bool(creds.get("m3u_url") or (creds.get("username") and creds.get("password")))


def _creds_from_email(email_session):
    """Scan the temp inbox for the credentials email and parse it. Returns creds or None."""
    try:
        messages = _inbox_messages(email_session)
    except Exception:
        return None
    for msg in messages:
        creds = parse_credentials_from_text(_message_text(msg))
        if _creds_complete(creds):
            print(f"[OK] Credentials found in email: {msg.get('subject', '')}")
            return creds
    return None


def open_and_extract_credentials(driver, email_session=None):
    """Poll the credentials page (and inbox) until the trial line is provisioned.

    Trial activation is asynchronous — the page shows "Pending Activation" until the line
    is created. We reload the page (cache-busted) and click "Open Access" each round,
    extracting as soon as the access details appear; the credentials email is a fallback.
    Non-fatal: returns the creds dict (values may be None on timeout).
    """
    print(f"[*] Opening credentials page: {WEBEST_CREDENTIALS_URL}")
    deadline = time.time() + WEBEST_CREDENTIALS_WAIT
    attempt = 0
    creds = {}

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        try:
            load_page(driver, _cache_bust(WEBEST_CREDENTIALS_URL))
        except Exception as exc:
            print(f"[!] Could not load credentials page: {exc}")
            break

        # Reveal the details if there's an "Open Access" control.
        try:
            btn = find_clickable_by_text(
                driver, ["open access", "open", "reveal", "show access", "view access"],
                timeout=5,
            )
            print(f"[*] Clicking '{(btn.text or '').strip()}' to reveal credentials...")
            safe_click(driver, btn)
            time.sleep(2)
        except Exception:
            pass

        creds = extract_iptv_credentials(driver)
        if _creds_complete(creds):
            save_page_debug_artifacts(driver, "webest_credentials")
            break

        # Fallback: the same credentials are emailed once the line is active.
        if email_session:
            email_creds = _creds_from_email(email_session)
            if _creds_complete(email_creds):
                creds = email_creds
                break

        body = ""
        try:
            body = (driver.find_element(By.TAG_NAME, "body").text or "").lower()
        except Exception:
            pass
        awaiting = "pending" in body or "awaiting" in body
        state = "awaiting activation" if awaiting else "no details yet"
        print(f"[*] Credentials not ready ({state}); attempt {attempt}, {remaining}s left")
        save_page_debug_artifacts(driver, "webest_credentials")
        time.sleep(CREDENTIALS_POLL_SECONDS)

    print("[*] IPTV credentials:")
    for key in ("username", "password", "host", "samsung_dns", "m3u_url"):
        print(f"      {key:12s}: {creds.get(key) or '(not found)'}")
    return creds


def save_credentials_to_ibo(creds):
    """Push the extracted Xtream credentials into IBO Player via the configured playlist id.

    Reuses iptvvcanada's save_to_iboplayer() (Xtream "xc" payload + retries) by pointing
    its module globals at the WEBEST IBO config. Non-fatal: returns True/False.
    """
    if not WEBEST_IBOPLAYER_ENABLED:
        print("[*] IBO Player save disabled (WEBEST_IBOPLAYER_ENABLED=False)")
        return False
    if not (creds and creds.get("host") and creds.get("username") and creds.get("password")):
        print("[!] IBO Player save skipped: incomplete credentials")
        return False
    if not WEBEST_IBOPLAYER_COOKIE:
        print("[!] IBO Player save skipped: no IBOPLAYER_COOKIE configured")
        return False

    import iptvvcanada_automation as _iptvv

    # Point the shared saver at the WEBEST playlist slot.
    _iptvv.IPTVV_IBOPLAYER_ENABLED = True
    _iptvv.IPTVV_IBOPLAYER_COOKIE = WEBEST_IBOPLAYER_COOKIE
    _iptvv.IPTVV_IBOPLAYER_PLAYLIST_URL_ID = WEBEST_IBOPLAYER_PLAYLIST_URL_ID
    _iptvv.IPTVV_IBOPLAYER_PLAYLIST_NAME = WEBEST_IBOPLAYER_PLAYLIST_NAME

    try:
        return _iptvv.save_to_iboplayer(
            creds["username"], creds["password"], creds["host"]
        )
    except Exception as exc:
        print(f"[!] IBO Player save error: {exc}")
        return False


# ═══════════════════════════════════════════════════════════
# Orchestration
# ═══════════════════════════════════════════════════════════
def register_once(email, user_data, password, email_session, use_proxy):
    """One full attempt: build driver (direct or via proxy) -> fill/submit -> verify OTP.

    When use_proxy is False the host's public IP is used directly; when True a fresh
    validated free proxy is picked. A new driver is used per attempt; the driver is kept
    alive through the OTP step (same session/nonce/token).

    Returns (ok, detail, exit_ip, retryable, creds). retryable is False once the account has
    been created at the verify step (re-registering the same email would just collide). creds
    is the extracted IPTV-credentials dict on success, else None.
    """
    proxy, exit_ip = None, "direct (public IP)"
    if use_proxy:
        proxy, exit_ip = pick_working_proxy()
        if not proxy:
            return False, "no working free proxy available", "unknown", True, None

    driver = None
    try:
        driver = build_proxy_driver(proxy)
        try:
            fill_register_form(driver, user_data, email, password)
        except ProxyDeadError as exc:
            # Dead proxy: rotate to a new one, no giant error-page dump needed.
            return False, f"proxy unusable: {exc}", exit_ip, True, None
        except Exception as exc:
            save_page_debug_artifacts(driver, "webest_register_exception")
            return False, f"exception during form fill: {exc}", exit_ip, True, None

        status, detail = classify_result(driver)

        # WordPress nonce expired (rotating-IP proxy, slow load). Reload for a fresh
        # nonce and resubmit once before giving up — cheap and often fixes it.
        if status == "failure" and ("expired" in detail.lower() or "an error occurred" in detail.lower()):
            print("[!] Nonce expired; reloading register page and resubmitting once...")
            try:
                fill_register_form(driver, user_data, email, password)
                status, detail = classify_result(driver)
            except ProxyDeadError as exc:
                return False, f"proxy unusable on nonce retry: {exc}", exit_ip, True, None
            except Exception as exc:
                return False, f"exception on nonce retry: {exc}", exit_ip, True, None

        # OTP verification step: account created, now needs the emailed code.
        if status == "needs_otp":
            print(f"[*] {detail}")
            code = wait_for_otp_code(email_session, WEBEST_OTP_WAIT)
            if not code:
                save_page_debug_artifacts(driver, "webest_otp_timeout")
                return False, "submitted but no OTP code arrived in time", exit_ip, False, None
            try:
                submit_otp(driver, code)
            except Exception as exc:
                save_page_debug_artifacts(driver, "webest_otp_submit_error")
                return False, f"OTP submit error: {exc}", exit_ip, False, None
            status, detail = classify_result(driver)
            if status == "needs_otp":
                save_page_debug_artifacts(driver, "webest_otp_rejected")
                return False, f"OTP rejected or expired: {detail}", exit_ip, False, None

        if status == "success":
            # Account verified + on the dashboard. Request the trial, then read credentials.
            if WEBEST_REQUEST_TRIAL:
                _, trial_detail = request_trial(driver)
                detail = f"{detail} | trial: {trial_detail}"
            creds = None
            if WEBEST_FETCH_CREDENTIALS:
                creds = open_and_extract_credentials(driver, email_session)
            return True, detail, exit_ip, False, creds

        save_page_debug_artifacts(driver, "webest_register_failed")
        return False, detail, exit_ip, True, None
    finally:
        if driver is not None and AUTO_EXIT:
            try:
                driver.quit()
            except Exception:
                pass


def main():
    print("=" * 60)
    print("WEBESTIPTV - Automated Account Registration")
    print("=" * 60)

    # Try the host's public IP first (most reliable for the stateful WP flow); fall back to
    # fresh free-proxy IPs. Each attempt is a brand-new account (fresh inbox/data) — required
    # because a verified account's email can't be reused, and because a trial stuck at
    # "Awaiting Activation" on one IP gets retried on a different IP per the user's request.
    if WEBEST_USE_PROXY:
        plan = [False] + [True] * (WEBEST_MAX_ATTEMPTS - 1)  # attempt 1 direct, rest proxy
    else:
        plan = [False] * WEBEST_MAX_ATTEMPTS

    success = False
    email = password = None
    detail, exit_ip, creds = "not attempted", "unknown", None

    for attempt, use_proxy in enumerate(plan, 1):
        mode = "free proxy" if use_proxy else "public IP"
        print(f"\n[*] Registration attempt {attempt}/{len(plan)} via {mode}")

        # Fresh account per attempt.
        email_session = create_inbox()
        if not email_session:
            print("[!] Failed to create a receiving email inbox; skipping attempt.")
            continue
        email = email_session["address"]
        user_data = generate_random_user_data()
        password = generate_password()
        print(f"[*] Receiving email ({email_session.get('backend')}): {email}")
        print(f"[*] User: {user_data['first_name']} {user_data['last_name']} | phone {user_data['phone']}")

        ok, detail, exit_ip, _retryable, creds = register_once(
            email, user_data, password, email_session, use_proxy
        )

        if ok and _creds_complete(creds):
            success = True
            break
        if ok:
            # Account verified but the trial line never provisioned (Awaiting Activation).
            print("[!] Account verified but trial is awaiting activation (no credentials).")
        else:
            print(f"[!] Attempt {attempt} did not succeed: {detail}")
        if attempt < len(plan):
            nxt = "free proxy" if plan[attempt] else "public IP"
            print(f"[*] Trying a fresh account via {nxt}...")

    # On success, push the credentials into IBO Player.
    ibo_saved = False
    if success:
        ibo_saved = save_credentials_to_ibo(creds)

    # Split the folded "… | trial: …" note into its own summary line when present.
    result_detail, _, trial_detail = detail.partition(" | trial: ")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print(f"  Email:    {email}")
    print(f"  Password: {password}")
    print(f"  Exit IP:  {exit_ip}")
    print(f"  Result:   {'CREDENTIALS OBTAINED' if success else 'FAILED / awaiting activation'} - {result_detail}")
    if trial_detail:
        print(f"  Trial:    {trial_detail}")
    if creds:
        print("  IPTV credentials:")
        print(f"    Username:     {creds.get('username') or '(not found)'}")
        print(f"    Password:     {creds.get('password') or '(not found)'}")
        print(f"    DNS / Host:   {creds.get('host') or '(not found)'}")
        print(f"    Samsung DNS:  {creds.get('samsung_dns') or '(not found)'}")
        print(f"    M3U URL:      {creds.get('m3u_url') or '(not found)'}")
    if success:
        print(f"  IBO Player:  {'saved (id ' + WEBEST_IBOPLAYER_PLAYLIST_URL_ID + ')' if ibo_saved else 'not saved'}")
    print(f"  Login at: {WEBEST_LOGIN_URL}")
    print("=" * 60)

    # Telegram notification (success or failure).
    if success:
        lines = [f"Email: {email}", f"Password: {password}"]
        if creds:
            if creds.get("username"):
                lines.append(f"IPTV user: {creds['username']}")
            if creds.get("password"):
                lines.append(f"IPTV pass: {creds['password']}")
            if creds.get("host"):
                lines.append(f"Host: {creds['host']}")
            if creds.get("samsung_dns"):
                lines.append(f"Samsung DNS: {creds['samsung_dns']}")
            if creds.get("m3u_url"):
                lines.append(f"M3U: {creds['m3u_url']}")
        lines.append(f"IBO Player: {'saved' if ibo_saved else 'not saved'}")
        lines.append(f"Exit IP: {exit_ip}")
        notifier.send_notification(
            "✅ WEBESTIPTV SUCCESS",
            "Trial account created and credentials extracted.",
            "\n".join(lines),
        )
    else:
        notifier.send_notification(
            "❌ WEBESTIPTV FAILED",
            f"Could not obtain credentials: {result_detail}",
            f"Email: {email}\nExit IP: {exit_ip}",
        )

    return 0 if success else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception as exc:
        import traceback

        notifier.send_notification(
            "❌ WEBESTIPTV CRASH",
            f"Automation crashed: {exc}",
            traceback.format_exc()[-800:],
        )
        raise
