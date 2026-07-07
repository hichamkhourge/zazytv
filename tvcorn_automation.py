"""
TVCORN - Automated Trial Account Creation

Automates the tvcorn.com/trial free-trial flow using a temporary inbox (the same
mail.tm / procmail.xyz backends used by iptvvcanada_automation.py) and extracts
the Xtream credentials the site reveals once the trial is generated.

The tvcorn trial is a multi-step, JavaScript-driven form:

  Step 1  Enter name + email, click "Continue" -> POST /trial/sendOtp.
          The site emails a 6-digit one-time code to the address.
  Step 3  Type the 6-digit OTP into the six .otp-box inputs, click Verify
          -> POST /trial/verifyOtp.
  Step 4  The page subscribes to a public Laravel Echo WebSocket channel
          (trial.<base64(email)>) and shows a generation progress bar.
  Step 5  When generation completes the page renders the connection details
          into .js-val-server / .js-val-user / .js-val-pass / .js-val-m3u.

Because the credentials are delivered only over the WebSocket that the page's own
JavaScript opens, we drive the real page with Selenium (undetected-chromedriver)
and let the browser handle Echo, then scrape the rendered credentials. The
temporary inbox is used mid-flow to read the OTP.

Note: the site runs a client-side guard (detectRiskyTerm) that rejects any name
or email whose alphanumerics contain IPTV-related terms ("tv", "iptv", "m3u",
"xtream", ...). We validate the generated address against the same blacklist and
regenerate until it passes.

Install deps: pip install selenium webdriver-manager python-dotenv requests
"""
import argparse
import html
import json
import os
import random
import re
import string
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
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

# ═══════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════
TVCORN_BASE_URL = os.getenv("TVCORN_BASE_URL", "https://tvcorn.com").rstrip("/")
TVCORN_TRIAL_URL = os.getenv("TVCORN_TRIAL_URL", f"{TVCORN_BASE_URL}/trial")

MAILTM_API_BASE = os.getenv("MAILTM_API_BASE", "https://api.mail.tm")
PROCMAIL_API_BASE = os.getenv("PROCMAIL_API_BASE", "https://api.procmail.xyz").rstrip("/")

# Email backend (shared with iptvvcanada_automation.py):
#   "procmail" (default) — api.procmail.xyz REST API (pure HTTP).
#   "mailtm"             — mail.tm REST API.
TVCORN_EMAIL_BACKEND = os.getenv("TVCORN_EMAIL_BACKEND", "procmail").strip().lower()

# OTP email polling (the code email usually arrives within a minute).
OTP_POLL_SECONDS = int(os.getenv("TVCORN_OTP_POLL_SECONDS", "10"))
OTP_MAX_WAIT_SECONDS = int(os.getenv("TVCORN_OTP_MAX_WAIT_SECONDS", "300"))  # 5 minutes

# How long to wait for the trial generation (WebSocket) to finish and render
# credentials. The site's own client-side timeout is 180s.
GENERATION_MAX_WAIT_SECONDS = int(os.getenv("TVCORN_GENERATION_MAX_WAIT_SECONDS", "240"))

AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"
TVCORN_DEBUG_DIR = os.getenv("TVCORN_DEBUG_DIR", "/app/logs")

# IBO Player integration (optional). Authentication uses the device-login bearer
# token from iboplayer_auth (shared IBOPLAYER_MAC_ADDRESS / IBOPLAYER_DEVICE_KEY /
# TWOCAPTCHA_API_KEY), like viewtvy/uzeen do.
TVCORN_IBOPLAYER_ENABLED = os.getenv("TVCORN_IBOPLAYER_ENABLED", "False").lower() == "true"
TVCORN_IBOPLAYER_PLAYLIST_URL_ID = os.getenv("TVCORN_IBOPLAYER_PLAYLIST_URL_ID", "")
TVCORN_IBOPLAYER_PLAYLIST_NAME = os.getenv("TVCORN_IBOPLAYER_PLAYLIST_NAME", "tvcorn")

# Client-side risky-term blacklist enforced by tvcorn's detectRiskyTerm(). The
# name and email (alphanumerics only, lowercased) must not contain any of these.
RISKY_TERMS = [
    "tv", "iptv", "m3u", "m3u8", "xtream", "xstream", "xtreme", "xcui", "restream",
    "streaming", "livestream", "smartiptv", "smarttv", "ottplayer", "megaiptv",
    "megafiles", "pirat", "piracy", "cracked", "flixiptv", "tivimate", "gseiptv",
    "playlist", "mediastar", "vodafoneiptv", "extremeiptv", "kodi", "duplex",
    "sportztv", "channelsiptv", "bouquet", "epgserver", "rtmp", "cccam", "mgcamd",
]


class TrialFailedError(RuntimeError):
    """Raised when tvcorn rejects the trial or generation fails."""


def is_risky(value):
    """Replicate tvcorn's detectRiskyTerm(): strip to alphanumerics, lowercase,
    and report the first blacklisted substring found (or None)."""
    normalized = re.sub(r"[^a-z0-9]", "", str(value or "").lower())
    if not normalized:
        return None
    for term in RISKY_TERMS:
        if term and term in normalized:
            return term
    return None


# ═══════════════════════════════════════════════════════════
# mail.tm backend
# ═══════════════════════════════════════════════════════════
def get_available_domains():
    """Fetch list of available mail.tm domains."""
    try:
        response = requests.get(f"{MAILTM_API_BASE}/domains", timeout=10)
        response.raise_for_status()
        domains = response.json()
        if domains and "hydra:member" in domains:
            return [d["domain"] for d in domains["hydra:member"]]
        return []
    except Exception as exc:
        print(f"[!] Failed to fetch mail.tm domains: {exc}")
        return []


def create_mailtm_account():
    """Create a temporary email account via mail.tm whose address passes the
    risky-term guard. Returns (address, password, auth_token) or (None, None, None)."""
    try:
        domains = [d for d in get_available_domains() if not is_risky(d)]
        if not domains:
            raise RuntimeError("No usable mail.tm domains available")

        email_address = password = None
        for _ in range(40):
            username = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            domain = random.choice(domains)
            candidate = f"{username}@{domain}"
            if not is_risky(candidate):
                email_address = candidate
                password = "".join(random.choices(string.ascii_letters + string.digits, k=16))
                break
        if not email_address:
            raise RuntimeError("Could not generate a non-risky mail.tm address")

        print(f"[*] Creating mail.tm account: {email_address}")
        requests.post(
            f"{MAILTM_API_BASE}/accounts",
            json={"address": email_address, "password": password},
            timeout=10,
        ).raise_for_status()

        token_response = requests.post(
            f"{MAILTM_API_BASE}/token",
            json={"address": email_address, "password": password},
            timeout=10,
        )
        token_response.raise_for_status()
        auth_token = token_response.json().get("token")
        if not auth_token:
            raise RuntimeError("Failed to get auth token from mail.tm")

        print("[OK] mail.tm account ready")
        return email_address, password, auth_token
    except Exception as exc:
        print(f"[!] Failed to create mail.tm account: {exc}")
        return None, None, None


def get_mailtm_messages(auth_token):
    """Fetch message summaries from a mail.tm inbox."""
    try:
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(f"{MAILTM_API_BASE}/messages", headers=headers, timeout=10)
        response.raise_for_status()
        return response.json().get("hydra:member", [])
    except Exception as exc:
        print(f"[!] Failed to fetch mail.tm messages: {exc}")
        return []


def get_mailtm_message_by_id(auth_token, message_id):
    """Fetch a single mail.tm message body (with 'text'/'html')."""
    try:
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(
            f"{MAILTM_API_BASE}/messages/{message_id}", headers=headers, timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"[!] Failed to fetch message {message_id}: {exc}")
        return None


# ═══════════════════════════════════════════════════════════
# procmail.xyz backend
# ═══════════════════════════════════════════════════════════
def _decode_mime_header(value):
    """Best-effort RFC 2047 decode of a MIME-encoded header."""
    if not value:
        return ""
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _decode_qp(value):
    """Decode a quoted-printable body if it looks QP-encoded, else return as-is."""
    if not value:
        return ""
    if not re.search(r"=[0-9A-Fa-f]{2}|=\r?\n", value):
        return value
    try:
        import quopri
        return quopri.decodestring(value.encode("utf-8", "replace")).decode("utf-8", "replace")
    except Exception:
        return value


def create_procmail_inbox():
    """Generate a disposable address via api.procmail.xyz that passes the
    risky-term guard. Returns the address string or None."""
    try:
        for _ in range(15):
            resp = requests.get(f"{PROCMAIL_API_BASE}/generate", timeout=15)
            resp.raise_for_status()
            address = resp.text.strip().strip('"')
            if "@" not in address:
                raise RuntimeError(f"unexpected /generate response: {address[:120]!r}")
            term = is_risky(address)
            if term:
                print(f"[*] Discarding procmail address '{address}' (contains '{term}'), retrying...")
                continue
            print(f"[OK] procmail inbox created: {address}")
            return address
        raise RuntimeError("Could not obtain a non-risky procmail address")
    except Exception as exc:
        print(f"[!] Failed to create procmail inbox: {exc}")
        return None


def get_procmail_messages(address):
    """Fetch the procmail inbox normalized to the shared message shape
    (each message already carries inline 'text'/'html')."""
    try:
        resp = requests.get(
            f"{PROCMAIL_API_BASE}/inbox/{requests.utils.quote(address)}", timeout=15
        )
        resp.raise_for_status()
        data = resp.json() or []
    except Exception as exc:
        print(f"[!] Failed to fetch procmail inbox: {exc}")
        return []

    from email.utils import parseaddr

    messages = []
    for idx, item in enumerate(data):
        raw_sender = item.get("Sender", "") or ""
        from_addr = parseaddr(raw_sender)[1] or raw_sender
        text_body = _decode_qp(item.get("PlainTextBody", "") or "")
        html_body = _decode_qp(item.get("HTMLBody") or "")
        messages.append({
            "id": f"{item.get('ReceivedAt', idx)}-{idx}",
            "subject": _decode_mime_header(item.get("Subject", "")),
            "from": {"address": from_addr},
            "text": text_body,
            "html": [html_body] if html_body else [],
            "_received": item.get("ReceivedAt", ""),
        })
    return messages


# ═══════════════════════════════════════════════════════════
# Backend dispatchers
# ═══════════════════════════════════════════════════════════
def create_email_session():
    """Allocate a receiving address using the configured backend.
    Returns a session dict carrying 'backend' and 'address', or None."""
    if TVCORN_EMAIL_BACKEND == "mailtm":
        address, password, auth_token = create_mailtm_account()
        if not address:
            return None
        return {"backend": "mailtm", "address": address, "password": password, "token": auth_token}

    address = create_procmail_inbox()
    if not address:
        return None
    return {"backend": "procmail", "address": address}


def fetch_inbox_messages(session):
    """Return inbox messages (each with 'subject'/'from'/'text'/'html') for the
    configured backend, newest-relevant included."""
    if session.get("backend") == "mailtm":
        summaries = get_mailtm_messages(session["token"])
        messages = []
        for summary in summaries:
            full = get_mailtm_message_by_id(session["token"], summary["id"]) or {}
            messages.append({
                "id": summary.get("id"),
                "subject": full.get("subject", summary.get("subject", "")),
                "from": full.get("from", summary.get("from", {})),
                "text": full.get("text", ""),
                "html": full.get("html", []),
                "_received": summary.get("createdAt", ""),
            })
        return messages
    return get_procmail_messages(session["address"])


# ═══════════════════════════════════════════════════════════
# OTP extraction
# ═══════════════════════════════════════════════════════════
def _message_plaintext(message):
    """Flatten a message's text + html into searchable plain text."""
    text_content = message.get("text", "") or ""
    html_content = message.get("html", [])
    if isinstance(html_content, list):
        html_content = " ".join(html_content)
    combined = f"{text_content}\n\n{html_content}"
    for _ in range(3):
        unescaped = html.unescape(combined)
        if unescaped == combined:
            break
        combined = unescaped
    normalized = re.sub(r"<br\s*/?>", "\n", combined, flags=re.I)
    normalized = re.sub(r"</p\s*>", "\n", normalized, flags=re.I)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    return re.sub(r"[ \t]+", " ", normalized)


def extract_otp_from_message(message):
    """Pull a 6-digit one-time code out of an email body. Prefers a code that
    appears near an OTP keyword, then falls back to any standalone 6-digit run."""
    body = _message_plaintext(message)

    # Strong signal: a 6-digit code adjacent to an OTP-ish keyword.
    keyword_patterns = [
        r"(?:code|otp|verification|verify|one[\s-]*time|pin)[^0-9]{0,40}?(?<!\d)(\d{6})(?!\d)",
        r"(?<!\d)(\d{6})(?!\d)[^0-9]{0,40}?(?:code|otp|verification|verify)",
    ]
    for pattern in keyword_patterns:
        match = re.search(pattern, body, re.I)
        if match:
            return match.group(1)

    # Fallback: the first standalone 6-digit sequence.
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", body)
    if match:
        return match.group(1)
    return None


def wait_for_otp(session, sent_after_ids, max_wait_seconds=OTP_MAX_WAIT_SECONDS):
    """Poll the inbox until an email with a 6-digit OTP arrives.

    sent_after_ids: set of message ids that already existed before we requested
    the OTP, so we only consider newly-arrived mail."""
    print(f"[*] Waiting for OTP email (max {max_wait_seconds}s)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking inbox for OTP (attempt {attempt}, {remaining}s remaining)...")
        messages = fetch_inbox_messages(session)
        # Newest first when a received timestamp is available.
        messages = sorted(messages, key=lambda m: m.get("_received", ""), reverse=True)
        for msg in messages:
            if msg.get("id") in sent_after_ids:
                continue
            subject = msg.get("subject", "")
            from_addr = msg.get("from", {}).get("address", "")
            otp = extract_otp_from_message(msg)
            if otp:
                print(f"[OK] OTP found: {otp} (from: {from_addr}, subject: {subject})")
                return otp
            print(f"    - Email without OTP from {from_addr}: {subject}")
        time.sleep(OTP_POLL_SECONDS)
    print(f"[!] Timeout: OTP email not received after {max_wait_seconds}s")
    return None


# ═══════════════════════════════════════════════════════════
# Selenium browser automation
# ═══════════════════════════════════════════════════════════
def get_random_user_agent():
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    ]
    return random.choice(user_agents)


def get_driver():
    """Initialize Chrome WebDriver with anti-detection options (undetected-chromedriver)."""
    headless_mode = os.getenv("HEADLESS", "True").lower() == "true"
    options = uc.ChromeOptions()

    if headless_mode:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        print("[*] Running in HEADLESS mode")
    else:
        options.add_argument("--start-maximized")
        print("[*] Running in GUI mode")

    random_ua = get_random_user_agent()
    options.add_argument(f"--user-agent={random_ua}")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-notifications")
    options.add_argument("--no-proxy-server")

    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
        "proxy": {"mode": "direct", "pac_url": "", "bypass_list": ""},
    }
    options.add_experimental_option("prefs", prefs)

    chrome_binary = os.getenv("CHROME_BINARY", "").strip()
    if not chrome_binary:
        for candidate in ("/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"):
            if os.path.exists(candidate):
                chrome_binary = candidate
                break
    if chrome_binary:
        options.binary_location = chrome_binary
        print(f"[*] Using Chrome binary: {chrome_binary}")

    try:
        print("[*] Initializing undetected-chromedriver...")
        driver = uc.Chrome(options=options, use_subprocess=False)
        print("[OK] undetected-chromedriver initialized")
    except Exception as exc:
        print(f"[!] undetected-chromedriver failed ({exc}); falling back to regular ChromeDriver...")
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
        service = Service(chromedriver_path) if os.path.exists(chromedriver_path) \
            else Service(ChromeDriverManager().install())
        regular_options = Options()
        for arg in options.arguments:
            regular_options.add_argument(arg)
        driver = webdriver.Chrome(service=service, options=regular_options)
    return driver


def safe_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.3)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)


def save_page_debug_artifacts(driver, label):
    """Save a screenshot and HTML snapshot for diagnosis."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "page"
    artifacts = {}
    for candidate_dir in [TVCORN_DEBUG_DIR, "./logs", os.path.expanduser("~/logs"), "/tmp"]:
        try:
            os.makedirs(candidate_dir, exist_ok=True)
            test_file = os.path.join(candidate_dir, f".write_test_{os.getpid()}")
            with open(test_file, "w") as fh:
                fh.write("test")
            os.remove(test_file)
            debug_dir = candidate_dir
            break
        except Exception:
            continue
    else:
        return artifacts

    base_path = os.path.join(debug_dir, f"tvcorn_{safe_label}_{timestamp}")
    try:
        driver.save_screenshot(f"{base_path}.png")
        artifacts["screenshot"] = f"{base_path}.png"
        print(f"[*] Screenshot saved to: {base_path}.png")
    except Exception as exc:
        print(f"[!] Could not save screenshot: {exc}")
    try:
        with open(f"{base_path}.html", "w", encoding="utf-8") as fh:
            fh.write(driver.page_source)
        artifacts["html"] = f"{base_path}.html"
        print(f"[*] HTML snapshot saved to: {base_path}.html")
    except Exception as exc:
        print(f"[!] Could not save HTML snapshot: {exc}")
    return artifacts


def get_active_step(driver):
    """Return the step-target of the currently visible .js-step (e.g. '1','3','5','error')."""
    try:
        el = driver.find_element(By.CSS_SELECTOR, ".js-step.active")
        return el.get_attribute("step-target")
    except Exception:
        return None


def wait_for_step(driver, target, timeout):
    """Wait until the .js-step with the given step-target becomes the active one."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if get_active_step(driver) == str(target):
            return True
        time.sleep(0.5)
    return False


def read_toast_text(driver):
    """Best-effort read of any visible toast/notice message for error reporting."""
    selectors = [".toast", ".toastify", "[class*='toast']", ".js-error-email", ".text-red-500"]
    for selector in selectors:
        try:
            for el in driver.find_elements(By.CSS_SELECTOR, selector):
                if el.is_displayed() and el.text.strip():
                    return el.text.strip()
        except Exception:
            continue
    return ""


def generate_user_name():
    """Generate a realistic, non-risky full name."""
    first_names = ["John", "Jane", "Michael", "Sarah", "David", "Emma", "Chris",
                   "Laura", "Daniel", "Olivia", "James", "Sophie", "Robert", "Anna"]
    last_names = ["Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore",
                  "Taylor", "Anderson", "Clark", "Walker", "Harris", "Young"]
    for _ in range(20):
        name = f"{random.choice(first_names)} {random.choice(last_names)}"
        if not is_risky(name):
            return name
    return "John Smith"


def fill_step1_and_send_otp(driver, name, email_address):
    """Fill name + email on step 1 and click Continue to trigger /trial/sendOtp."""
    print("[*] Loading trial page...")
    driver.get(TVCORN_TRIAL_URL)
    WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(3)

    if not wait_for_step(driver, 1, timeout=20):
        # Page rendered but step 1 not flagged active yet; the form may still be usable.
        print("[!] Step 1 not marked active; continuing on a best-effort basis")

    name_input = WebDriverWait(driver, 20).until(
        EC.presence_of_element_located((By.CSS_SELECTOR, ".js-form-step-1 [name='name']"))
    )
    email_input = driver.find_element(By.CSS_SELECTOR, ".js-user-email")

    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", name_input)
    name_input.clear()
    name_input.send_keys(name)
    email_input.clear()
    email_input.send_keys(email_address)
    print(f"[OK] Filled name='{name}', email='{email_address}'")

    # The step-1 primary button triggers submitStepData() -> POST /trial/sendOtp.
    continue_btn = driver.find_element(By.CSS_SELECTOR, ".js-form-step-1 .js-go-to-step")
    safe_click(driver, continue_btn)
    print("[*] Clicked Continue; requesting OTP...")

    # On success the site advances to the OTP step (step 3).
    if not wait_for_step(driver, 3, timeout=30):
        toast = read_toast_text(driver)
        save_page_debug_artifacts(driver, "send_otp_failed")
        raise TrialFailedError(
            "tvcorn did not advance to the OTP step after submitting name/email. "
            f"Site message: {toast or 'none'}. The email domain may be rejected server-side."
        )
    print("[OK] Reached OTP step (step 3) - OTP has been emailed")


def submit_otp_and_wait_credentials(driver, otp):
    """Type the OTP into the six boxes, verify, and wait for the credentials to render."""
    print(f"[*] Entering OTP: {otp}")
    otp_boxes = driver.find_elements(By.CSS_SELECTOR, ".otp-box")
    if len(otp_boxes) < 6:
        raise TrialFailedError(f"Expected 6 OTP boxes, found {len(otp_boxes)}")

    for box, digit in zip(otp_boxes, otp):
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", box)
        box.clear()
        box.send_keys(digit)
        time.sleep(0.15)

    verify_btn = driver.find_element(By.CSS_SELECTOR, ".js-verify-otp")
    safe_click(driver, verify_btn)
    print("[*] Clicked Verify; validating OTP and generating trial...")

    # verifyOtp failure keeps us on step 3 with a toast; success goes to step 4 then 5.
    time.sleep(4)
    if get_active_step(driver) == "3":
        toast = read_toast_text(driver)
        if toast:
            raise TrialFailedError(f"OTP verification failed: {toast}")

    print(f"[*] Waiting up to {GENERATION_MAX_WAIT_SECONDS}s for trial generation...")
    deadline = time.time() + GENERATION_MAX_WAIT_SECONDS
    while time.time() < deadline:
        step = get_active_step(driver)
        if step == "5":
            print("[OK] Reached success step (step 5)")
            return
        if step == "error":
            toast = read_toast_text(driver)
            save_page_debug_artifacts(driver, "generation_error")
            raise TrialFailedError(f"Trial generation failed: {toast or 'site returned error step'}")
        time.sleep(2)

    save_page_debug_artifacts(driver, "generation_timeout")
    raise TrialFailedError(
        f"Trial generation did not complete within {GENERATION_MAX_WAIT_SECONDS}s"
    )


def extract_credentials(driver):
    """Read the connection details rendered on the success step.

    The values live inside tab panels (the m3u tab is display:none and the spans
    are not "displayed" by Selenium's rules), so element.text returns "". We read
    textContent via JS instead, scanning every matching node and taking the first
    non-empty value."""
    def text_of(selector):
        try:
            els = driver.find_elements(By.CSS_SELECTOR, selector)
            for el in els:
                value = (driver.execute_script("return arguments[0].textContent;", el) or "").strip()
                if value:
                    return value
        except Exception:
            pass
        return ""

    hostname = text_of(".js-val-server")
    username = text_of(".js-val-user")
    password = text_of(".js-val-pass")
    m3u_url = text_of(".js-val-m3u")

    print("[*] Extracted connection details:")
    print(f"    Server:   {hostname}")
    print(f"    Username: {username}")
    print(f"    Password: {password}")
    print(f"    M3U URL:  {m3u_url}")
    return username, password, hostname, m3u_url


# ═══════════════════════════════════════════════════════════
# IBO Player + webhook + Telegram (optional integrations)
# ═══════════════════════════════════════════════════════════
def save_to_iboplayer(username, password, hostname, max_retries=3):
    """Save the playlist to IBO Player using their API (if enabled).

    Authenticated with a device-login bearer token (obtained/cached by
    iboplayer_auth); a 401/403 triggers a token refresh + one retry.
    """
    if not TVCORN_IBOPLAYER_ENABLED:
        print("[*] IBO Player integration disabled (TVCORN_IBOPLAYER_ENABLED=False)")
        return False
    if (
        not TVCORN_IBOPLAYER_PLAYLIST_URL_ID
        or not iboplayer_auth.IBOPLAYER_MAC_ADDRESS
        or not iboplayer_auth.IBOPLAYER_DEVICE_KEY
        or not iboplayer_auth.TWOCAPTCHA_API_KEY
    ):
        print("[!] IBO Player enabled but TVCORN_IBOPLAYER_PLAYLIST_URL_ID / "
              "IBOPLAYER_MAC_ADDRESS / IBOPLAYER_DEVICE_KEY / TWOCAPTCHA_API_KEY missing")
        return False

    api_url = "https://iboplayer.com/frontend/device/savePlaylist"
    try:
        headers = iboplayer_auth.authed_headers()
    except Exception as e:
        print(f"[!] Could not obtain IBO Player bearer token: {e}")
        return False

    payload = {
        "current_playlist_url_id": TVCORN_IBOPLAYER_PLAYLIST_URL_ID,
        "password": password,
        "pin": "",
        "playlist_name": TVCORN_IBOPLAYER_PLAYLIST_NAME,
        "playlist_type": "xc",
        "playlist_url": (hostname or "").rstrip("/"),
        "protect": "false",
        "username": username,
        "xml_url": "",
    }
    print("[*] Saving playlist to IBO Player...")
    relogin_attempted = False
    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
            if response.status_code == 200:
                # HTTP 200 alone is NOT success: IBO Player returns
                # {"status":"error"} in the body when it rejects the save.
                try:
                    response_data = response.json()
                except Exception:
                    response_data = None
                if isinstance(response_data, dict) and response_data.get("status") == "success":
                    print("[OK] Playlist saved to IBO Player")
                    return True
                print(f"[!] IBO Player rejected the save (status != success): "
                      f"{response_data if response_data is not None else response.text[:300]}")
                print("[!] Most common cause: TVCORN_IBOPLAYER_PLAYLIST_URL_ID is not a playlist "
                      f"owned by the logged-in device ({iboplayer_auth.IBOPLAYER_MAC_ADDRESS}).")
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
                print(f"[!] IBO Player config error {response.status_code}: {response.text[:200]}")
                return False
            print(f"[!] IBO Player server error {response.status_code} (attempt {attempt}/{max_retries})")
        except Exception as exc:
            print(f"[!] IBO Player exception (attempt {attempt}/{max_retries}): {exc}")
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    print("[!] Failed to save playlist to IBO Player")
    return False


def send_webhook_callback(callback_url, user_id, status, username=None, password=None,
                          host=None, m3u_url=None, error=None, max_retries=3):
    """Send a webhook callback to a Laravel backend (if a callback URL is provided)."""
    if not callback_url:
        return False
    payload = {"user_id": user_id, "status": status, "timestamp": datetime.now().isoformat()}
    if status == "success":
        payload.update({"username": username, "password": password, "host": host, "m3u_url": m3u_url})
    else:
        payload["error"] = error

    headers = {"Content-Type": "application/json", "User-Agent": "TVCORN-Automation/1.0"}
    webhook_token = os.getenv("WEBHOOK_AUTH_TOKEN", "")
    if webhook_token:
        headers["Authorization"] = f"Bearer {webhook_token}"

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
            print(f"[*] Webhook response status: {response.status_code}")
            if response.status_code in (200, 201, 202):
                return True
            if 400 <= response.status_code < 500:
                print(f"[!] Webhook client error: {response.text[:500]}")
                return False
        except requests.RequestException as exc:
            print(f"[!] Webhook request failed: {exc}")
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    return False


def send_telegram(status, message, **kwargs):
    try:
        if status == "success":
            notifier.notify_success(message=message, **kwargs)
        else:
            notifier.notify_error(message, kwargs.get("traceback"))
    except Exception as exc:
        print(f"[!] Telegram notification failed: {exc}")


# ═══════════════════════════════════════════════════════════
# Main flow
# ═══════════════════════════════════════════════════════════
def main(user_id=None, callback_url=None):
    driver = None
    is_laravel_mode = bool(user_id and callback_url)

    print("\n" + "=" * 60)
    print("TVCORN - AUTOMATED TRIAL CREATION")
    print("=" * 60)
    print(f"[*] Trial URL:    {TVCORN_TRIAL_URL}")
    print(f"[*] Email backend: {TVCORN_EMAIL_BACKEND}")
    print(f"[*] User ID:       {user_id if user_id else 'N/A'}")
    print("=" * 60 + "\n")

    try:
        # Step 1: temporary inbox (validated against the risky-term guard).
        email_session = create_email_session()
        if not email_session:
            raise RuntimeError(f"Failed to create temporary email ({TVCORN_EMAIL_BACKEND} backend)")
        email_address = email_session["address"]
        name = generate_user_name()

        # Snapshot existing inbox ids so we only match the new OTP email.
        existing_ids = {m.get("id") for m in fetch_inbox_messages(email_session)}

        # Step 2: browser, fill form, request OTP.
        driver = get_driver()
        fill_step1_and_send_otp(driver, name, email_address)

        # Step 3: read the OTP from the inbox.
        otp = wait_for_otp(email_session, existing_ids)
        if not otp:
            raise TrialFailedError("OTP email never arrived")

        # Step 4: submit OTP and wait for the trial to generate.
        submit_otp_and_wait_credentials(driver, otp)

        # Step 5: scrape the credentials.
        username, password, hostname, m3u_url = extract_credentials(driver)
        if not username or not password or not hostname:
            save_page_debug_artifacts(driver, "missing_credentials")
            raise TrialFailedError("Reached success step but credentials were empty")

        if not m3u_url:
            m3u_url = f"{hostname.rstrip('/')}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

        print("\n" + "=" * 60)
        print("✓ TVCORN TRIAL CREDENTIALS EXTRACTED SUCCESSFULLY")
        print("=" * 60)
        print(f"[*] Email used: {email_address}")
        print(f"[*] Server:     {hostname}")
        print(f"[*] Username:   {username}")
        print(f"[*] Password:   {password}")
        print(f"[*] M3U URL:    {m3u_url}")
        print("=" * 60 + "\n")

        save_to_iboplayer(username, password, hostname)

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url, user_id=user_id, status="success",
                username=username, password=password, host=hostname, m3u_url=m3u_url,
            )
        send_telegram("success", f"TVCORN trial created for {email_address}",
                      host=hostname, username=username, password=password, m3u_url=m3u_url)
        print("[OK] TVCORN automation complete")

    except Exception as exc:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"\n[!] TVCORN automation failed: {exc}")
        print(error_traceback)
        if driver:
            try:
                driver.quit()
                driver = None
            except Exception:
                pass
        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url, user_id=user_id, status="failed",
                error=f"{exc}\n\n{error_traceback}",
            )
        send_telegram("error", str(exc), traceback=error_traceback)
        raise SystemExit(1)

    finally:
        if driver and AUTO_EXIT:
            try:
                driver.quit()
                print("[*] Browser closed")
            except Exception:
                pass
        elif driver:
            print("[*] AUTO_EXIT disabled; browser will remain open")
            while True:
                time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="TVCORN - Automated Trial Account Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python tvcorn_automation.py
  HEADLESS=False python tvcorn_automation.py
  python tvcorn_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/tvcorn-automation
        """,
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    args = parser.parse_args()

    main(user_id=args.user_id, callback_url=args.callback_url)
