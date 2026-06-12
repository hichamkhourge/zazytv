"""
IPTVV Canada - Automated Trial Account Creation

Automates the IPTVV.ca cart checkout flow and extracts Xtream credentials from the
received email. By default it uses a real Gmail account read over IMAP with plus-aliasing
(IPTVV_GMAIL_* env vars); set IPTVV_EMAIL_BACKEND=tempmaillol or =mailtm for the
temp-mail backends.

Install deps: pip install selenium webdriver-manager 2captcha-python python-dotenv requests
"""
import argparse
import email as email_lib
import email.header
import email.utils
import html
import imaplib
import json
import os
import random
import re
import string
import tempfile
import time
from datetime import datetime, timezone
from urllib.parse import urlparse
import requests
from dotenv import load_dotenv
import undetected_chromedriver as uc
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
IPTVV_BASE_URL = os.getenv("IPTVV_BASE_URL", "https://iptvv.ca").rstrip("/")
IPTVV_CART_URL = os.getenv("IPTVV_CART_URL", f"{IPTVV_BASE_URL}/cart/")
MAILTM_API_BASE = os.getenv("MAILTM_API_BASE", "https://api.mail.tm")

# Email backend:
#   "gmail"      (default) — a real Gmail account read over IMAP, using plus-aliasing
#                (<base>+<random>@gmail.com) so every run gets a fresh, fully deliverable
#                address that passes IPTVV's checkout email validation.
#   "tempmaillol"          — free tempmail.lol API (rotating obscure domains).
#   "mailtm"               — legacy mail.tm API (now blocked by IPTVV).
IPTVV_EMAIL_BACKEND = os.getenv("IPTVV_EMAIL_BACKEND", "gmail").strip().lower()

# Gmail IMAP backend. Log in to the base mailbox over IMAP with a Google App Password
# (requires 2-Step Verification on the account).
IPTVV_GMAIL_ADDRESS = os.getenv("IPTVV_GMAIL_ADDRESS", "").strip()
IPTVV_GMAIL_APP_PASSWORD = os.getenv("IPTVV_GMAIL_APP_PASSWORD", "").replace(" ", "")
IPTVV_GMAIL_IMAP_HOST = os.getenv("IPTVV_GMAIL_IMAP_HOST", "imap.gmail.com").strip()
IPTVV_GMAIL_IMAP_PORT = int(os.getenv("IPTVV_GMAIL_IMAP_PORT", "993"))
# Receiving address scheme:
#   - If IPTVV_EMAIL_DOMAIN is set (a domain you own with a catch-all forwarding into the
#     Gmail base mailbox), each run gets a distinct real address random@that-domain. This
#     is the reliable choice: IPTVV cannot normalize/dedupe distinct local parts the way
#     it strips Gmail "+tags", and your own domain is not on any disposable blocklist.
#   - If unset, it falls back to a Gmail plus-alias <base>+<random>@gmail.com (note: IPTVV
#     normalizes these to the base account, so they only yield one trial per base account).
IPTVV_EMAIL_DOMAIN = os.getenv("IPTVV_EMAIL_DOMAIN", "").strip().lstrip("@")

# tempmail.lol API. No key is required for the free tier; set TEMPMAILLOL_API_KEY for
# higher rate limits, and TEMPMAILLOL_DOMAIN to pin a specific domain (via v2 create).
TEMPMAILLOL_API_BASE = os.getenv("TEMPMAILLOL_API_BASE", "https://api.tempmail.lol").rstrip("/")
TEMPMAILLOL_API_KEY = os.getenv("TEMPMAILLOL_API_KEY", "").strip()
TEMPMAILLOL_DOMAIN = os.getenv("TEMPMAILLOL_DOMAIN", "").strip().lstrip("@")

EMAIL_POLL_SECONDS = int(os.getenv("IPTVV_EMAIL_POLL_SECONDS", "30"))
EMAIL_MAX_WAIT_SECONDS = int(os.getenv("IPTVV_EMAIL_MAX_WAIT_SECONDS", "2700"))  # 45 minutes
# Socket timeout for IMAP calls so a stalled connection can't hang the whole run.
IPTVV_IMAP_TIMEOUT = int(os.getenv("IPTVV_IMAP_TIMEOUT", "30"))
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"
IPTVV_PAGE_LOAD_RETRIES = int(os.getenv("IPTVV_PAGE_LOAD_RETRIES", "2"))
IPTVV_CLOUDFLARE_WAIT_SECONDS = int(os.getenv("IPTVV_CLOUDFLARE_WAIT_SECONDS", "45"))
IPTVV_DEBUG_DIR = os.getenv("IPTVV_DEBUG_DIR", "/app/logs")
IPTVV_PROXY_CHECK_URL = os.getenv("IPTVV_PROXY_CHECK_URL", "https://api.ipify.org")
IPTVV_KNOWN_BLOCKED_IP = os.getenv("IPTVV_KNOWN_BLOCKED_IP", "").strip()

# Optional authenticated proxy for the checkout browser (e.g. Apify residential).
# Disabled by default; when enabled, only the browser egress is proxied -- the
# requests-based calls (mail.tm, 2captcha, IBO Player, webhooks) stay direct.
USE_IPTVV_PROXY = os.getenv("USE_IPTVV_PROXY", "False").lower() == "true"
IPTVV_PROXY_URL = os.getenv("IPTVV_PROXY_URL", "").strip()

# IBO Player integration configuration
IPTVV_IBOPLAYER_ENABLED = os.getenv("IPTVV_IBOPLAYER_ENABLED", "False").lower() == "true"
IPTVV_IBOPLAYER_COOKIE = os.getenv("IPTVV_IBOPLAYER_COOKIE", "")
IPTVV_IBOPLAYER_PLAYLIST_URL_ID = os.getenv("IPTVV_IBOPLAYER_PLAYLIST_URL_ID", "")
IPTVV_IBOPLAYER_PLAYLIST_NAME = os.getenv("IPTVV_IBOPLAYER_PLAYLIST_NAME", "IPTVV Canada")
IPTVV_IBOPLAYER_PLAYLIST_URL_TEMPLATE = os.getenv("IPTVV_IBOPLAYER_PLAYLIST_URL", "http://iptvvcanada.com")

# Email subject patterns to detect credentials email (checked case-insensitively)
CREDENTIALS_EMAIL_SUBJECTS = [
    "Free 24-Hour IPTV Trial Subscription",  # Current IPTVV format
    "Your trial is now active",               # Legacy/alternate format
    "IPTV Trial Subscription",                # Partial match
]
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY else None


class CloudflareBlockedError(RuntimeError):
    """Raised when IPTVV serves a Cloudflare/WAF page instead of checkout."""


class TrialRejectedError(RuntimeError):
    """Raised when IPTVV accepts checkout but refuses to issue trial credentials."""


# ═══════════════════════════════════════════════════════════
# Mail.tm API Helper Functions
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
    """
    Create a temporary email account via mail.tm API.

    Returns:
        tuple: (email_address, password, auth_token) or (None, None, None) on failure
    """
    try:
        domains = get_available_domains()
        if not domains:
            raise RuntimeError("No mail.tm domains available")

        # Generate random email
        username = ''.join(random.choices(string.ascii_lowercase + string.digits, k=12))
        domain = random.choice(domains)
        email_address = f"{username}@{domain}"
        password = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

        print(f"[*] Creating mail.tm account: {email_address}")

        # Create account
        create_response = requests.post(
            f"{MAILTM_API_BASE}/accounts",
            json={"address": email_address, "password": password},
            timeout=10
        )
        create_response.raise_for_status()
        print(f"[OK] Mail.tm account created: {email_address}")

        # Get auth token
        token_response = requests.post(
            f"{MAILTM_API_BASE}/token",
            json={"address": email_address, "password": password},
            timeout=10
        )
        token_response.raise_for_status()
        auth_token = token_response.json().get("token")

        if not auth_token:
            raise RuntimeError("Failed to get auth token from mail.tm")

        print("[OK] Mail.tm authentication successful")
        return email_address, password, auth_token

    except Exception as exc:
        print(f"[!] Failed to create mail.tm account: {exc}")
        return None, None, None


def get_mailtm_messages(auth_token):
    """
    Fetch messages from mail.tm inbox.

    Args:
        auth_token: Bearer token for authentication

    Returns:
        list: List of message objects
    """
    try:
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(
            f"{MAILTM_API_BASE}/messages",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        data = response.json()
        return data.get("hydra:member", [])
    except Exception as exc:
        print(f"[!] Failed to fetch mail.tm messages: {exc}")
        return []


def get_mailtm_message_by_id(auth_token, message_id):
    """
    Fetch a specific message's full content from mail.tm.

    Args:
        auth_token: Bearer token for authentication
        message_id: ID of the message to retrieve

    Returns:
        dict: Message object with 'text' and 'html' fields, or None on failure
    """
    try:
        headers = {"Authorization": f"Bearer {auth_token}"}
        response = requests.get(
            f"{MAILTM_API_BASE}/messages/{message_id}",
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        print(f"[!] Failed to fetch message {message_id}: {exc}")
        return None


def _scan_messages_for_credentials(messages, fetch_full):
    """Scan a list of normalized messages for the IPTVV credentials/rejection email.

    Each message is a dict with at least 'subject', 'from': {'address': ...} and 'id'.
    `fetch_full(msg)` returns the full message body (with 'text'/'html'); for tempmail.lol
    the message is already complete so it returns msg unchanged, while for mail.tm it
    fetches the body by id.

    Returns the full credentials message, or None if not found in this batch.
    Raises TrialRejectedError if a rejection email is seen.
    """
    rejection_markers = [
        "already used",
        "duplicate trial",
        "trial was already used",
        "order was cancelled",
        "order was canceled",
    ]

    for msg in messages:
        subject = msg.get("subject", "")
        from_addr = msg.get("from", {}).get("address", "")

        print(f"    - From: {from_addr}, Subject: {subject}")

        lowered_subject = subject.lower()

        if "iptvv" in from_addr.lower() and any(marker in lowered_subject for marker in rejection_markers):
            full_message = fetch_full(msg)
            preview = ""
            if full_message:
                preview = full_message.get("text", "")[:500].strip()
            raise TrialRejectedError(
                f"IPTVV refused to issue trial credentials: {subject}. "
                f"Message preview: {preview}"
            )

        # Check if this is the credentials email (check multiple subject patterns)
        for pattern in CREDENTIALS_EMAIL_SUBJECTS:
            if pattern.lower() in lowered_subject:
                print("[OK] Credentials email found!")
                full_message = fetch_full(msg)
                if full_message:
                    return full_message
                break

    return None


# ═══════════════════════════════════════════════════════════
# Gmail IMAP Backend (plus-aliasing)
# ═══════════════════════════════════════════════════════════

def _redact(value, keep=2):
    """Mask a secret for logging: keep the first/last `keep` chars, star the rest."""
    if not value:
        return "(empty)"
    s = str(value)
    if len(s) <= keep * 2:
        return "*" * len(s)
    return f"{s[:keep]}{'*' * (len(s) - keep * 2)}{s[-keep:]}"


def build_gmail_alias():
    """Build a fresh receiving address read via the Gmail base mailbox.

    With IPTVV_EMAIL_DOMAIN set (a catch-all domain forwarding into the Gmail mailbox),
    returns a distinct real address random@that-domain. Otherwise falls back to a Gmail
    plus-alias <base>+<random>@gmail.com.
    """
    # Letters only (no digits): IPTVV's checkout validator rejects plus-tags
    # that contain numbers, so keep the local part alphabetic.
    suffix = "".join(random.choices(string.ascii_lowercase, k=12))
    if IPTVV_EMAIL_DOMAIN:
        # A leading letter keeps the local part looking like a normal username.
        return f"u{suffix}@{IPTVV_EMAIL_DOMAIN}"
    local, _, domain = IPTVV_GMAIL_ADDRESS.partition("@")
    return f"{local}+{suffix}@{domain}"


def _gmail_connect():
    """Open an authenticated IMAP connection to the Gmail base inbox."""
    conn = imaplib.IMAP4_SSL(IPTVV_GMAIL_IMAP_HOST, IPTVV_GMAIL_IMAP_PORT, timeout=IPTVV_IMAP_TIMEOUT)
    conn.login(IPTVV_GMAIL_ADDRESS, IPTVV_GMAIL_APP_PASSWORD)
    conn.select("INBOX")
    return conn


def _gmail_search_uids_for_alias(conn, alias):
    """Return the set of message UIDs delivered to `alias`.

    Gmail records the exact plus-alias in the Delivered-To header and supports the
    native X-GM-RAW search, which is the most reliable; standard HEADER/TO searches are
    tried as fallbacks. The alias is freshly random per run, so any match belongs to us.
    """
    uids = set()
    # Gmail-native search first (handles plus-aliasing reliably).
    try:
        typ, data = conn.uid("search", None, "X-GM-RAW", f'"deliveredto:{alias}"')
        if typ == "OK" and data and data[0]:
            uids.update(data[0].split())
    except Exception:
        pass

    for criterion in (
        f'(HEADER DELIVERED-TO "{alias}")',
        f'(HEADER TO "{alias}")',
        f'(TO "{alias}")',
    ):
        try:
            typ, data = conn.uid("search", None, criterion)
            if typ == "OK" and data and data[0]:
                uids.update(data[0].split())
        except Exception:
            continue
    return uids


def _parse_imap_message(raw_bytes, uid):
    """Parse a raw RFC822 message into the normalized shape used by the scanner."""
    msg = email_lib.message_from_bytes(raw_bytes)

    try:
        subject = str(email_lib.header.make_header(email_lib.header.decode_header(msg.get("Subject", ""))))
    except Exception:
        subject = msg.get("Subject", "") or ""

    from_addr = email_lib.utils.parseaddr(msg.get("From", ""))[1]

    text_parts = []
    html_parts = []

    def _decode(part):
        payload = part.get_payload(decode=True)
        if payload is None:
            return None
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            return payload.decode("utf-8", errors="replace")

    if msg.is_multipart():
        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = str(part.get("Content-Disposition") or "")
            if "attachment" in disposition.lower():
                continue
            decoded = _decode(part)
            if decoded is None:
                continue
            ctype = part.get_content_type()
            if ctype == "text/html":
                html_parts.append(decoded)
            elif ctype == "text/plain":
                text_parts.append(decoded)
    else:
        decoded = _decode(msg)
        if decoded is not None:
            if msg.get_content_type() == "text/html":
                html_parts.append(decoded)
            else:
                text_parts.append(decoded)

    uid_str = uid.decode() if isinstance(uid, bytes) else str(uid)
    return {
        "id": uid_str,
        "subject": subject,
        "from": {"address": from_addr},
        "text": "\n".join(text_parts),
        "html": html_parts,  # list, matching the mail.tm message shape
    }


def _fetch_gmail_messages(alias):
    """Connect, find, and parse all messages delivered to `alias`."""
    conn = _gmail_connect()
    try:
        uids = _gmail_search_uids_for_alias(conn, alias)
        messages = []
        for uid in uids:
            typ, data = conn.uid("fetch", uid, "(RFC822)")
            if typ != "OK" or not data:
                continue
            # The raw message is the body of a (header, body) tuple; the server may
            # also interleave bare bytes (e.g. b')'), so pick the first valid tuple.
            raw = next(
                (item[1] for item in data
                 if isinstance(item, tuple) and len(item) >= 2 and item[1]),
                None,
            )
            if raw is None:
                continue
            messages.append(_parse_imap_message(raw, uid))
        return messages
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def _wait_for_credentials_email_gmail(alias, max_wait_seconds):
    """Poll the Gmail inbox for the credentials email delivered to `alias`."""
    print(f"[*] Waiting for credentials email at {alias} (max {max_wait_seconds}s / {max_wait_seconds//60} minutes)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking Gmail inbox for {alias} (attempt {attempt}, {remaining}s remaining)...")

        try:
            messages = _fetch_gmail_messages(alias)
        except Exception as exc:
            print(f"[!] Gmail IMAP check failed: {exc}")
            messages = []

        result = _scan_messages_for_credentials(messages, fetch_full=lambda m: m)
        if result:
            return result

        if messages:
            print(f"[*] Found {len(messages)} email(s) for this alias, but credentials email not yet received")
        else:
            print("[*] No mail for this alias yet")

        print(f"[*] Waiting {EMAIL_POLL_SECONDS}s before next check...")
        time.sleep(EMAIL_POLL_SECONDS)

    print(f"[!] Timeout: Credentials email not received after {max_wait_seconds}s")
    return None


# ═══════════════════════════════════════════════════════════
# tempmail.lol Backend
# ═══════════════════════════════════════════════════════════

# Domains we already know IPTVV's checkout rejects as disposable; if tempmail.lol
# rotates onto one of these, regenerate to get a clean domain.
TEMPMAILLOL_BLOCKED_DOMAINS = {
    "mailto.plus", "fexpost.com", "fexbox.org", "fextemp.com", "any.pink",
    "merepost.com", "rover.info", "chitthi.in", "mailisk.com",
}


def _tempmaillol_headers():
    """Auth header for tempmail.lol when an API key is configured (optional)."""
    return {"Authorization": TEMPMAILLOL_API_KEY} if TEMPMAILLOL_API_KEY else {}


def create_tempmaillol_inbox():
    """
    Create a temporary inbox via the tempmail.lol API.

    Returns:
        tuple: (email_address, token) or (None, None) on failure
    """
    try:
        # When a specific domain is requested (or a key is set), use the v2 create
        # endpoint which accepts a domain; otherwise use the simple free /generate.
        if TEMPMAILLOL_DOMAIN or TEMPMAILLOL_API_KEY:
            payload = {"domain": TEMPMAILLOL_DOMAIN} if TEMPMAILLOL_DOMAIN else {}
            response = requests.post(
                f"{TEMPMAILLOL_API_BASE}/v2/inbox/create",
                json=payload,
                headers=_tempmaillol_headers(),
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
            address = data.get("address")
            token = data.get("token")
        else:
            # Free endpoint; retry a few times if it rotates onto a blocked domain.
            address = token = None
            for _ in range(5):
                response = requests.get(f"{TEMPMAILLOL_API_BASE}/generate", timeout=15)
                response.raise_for_status()
                data = response.json()
                address = data.get("address")
                token = data.get("token")
                domain = (address or "").split("@")[-1].lower()
                # Match the apex domain (e.g. blocklist "fexpost.com" vs "x.fexpost.com").
                apex = ".".join(domain.split(".")[-2:])
                if domain not in TEMPMAILLOL_BLOCKED_DOMAINS and apex not in TEMPMAILLOL_BLOCKED_DOMAINS:
                    break
                print(f"[*] tempmail.lol gave a blocklisted domain ({domain}); regenerating...")

        if not address or not token:
            raise RuntimeError("tempmail.lol did not return an address/token")

        print(f"[OK] tempmail.lol inbox created: {address}")
        return address, token

    except Exception as exc:
        print(f"[!] Failed to create tempmail.lol inbox: {exc}")
        return None, None


def get_tempmaillol_messages(token):
    """
    Fetch messages from a tempmail.lol inbox and normalize them to the shared shape.

    Each tempmail.lol email has {from, to, subject, body, html, date, ip}; the body is
    returned inline (no separate fetch-by-id call), so the normalized message already
    carries 'text'/'html' for extract_credentials_from_email().
    """
    try:
        response = requests.get(
            f"{TEMPMAILLOL_API_BASE}/auth/{token}",
            headers=_tempmaillol_headers(),
            timeout=15,
        )
        response.raise_for_status()
        emails = response.json().get("email", []) or []

        messages = []
        for idx, item in enumerate(emails):
            html_value = item.get("html")
            raw_from = item.get("from", "") or ""
            # tempmail.lol may include a display name ("Name <addr>"); keep just the addr.
            addr_match = re.search(r"<([^>]+)>", raw_from)
            from_addr = addr_match.group(1).strip() if addr_match else raw_from.strip()
            messages.append({
                "id": str(item.get("date", idx)),
                "subject": item.get("subject", "") or "",
                "from": {"address": from_addr},
                "text": item.get("body", "") or "",
                "html": [html_value] if html_value else [],
            })
        return messages
    except Exception as exc:
        print(f"[!] Failed to fetch tempmail.lol messages: {exc}")
        return []


def _wait_for_credentials_email_tempmaillol(token, max_wait_seconds):
    """Poll the tempmail.lol inbox until the credentials email arrives."""
    print(f"[*] Waiting for credentials email (max {max_wait_seconds}s / {max_wait_seconds//60} minutes)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking tempmail.lol inbox (attempt {attempt}, {remaining}s remaining)...")

        messages = get_tempmaillol_messages(token)

        # Bodies are inline, so fetch_full is the identity function.
        result = _scan_messages_for_credentials(messages, fetch_full=lambda m: m)
        if result:
            return result

        if messages:
            print(f"[*] Found {len(messages)} email(s), but credentials email not yet received")
        else:
            print("[*] Inbox is empty")

        print(f"[*] Waiting {EMAIL_POLL_SECONDS}s before next check...")
        time.sleep(EMAIL_POLL_SECONDS)

    print(f"[!] Timeout: Credentials email not received after {max_wait_seconds}s")
    return None


def _wait_for_credentials_email_mailtm(auth_token, max_wait_seconds):
    """Poll the mail.tm temporary inbox until the credentials email arrives."""
    print(f"[*] Waiting for credentials email (max {max_wait_seconds}s / {max_wait_seconds//60} minutes)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking mail.tm inbox (attempt {attempt}, {remaining}s remaining)...")

        messages = get_mailtm_messages(auth_token)

        result = _scan_messages_for_credentials(
            messages,
            fetch_full=lambda m: get_mailtm_message_by_id(auth_token, m["id"]),
        )
        if result:
            return result

        if messages:
            print(f"[*] Found {len(messages)} email(s), but credentials email not yet received")
        else:
            print("[*] Inbox is empty")

        print(f"[*] Waiting {EMAIL_POLL_SECONDS}s before next check...")
        time.sleep(EMAIL_POLL_SECONDS)

    print(f"[!] Timeout: Credentials email not received after {max_wait_seconds}s")
    return None


# ═══════════════════════════════════════════════════════════
# Email Backend Dispatch
# ═══════════════════════════════════════════════════════════

def create_email_account():
    """Create/allocate a receiving email address using the configured backend.

    Returns a session dict (always includes 'address' and 'backend') or None on failure.
    """
    if IPTVV_EMAIL_BACKEND == "mailtm":
        address, password, auth_token = create_mailtm_account()
        if not address:
            return None
        return {"backend": "mailtm", "address": address, "password": password, "token": auth_token}

    if IPTVV_EMAIL_BACKEND == "tempmaillol":
        address, token = create_tempmaillol_inbox()
        if not address:
            return None
        return {"backend": "tempmaillol", "address": address, "token": token}

    # Default: Gmail IMAP backend (custom-domain catch-all, or plus-alias fallback).
    if not IPTVV_GMAIL_ADDRESS or not IPTVV_GMAIL_APP_PASSWORD:
        print("[!] Gmail email backend requires IPTVV_GMAIL_ADDRESS and IPTVV_GMAIL_APP_PASSWORD")
        return None

    alias = build_gmail_alias()
    scheme = f"catch-all @{IPTVV_EMAIL_DOMAIN}" if IPTVV_EMAIL_DOMAIN else "plus-alias"
    print(f"[*] Generated receiving address ({scheme}): {alias}")

    # Verify IMAP login up front so we fail fast before submitting checkout.
    try:
        conn = _gmail_connect()
        conn.logout()
        print(f"[OK] Gmail IMAP login verified ({IPTVV_GMAIL_ADDRESS})")
    except Exception as exc:
        print(f"[!] Gmail IMAP login failed: {exc}")
        return None

    return {"backend": "gmail", "address": alias}


def wait_for_credentials_email(email_session, max_wait_seconds=EMAIL_MAX_WAIT_SECONDS):
    """Dispatch to the configured backend's polling loop.

    Returns:
        dict: Full message object with 'text'/'html', or None if timeout.
    """
    backend = email_session.get("backend")
    if backend == "mailtm":
        return _wait_for_credentials_email_mailtm(email_session["token"], max_wait_seconds)
    if backend == "tempmaillol":
        return _wait_for_credentials_email_tempmaillol(email_session["token"], max_wait_seconds)
    return _wait_for_credentials_email_gmail(email_session["address"], max_wait_seconds)


# ═══════════════════════════════════════════════════════════
# Selenium Browser Automation
# ═══════════════════════════════════════════════════════════

def get_random_user_agent():
    """Generate a random realistic user agent."""
    user_agents = [
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
        'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36',
    ]
    return random.choice(user_agents)


def build_iptvv_proxy():
    """Parse IPTVV_PROXY_URL into a proxy dict, or None when disabled/unset.

    Returns {'host', 'port', 'user', 'password'} for an authenticated HTTP proxy
    (e.g. Apify residential)."""
    if not USE_IPTVV_PROXY or not IPTVV_PROXY_URL:
        return None
    parsed = urlparse(IPTVV_PROXY_URL)
    if not parsed.hostname or not parsed.port:
        print("[!] USE_IPTVV_PROXY is True but IPTVV_PROXY_URL is malformed")
        return None
    return {
        "host": parsed.hostname,
        "port": str(parsed.port),
        "user": parsed.username or "",
        "password": parsed.password or "",
    }


def create_proxy_auth_extension(host, port, user, password):
    """Build an unpacked Chrome extension that points Chrome at an authenticated
    proxy and answers the proxy Basic-auth challenge automatically.

    Chrome's --proxy-server flag cannot carry user:pass credentials, so for an
    authenticated proxy we inject them via chrome.webRequest.onAuthRequired.
    Returns the path to a temp directory holding the unpacked extension (caller
    is responsible for cleanup)."""
    ext_dir = tempfile.mkdtemp(prefix='iptvv_proxy_ext_')

    manifest = {
        "version": "1.0.0",
        "manifest_version": 2,
        "name": "Proxy Auth",
        "permissions": [
            "proxy",
            "tabs",
            "unlimitedStorage",
            "storage",
            "<all_urls>",
            "webRequest",
            "webRequestBlocking",
        ],
        "background": {"scripts": ["background.js"]},
        "minimum_chrome_version": "22.0.0",
    }

    background_js = """
var config = {
    mode: "fixed_servers",
    rules: {
        singleProxy: {
            scheme: "http",
            host: "%s",
            port: parseInt("%s")
        },
        bypassList: ["localhost"]
    }
};
chrome.proxy.settings.set({value: config, scope: "regular"}, function() {});
function callbackFn(details) {
    return {
        authCredentials: {
            username: "%s",
            password: "%s"
        }
    };
}
chrome.webRequest.onAuthRequired.addListener(
    callbackFn,
    {urls: ["<all_urls>"]},
    ["blocking"]
);
""" % (host, port, user, password)

    with open(os.path.join(ext_dir, 'manifest.json'), 'w') as f:
        json.dump(manifest, f)
    with open(os.path.join(ext_dir, 'background.js'), 'w') as f:
        f.write(background_js)

    return ext_dir


def get_driver():
    """Initialize Chrome WebDriver with anti-detection options (undetected-chromedriver).

    Uses a direct connection on the host's public IP by default. When
    USE_IPTVV_PROXY is enabled, the browser egress is routed through the
    authenticated proxy in IPTVV_PROXY_URL via an injected auth extension.
    """
    headless_mode = os.getenv("HEADLESS", "True").lower() == "true"

    # Use undetected-chromedriver's ChromeOptions
    options = uc.ChromeOptions()

    # Determine proxy first: the MV2 proxy-auth extension does not load reliably
    # under --headless=new, so when a proxy is active and a virtual display
    # (Xvfb DISPLAY=:99 in Docker) is available we run headed against it instead.
    proxy = build_iptvv_proxy()
    proxy_ext_dir = None
    has_display = os.environ.get("DISPLAY") is not None
    use_headless_new = headless_mode and not (proxy and has_display)

    if not headless_mode:
        options.add_argument("--start-maximized")
        print("[*] Running in GUI mode")
    elif use_headless_new:
        options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        print("[*] Running in HEADLESS mode")
        if proxy and not has_display:
            print("[!] Proxy enabled but no DISPLAY found; the proxy-auth "
                  "extension may not load under --headless=new (expect 407).")
    else:
        # Headless requested but proxy needs a real display -> headed via Xvfb.
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1920,1080")
        print(f"[*] Running headed against virtual display "
              f"(DISPLAY={os.environ.get('DISPLAY')}) for proxy-auth extension")

    # Use random user agent for additional anonymity
    random_ua = get_random_user_agent()
    options.add_argument(f"--user-agent={random_ua}")
    print(f"[*] Using User-Agent: {random_ua[:80]}...")

    # Additional anti-detection options
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--ignore-certificate-errors")
    options.add_argument("--disable-logging")
    options.add_argument("--log-level=3")
    options.add_argument("--disable-notifications")

    # Additional preferences to appear more human-like
    prefs = {
        "credentials_enable_service": False,
        "profile.password_manager_enabled": False,
        "profile.default_content_setting_values.notifications": 2,
        "profile.managed_default_content_settings.images": 1,  # Enable images
    }

    if proxy:
        # Authenticated proxy (e.g. Apify residential) -> Chrome's --proxy-server
        # flag can't carry credentials, so inject them via an auth extension.
        proxy_ext_dir = create_proxy_auth_extension(
            proxy["host"], proxy["port"], proxy["user"], proxy["password"]
        )
        options.add_argument(f"--load-extension={proxy_ext_dir}")
        print(f"[*] Using Apify proxy via extension: {proxy['host']}:{proxy['port']} "
              f"(user={proxy['user']})")
    else:
        # Direct connection (no proxy). Explicitly disable any proxy to prevent
        # ERR_NO_SUPPORTED_PROXIES from a leaked system/env proxy setting.
        options.add_argument("--no-proxy-server")
        prefs["proxy"] = {
            "mode": "direct",
            "pac_url": "",
            "bypass_list": ""
        }
        print("[*] Using direct connection (public IP, no proxy)")

    options.add_experimental_option("prefs", prefs)

    # Use undetected-chromedriver (no need for chromedriver path, it manages itself)
    try:
        print("[*] Initializing undetected-chromedriver...")
        driver = uc.Chrome(options=options, use_subprocess=False)
        print("[OK] undetected-chromedriver initialized successfully")
    except Exception as e:
        print(f"[!] Failed to initialize undetected-chromedriver: {e}")
        print("[*] Falling back to regular ChromeDriver...")
        # Fallback to regular webdriver if undetected fails
        chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
        if os.path.exists(chromedriver_path):
            service = Service(chromedriver_path)
        else:
            service = Service(ChromeDriverManager().install())

        regular_options = Options()

        # Copy all arguments from uc.ChromeOptions to regular Options
        for arg in options.arguments:
            regular_options.add_argument(arg)

        # Copy experimental options (prefs)
        if hasattr(options, 'experimental_options'):
            for key, value in options.experimental_options.items():
                regular_options.add_experimental_option(key, value)

        driver = webdriver.Chrome(service=service, options=regular_options)

    # Remember the proxy extension temp dir (if any) so callers can clean it up.
    driver._proxy_ext_dir = proxy_ext_dir

    return driver


def safe_click(driver, el):
    """Safely click an element with fallback to JavaScript click."""
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.4)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)


def find_clickable_by_text(driver, terms, timeout=15):
    """Find a clickable element containing any of the given text terms."""
    terms = [term.lower() for term in terms]
    end = time.time() + timeout
    while time.time() < end:
        candidates = driver.find_elements(
            By.XPATH,
            "//a|//button|//input[@type='submit' or @type='button']|//label|//*[@role='button']",
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
                    ],
                )
            ).lower()
            if any(term in text for term in terms):
                return el
        time.sleep(0.5)
    raise TimeoutError(f"Could not find clickable element containing: {terms}")


def page_text_lower(driver):
    """Return visible body text, lowercased, without failing page checks."""
    try:
        return driver.find_element(By.TAG_NAME, "body").text.lower()
    except Exception:
        return ""


def is_cloudflare_block_page(driver):
    """Detect Cloudflare/WAF pages that replace the real WooCommerce checkout."""
    title = (driver.title or "").lower()
    current_url = (driver.current_url or "").lower()
    body = page_text_lower(driver)
    markers = [
        "attention required",
        "cloudflare",
        "checking your browser",
        "verify you are human",
        "cf-browser-verification",
        "ray id",
        "error 1020",
        "access denied",
    ]
    return (
        "cloudflare" in title
        or "cdn-cgi" in current_url
        or any(marker in body for marker in markers)
    )


def is_browser_error_page(driver):
    """Detect Chrome's own network-error screen (NOT a Cloudflare block).

    Chrome renders these (e.g. ERR_NO_SUPPORTED_PROXIES, DNS failures, timeouts)
    when it never reaches the site at all, so they must not be mistaken for a
    Cloudflare/WAF block or a missing checkout form.
    """
    body = page_text_lower(driver)
    current_url = (driver.current_url or "").lower()
    markers = [
        "this site can't be reached",
        "this site can’t be reached",
        "err_",
        "dns_probe_finished",
        "took too long to respond",
        "your internet access is blocked",
        "no internet",
    ]
    return current_url.startswith("chrome-error://") or any(marker in body for marker in markers)


def save_page_debug_artifacts(driver, label):
    """Save a screenshot and HTML snapshot for production diagnosis."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "page"
    debug_dir = IPTVV_DEBUG_DIR
    artifacts = {}

    # Try to create/access the debug directory, fallback to user-writable locations
    fallback_dirs = [debug_dir, "./logs", os.path.expanduser("~/logs"), "/tmp"]
    debug_dir_accessible = False

    for candidate_dir in fallback_dirs:
        try:
            os.makedirs(candidate_dir, exist_ok=True)
            # Test write access by creating a test file
            test_file = os.path.join(candidate_dir, f".write_test_{os.getpid()}")
            with open(test_file, "w") as f:
                f.write("test")
            os.remove(test_file)
            debug_dir = candidate_dir
            debug_dir_accessible = True
            if candidate_dir != IPTVV_DEBUG_DIR:
                print(f"[*] Using fallback debug directory: {debug_dir}")
            break
        except Exception as exc:
            if candidate_dir == fallback_dirs[-1]:
                print(f"[!] Could not access any debug directory, last error: {exc}")
            continue

    base_path = os.path.join(debug_dir, f"iptvv_{safe_label}_{timestamp}")

    try:
        screenshot_path = f"{base_path}.png"
        driver.save_screenshot(screenshot_path)
        print(f"[*] Screenshot saved to: {screenshot_path}")
        artifacts["screenshot"] = screenshot_path
    except Exception as exc:
        print(f"[!] Could not save screenshot: {exc}")

    try:
        html_path = f"{base_path}.html"
        with open(html_path, "w", encoding="utf-8") as fh:
            fh.write(driver.page_source)
        print(f"[*] HTML snapshot saved to: {html_path}")
        artifacts["html"] = html_path
    except Exception as exc:
        print(f"[!] Could not save HTML snapshot: {exc}")

    return artifacts


def get_public_ip():
    """Best-effort public IP lookup for Cloudflare allowlist/support tickets."""
    try:
        response = requests.get("https://api.ipify.org", timeout=8)
        response.raise_for_status()
        return response.text.strip()
    except Exception as exc:
        print(f"[!] Could not fetch public IP: {exc}")
        return "unknown"


def extract_cloudflare_diagnostics(driver):
    """Extract useful Cloudflare details from the block page."""
    source = driver.page_source or ""
    page_text = html.unescape(re.sub(r"<[^>]+>", " ", source))
    page_text = re.sub(r"\s+", " ", page_text).strip()

    ray_id = "unknown"
    ray_match = re.search(r"Ray ID:?\s*([0-9a-fA-F]{12,})", page_text)
    if ray_match:
        ray_id = ray_match.group(1)

    reason = "Cloudflare/WAF block page"
    reason_patterns = [
        r"You are unable to access[^.]*\.?",
        r"Sorry, you have been blocked\.?",
        r"Attention Required![^.]*\.?",
        r"Access denied\.?",
    ]
    for pattern in reason_patterns:
        match = re.search(pattern, page_text, flags=re.IGNORECASE)
        if match:
            reason = match.group(0).strip()
            break

    return {
        "ray_id": ray_id,
        "reason": reason,
        "url": driver.current_url,
        "title": driver.title,
    }


def get_browser_public_ip(driver):
    """Fetch public IP from inside Chrome to verify VPN/browser egress."""
    original_url = driver.current_url
    try:
        driver.get(IPTVV_PROXY_CHECK_URL)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        browser_ip = driver.find_element(By.TAG_NAME, "body").text.strip()
        browser_ip = re.sub(r"\s+", " ", browser_ip)
        if browser_ip:
            return browser_ip
        return "unknown"
    except Exception as exc:
        print(f"[!] Could not fetch browser public IP from {IPTVV_PROXY_CHECK_URL}: {exc}")
        return "unknown"
    finally:
        if original_url and original_url != "data:,":
            try:
                driver.get(original_url)
            except Exception:
                pass


def preflight_checkout_access():
    """Check whether the current prod egress can reach the real IPTVV checkout."""
    driver = None
    checkout_url = f"{IPTVV_BASE_URL}/checkout/"

    print("\n" + "=" * 60)
    print("IPTVV CANADA - CHECKOUT PREFLIGHT")
    print("=" * 60)
    print(f"[*] Checkout URL: {checkout_url}")
    print(f"[*] Debug directory: {IPTVV_DEBUG_DIR}")
    print("=" * 60 + "\n")

    try:
        server_ip = get_public_ip()
        print(f"[*] Server public IP: {server_ip}")

        driver = get_driver()
        browser_ip = get_browser_public_ip(driver)
        print(f"[*] Browser-visible public IP: {browser_ip}")
        if IPTVV_KNOWN_BLOCKED_IP and browser_ip == IPTVV_KNOWN_BLOCKED_IP:
            print(f"[!] Browser is still using known blocked IPTVV IP: {IPTVV_KNOWN_BLOCKED_IP}")
        if server_ip != "unknown" and browser_ip != "unknown" and server_ip == browser_ip:
            print("[*] Browser IP matches server IP; this is expected for full-system VPN egress.")

        print("[*] Seeding free-trial cart for checkout preflight...")
        driver.get(f"{IPTVV_BASE_URL}/?add-to-cart=7758")
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5)

        print(f"[*] Opening checkout: {checkout_url}")
        driver.get(checkout_url)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5)

        artifacts = save_page_debug_artifacts(driver, "preflight_checkout")
        print(f"[*] Checkout URL after load: {driver.current_url}")
        print(f"[*] Page title: {driver.title}")

        if is_cloudflare_block_page(driver):
            diagnostics = extract_cloudflare_diagnostics(driver)
            print(f"[!] Cloudflare Ray ID: {diagnostics['ray_id']}")
            print(f"[!] Cloudflare reason: {diagnostics['reason']}")
            raise CloudflareBlockedError(
                "Preflight failed: IPTVV checkout is blocked by Cloudflare/WAF. "
                f"Server IP: {server_ip}; browser IP: {browser_ip}; "
                f"Cloudflare Ray ID: {diagnostics['ray_id']}; "
                f"debug HTML: {artifacts.get('html', 'not saved')}; "
                f"screenshot: {artifacts.get('screenshot', 'not saved')}."
            )

        if is_browser_error_page(driver):
            print("[!] Chrome rendered a network-error page (NOT a Cloudflare block)")
            raise RuntimeError(
                "Preflight failed: network/connectivity error - Chrome never reached IPTVV "
                "(this is NOT a Cloudflare IP block; check DNS/egress/leftover proxy settings). "
                f"Server IP: {server_ip}; browser IP: {browser_ip}; "
                f"Current URL: {driver.current_url}; title: {driver.title}; "
                f"debug HTML: {artifacts.get('html', 'not saved')}; "
                f"screenshot: {artifacts.get('screenshot', 'not saved')}."
            )

        try:
            driver.find_element(By.ID, "billing_email")
        except Exception:
            raise RuntimeError(
                "Preflight failed: no Cloudflare block and no network error detected, "
                "but the checkout billing_email field is missing (unexpected page layout). "
                f"Server IP: {server_ip}; browser IP: {browser_ip}; "
                f"Current URL: {driver.current_url}; title: {driver.title}; "
                f"debug HTML: {artifacts.get('html', 'not saved')}; "
                f"screenshot: {artifacts.get('screenshot', 'not saved')}."
            )

        print("[OK] Preflight passed: IPTVV checkout form is reachable.")
        print(f"[OK] Server IP: {server_ip}")
        print(f"[OK] Browser-visible IP: {browser_ip}")
        print(f"[OK] Debug HTML: {artifacts.get('html', 'not saved')}")
        print(f"[OK] Screenshot: {artifacts.get('screenshot', 'not saved')}")
        return True

    finally:
        if driver:
            try:
                driver.quit()
                print("[*] Browser closed")
            except Exception:
                pass


def wait_for_real_checkout_page(driver, context, timeout=30):
    """Wait until the WooCommerce checkout appears, or fail on a blocker page."""
    print(f"[*] Verifying checkout page after {context}...")
    deadline = time.time() + timeout
    cloudflare_seen = False
    reload_attempts = 0

    while time.time() < deadline:
        if is_cloudflare_block_page(driver):
            cloudflare_seen = True
            print("[!] Cloudflare/WAF page detected instead of checkout; waiting for clearance...")
            if reload_attempts < IPTVV_PAGE_LOAD_RETRIES:
                reload_attempts += 1
                print(f"[*] Reloading page after blocker detection ({reload_attempts}/{IPTVV_PAGE_LOAD_RETRIES})...")
                driver.refresh()
            time.sleep(5)
            continue

        try:
            driver.find_element(By.ID, "billing_email")
            print("[OK] Billing email field detected - checkout form is loaded")
            return True
        except Exception:
            time.sleep(1)

    if cloudflare_seen or is_cloudflare_block_page(driver):
        artifacts = save_page_debug_artifacts(driver, "cloudflare_block")
        diagnostics = extract_cloudflare_diagnostics(driver)
        public_ip = get_public_ip()
        print(f"[!] Cloudflare Ray ID: {diagnostics['ray_id']}")
        print(f"[!] Cloudflare reason: {diagnostics['reason']}")
        print(f"[!] Production public IP: {public_ip}")
        raise CloudflareBlockedError(
            "IPTVV checkout is blocked by Cloudflare/WAF in this production container. "
            "The checkout form never loaded. "
            f"Public IP: {public_ip}; Cloudflare Ray ID: {diagnostics['ray_id']}; "
            f"debug HTML: {artifacts.get('html', 'not saved')}; "
            f"screenshot: {artifacts.get('screenshot', 'not saved')}. "
            "Ask IPTVV/Cloudflare to allowlist this server or provide an approved API/integration path."
        )

    artifacts = save_page_debug_artifacts(driver, "checkout_not_loaded")
    raise RuntimeError(
        f"IPTVV checkout form did not load after {context}. "
        f"Current URL: {driver.current_url}; title: {driver.title}; "
        f"debug HTML: {artifacts.get('html', 'not saved')}; "
        f"screenshot: {artifacts.get('screenshot', 'not saved')}"
    )


def generate_random_user_data():
    """Generate random user data for checkout form."""
    first_names = ["John", "Jane", "Mike", "Sarah", "David", "Emma", "Chris", "Lisa", "Tom", "Amy"]
    last_names = ["Smith", "Johnson", "Brown", "Davis", "Wilson", "Moore", "Taylor", "Anderson"]

    first = random.choice(first_names)
    last = random.choice(last_names)

    # Generate valid Canadian phone number (area codes: 416, 514, 604, 403, 613)
    area_codes = ["416", "514", "604", "403", "613", "647", "438", "778", "587", "343"]
    area_code = random.choice(area_codes)
    exchange = f"{random.randint(200, 999)}"  # Central office code
    line = f"{random.randint(1000, 9999)}"    # Line number
    phone = f"({area_code}) {exchange}-{line}"  # Format: (416) 555-1234

    return {
        "first_name": first,
        "last_name": last,
        "phone": phone,
        "address": f"{random.randint(100, 9999)} {random.choice(['Main', 'Oak', 'Maple', 'Cedar'])} St",
        "city": random.choice(["Toronto", "Montreal", "Vancouver", "Calgary", "Ottawa"]),
        "postal_code": f"{random.choice('ABCEGHJKLMNPRSTVXY')}{random.randint(0, 9)}{random.choice('ABCEGHJKLMNPRSTVWXYZ')} {random.randint(0, 9)}{random.choice('ABCEGHJKLMNPRSTVWXYZ')}{random.randint(0, 9)}",
        "country": "Canada",
    }


# ═══════════════════════════════════════════════════════════
# IPTVV.ca Automation Functions
# ═══════════════════════════════════════════════════════════

def navigate_to_cart_and_get_free_trial(driver):
    """Navigate to cart and add free trial product to cart (WooCommerce flow)."""
    print(f"[*] Navigating to IPTVV cart: {IPTVV_CART_URL}")
    driver.get(IPTVV_CART_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    print(f"[*] Current URL: {driver.current_url}")

    # Look for "Get Free Trial" link/button and click it to add product to cart
    print("[*] Looking for 'Get Free Trial' link...")
    try:
        trial_button = find_clickable_by_text(
            driver,
            ["get free trial", "free trial", "start free trial", "trial"],
            timeout=10
        )
        print(f"[OK] Found element: {trial_button.text}")
        safe_click(driver, trial_button)
        print("[OK] Clicked 'Get Free Trial' - adding product to cart...")

        # Wait for product to be added to cart (WooCommerce usually redirects or shows confirmation)
        time.sleep(5)
        print(f"[*] After click URL: {driver.current_url}")

        # Now navigate to checkout page
        checkout_url = f"{IPTVV_BASE_URL}/checkout/"
        print(f"[*] Navigating to checkout: {checkout_url}")
        driver.get(checkout_url)
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(5)

        # Wait for document to be fully ready
        for i in range(10):
            ready_state = driver.execute_script("return document.readyState")
            if ready_state == "complete":
                break
            time.sleep(1)

        print(f"[*] Checkout URL: {driver.current_url}")
        print(f"[*] Page title: {driver.title}")
        wait_for_real_checkout_page(driver, "free-trial cart flow", timeout=IPTVV_CLOUDFLARE_WAIT_SECONDS)

        # Verify we're on checkout page
        if "checkout" not in driver.current_url.lower():
            print("[!] WARNING: Not on checkout page after navigation")
            # Try alternative: look for "View Cart" or "Proceed to Checkout" button
            try:
                checkout_btn = find_clickable_by_text(driver, ["proceed to checkout", "checkout", "view cart"], timeout=10)
                safe_click(driver, checkout_btn)
                time.sleep(3)
                print(f"[*] After clicking checkout button: {driver.current_url}")
                wait_for_real_checkout_page(driver, "checkout button click", timeout=IPTVV_CLOUDFLARE_WAIT_SECONDS)
            except:
                pass

    except TimeoutError:
        print("[!] 'Get Free Trial' link not found")
        # Try direct URL for adding product to cart
        print("[*] Trying direct add-to-cart URL...")
        driver.get(f"{IPTVV_BASE_URL}/?add-to-cart=7758")
        time.sleep(8)  # Wait longer for product to be added
        print(f"[*] After add-to-cart URL: {driver.current_url}")

        # Navigate to checkout
        checkout_url = f"{IPTVV_BASE_URL}/checkout/"
        print(f"[*] Navigating to checkout: {checkout_url}")
        driver.get(checkout_url)

        # Wait for page to fully load including JavaScript
        WebDriverWait(driver, 30).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(8)  # Extra time for WooCommerce JavaScript to initialize

        # Wait for document to be fully ready
        for i in range(10):
            ready_state = driver.execute_script("return document.readyState")
            if ready_state == "complete":
                break
            time.sleep(1)

        print(f"[*] Checkout URL: {driver.current_url}")
        print(f"[*] Page title: {driver.title}")
        wait_for_real_checkout_page(driver, "direct add-to-cart flow", timeout=IPTVV_CLOUDFLARE_WAIT_SECONDS)


def select_full_channel_package(driver):
    """Select 'full channel' option from dropdown or radio buttons."""
    print("[*] Looking for 'full channel' option...")

    # Strategy 1: Check dropdowns/select elements
    for select_el in driver.find_elements(By.TAG_NAME, "select"):
        select = Select(select_el)
        options_text = " ".join([opt.text.lower() for opt in select.options])

        if "channel" in options_text or "package" in options_text or "plan" in options_text:
            print(f"[*] Found dropdown with channel options")
            for option in select.options:
                if "full" in option.text.lower() and "channel" in option.text.lower():
                    select.select_by_visible_text(option.text)
                    print(f"[OK] Selected: {option.text}")
                    return True
            # If no exact match, select the most comprehensive option (usually last or contains 'all')
            for option in select.options:
                if "all" in option.text.lower() or "full" in option.text.lower():
                    select.select_by_visible_text(option.text)
                    print(f"[OK] Selected: {option.text}")
                    return True

    # Strategy 2: Check radio buttons
    radio_buttons = driver.find_elements(By.XPATH, "//input[@type='radio']")
    for radio in radio_buttons:
        label_text = ""
        try:
            # Try to find associated label
            radio_id = radio.get_attribute("id")
            if radio_id:
                label = driver.find_element(By.XPATH, f"//label[@for='{radio_id}']")
                label_text = label.text.lower()
        except:
            # Try parent element text
            label_text = radio.find_element(By.XPATH, "./parent::*").text.lower()

        if "full" in label_text and "channel" in label_text:
            safe_click(driver, radio)
            print(f"[OK] Selected radio button: {label_text}")
            return True

    # Strategy 3: Check checkboxes
    checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
    for checkbox in checkboxes:
        label_text = ""
        try:
            checkbox_id = checkbox.get_attribute("id")
            if checkbox_id:
                label = driver.find_element(By.XPATH, f"//label[@for='{checkbox_id}']")
                label_text = label.text.lower()
        except:
            label_text = checkbox.find_element(By.XPATH, "./parent::*").text.lower()

        if "full" in label_text and "channel" in label_text:
            if not checkbox.is_selected():
                safe_click(driver, checkbox)
            print(f"[OK] Checked: {label_text}")
            return True

    print("[*] Could not find 'full channel' option - may not be required or already selected")
    return False


def fill_checkout_form(driver, email_address):
    """Fill WooCommerce checkout form with generated data and mail.tm email."""
    print("[*] Filling WooCommerce checkout form...")

    wait_for_real_checkout_page(driver, "form fill step", timeout=IPTVV_CLOUDFLARE_WAIT_SECONDS)
    time.sleep(2)  # Extra time for all form fields to render

    user_data = generate_random_user_data()
    user_data["email"] = email_address

    # WooCommerce standard billing field names
    field_mappings = {
        "email": ["billing_email", "email"],
        "first_name": ["billing_first_name", "firstname", "first_name"],
        "last_name": ["billing_last_name", "lastname", "last_name"],
        "phone": ["billing_phone", "phone"],
        "address": ["billing_address_1", "address", "address1"],
        "city": ["billing_city", "city"],
        "postal_code": ["billing_postcode", "postal", "postcode", "zip"],
        "country": ["billing_country", "country"],
    }

    # Try to fill each field
    filled_fields = []
    for data_key, field_names in field_mappings.items():
        value = user_data.get(data_key, "")
        if not value:
            continue

        filled = False
        for field_name in field_names:
            # Try by ID first (WooCommerce uses IDs), then name
            for by_type in [By.ID, By.NAME]:
                try:
                    field = driver.find_element(by_type, field_name)
                    if field.is_displayed() and field.is_enabled():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", field)
                        time.sleep(0.3)
                        field.clear()
                        field.send_keys(value)
                        print(f"[OK] Filled {field_name}: {value if data_key != 'email' else email_address}")
                        filled = True
                        filled_fields.append(field_name)
                        break
                except:
                    pass
            if filled:
                break

        if not filled:
            print(f"[!] Could not find field for: {data_key}")

    # Handle country dropdown (WooCommerce uses select2 sometimes)
    try:
        # Try standard select
        country_select = driver.find_element(By.ID, "billing_country")
        select = Select(country_select)
        for option in select.options:
            if "ca" == option.get_attribute("value").lower() or "canada" in option.text.lower():
                select.select_by_value(option.get_attribute("value"))
                print(f"[OK] Selected country: Canada")
                filled_fields.append("billing_country")
                break
    except:
        # Try by name if ID doesn't work
        try:
            country_select = driver.find_element(By.NAME, "billing_country")
            select = Select(country_select)
            select.select_by_value("CA")
            print(f"[OK] Selected country: CA")
            filled_fields.append("billing_country")
        except:
            print("[!] Could not find country dropdown")

    # WooCommerce often has a state/province field
    try:
        state_select = driver.find_element(By.ID, "billing_state")
        select = Select(state_select)
        # Select Ontario (ON) as default
        select.select_by_value("ON")
        print(f"[OK] Selected state: Ontario")
        filled_fields.append("billing_state")
    except:
        pass

    # IPTVV.ca CUSTOM REQUIRED FIELDS
    # The checkout flags every UNCHECKED box in device_select[] and
    # channels_select[] as invalid (an earlier run that ticked just one box per
    # group was rejected with the other 9 device + 1 channel boxes still listed
    # as required). So we must tick *all* checkboxes in both groups.
    for group_name in ["device_select[]", "channels_select[]"]:
        print(f"[*] Selecting all '{group_name}' checkboxes...")
        try:
            checkboxes = driver.find_elements(By.NAME, group_name)
            if not checkboxes:
                print(f"[!] No checkboxes found with name='{group_name}'")
                continue
            checked = 0
            for checkbox in checkboxes:
                try:
                    if not checkbox.is_selected():
                        # Set checked + fire change so both the native POST and any
                        # client-side validation see every box as selected.
                        driver.execute_script(
                            "arguments[0].scrollIntoView({block:'center'});"
                            "arguments[0].checked = true;"
                            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));"
                            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));",
                            checkbox,
                        )
                    checked += 1
                except Exception as exc:
                    print(f"[!] Could not check a '{group_name}' box: {exc}")
            print(f"[OK] Selected {checked}/{len(checkboxes)} '{group_name}' checkboxes")
            filled_fields.append(group_name)
        except Exception as exc:
            print(f"[!] '{group_name}' selection error: {exc}")

    # Ticking the "Other (specify)" device box makes its companion text field
    # (device_other) required; an empty one is rejected server-side with
    # "Please specify your device." Set it via JS so it works even while the
    # field is still hidden by the conditional-logic script.
    try:
        device_other = driver.find_element(By.ID, "device_other")
        driver.execute_script(
            "arguments[0].value = arguments[1];"
            "arguments[0].dispatchEvent(new Event('input', {bubbles:true}));"
            "arguments[0].dispatchEvent(new Event('change', {bubbles:true}));",
            device_other, "Smart TV",
        )
        print("[OK] Filled device_other: Smart TV")
        filled_fields.append("device_other")
    except Exception as exc:
        print(f"[!] Could not fill device_other: {exc}")

    print(f"[OK] Filled {len(filled_fields)} fields: {', '.join(filled_fields)}")
    print(f"[*] Using email: {email_address}")
    return user_data


def solve_recaptcha_v2(driver, timeout=120, max_retries=2):
    """Solve reCAPTCHA v2 using 2captcha service."""
    if not solver:
        print("[*] 2captcha solver not configured; checking if CAPTCHA exists...")
        has_captcha = any("recaptcha" in (iframe.get_attribute("src") or "")
                         for iframe in driver.find_elements(By.TAG_NAME, "iframe"))
        if has_captcha:
            print("[!] reCAPTCHA detected but TWOCAPTCHA_API_KEY not set")
            if os.getenv("HEADLESS", "True").lower() != "true":
                input("[*] Please solve the CAPTCHA manually in the browser, then press ENTER here...")
                return True
            return False
        return True

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


def submit_checkout_form(driver):
    """Submit the WooCommerce checkout form."""
    print("[*] Submitting WooCommerce checkout form...")
    wait_for_real_checkout_page(driver, "submit step", timeout=IPTVV_CLOUDFLARE_WAIT_SECONDS)

    # Solve CAPTCHA if present
    if not solve_recaptcha_v2(driver):
        raise RuntimeError("Failed to solve reCAPTCHA")

    # WooCommerce usually has a button with ID "place_order"
    submit_btn = None
    try:
        # Try WooCommerce standard button ID
        submit_btn = driver.find_element(By.ID, "place_order")
        print(f"[OK] Found WooCommerce place_order button")
    except:
        # Fallback to text search
        try:
            submit_btn = find_clickable_by_text(
                driver,
                ["place order", "place trial order", "submit", "checkout", "complete order"],
                timeout=15
            )
            print(f"[OK] Found submit button: {submit_btn.text}")
        except TimeoutError:
            print("[!] Could not find submit button")

    if submit_btn:
        # Scroll to button
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", submit_btn)
        time.sleep(1)

        # Click submit
        safe_click(driver, submit_btn)
        print("[OK] Submit button clicked")

        # Wait for processing/redirect (WooCommerce shows processing animation)
        print("[*] Waiting for order processing...")
        time.sleep(10)  # WooCommerce needs more time to process
        print(f"[*] After submit URL: {driver.current_url}")

        # Check if we got redirected to thank you / order received page
        if "order-received" in driver.current_url or "thank-you" in driver.current_url:
            print("[OK] Order successfully submitted! (on thank you page)")
        elif "checkout" in driver.current_url:
            # Still on checkout - might be validation errors
            print("[!] WARNING: Still on checkout page - order submission failed")

            # Save screenshot for debugging
            try:
                screenshot_path = "/tmp/iptvv_checkout_error.png"
                driver.save_screenshot(screenshot_path)
                print(f"[*] Screenshot saved to: {screenshot_path}")
            except:
                pass

            # Try to find error messages
            try:
                error_msg = driver.find_element(By.CSS_SELECTOR, ".woocommerce-error, .woocommerce-NoticeGroup-checkout, .woocommerce-notices-wrapper")
                print(f"[!] Checkout validation error: {error_msg.text}")
                # Print full page errors for debugging
                all_errors = driver.find_elements(By.CSS_SELECTOR, ".woocommerce-error li, .woocommerce-NoticeGroup-checkout li")
                if all_errors:
                    for i, err in enumerate(all_errors, 1):
                        print(f"    Error {i}: {err.text}")
            except:
                print("[*] No error message found via CSS selectors")

            # Check for inline field errors
            try:
                field_errors = driver.find_elements(By.CSS_SELECTOR, ".woocommerce-invalid-required-field, [aria-invalid='true']")
                if field_errors:
                    print(f"[!] Found {len(field_errors)} invalid/required fields:")
                    for field in field_errors:
                        field_name = field.get_attribute("name") or field.get_attribute("id") or "unknown"
                        print(f"    - {field_name}")
            except:
                pass

            # Print page source snippet around errors
            try:
                page_text = driver.find_element(By.TAG_NAME, "body").text
                if "error" in page_text.lower():
                    print("[*] Page contains 'error' keyword - checking...")
                    lines = page_text.split("\n")
                    for i, line in enumerate(lines):
                        if "error" in line.lower() or "required" in line.lower() or "invalid" in line.lower():
                            context_start = max(0, i-1)
                            context_end = min(len(lines), i+2)
                            print(f"[!] Error context: {' '.join(lines[context_start:context_end])}")
                            break
            except:
                pass
    else:
        raise RuntimeError("Could not find or click submit button")


# ═══════════════════════════════════════════════════════════
# Credential Extraction
# ═══════════════════════════════════════════════════════════

def extract_credentials_from_email(message):
    """
    Extract username, password, and hostname from mail.tm message.

    Args:
        message: Full message object from mail.tm API with 'text' and 'html' fields

    Returns:
        tuple: (username, password, hostname) or (None, None, None) if extraction fails
    """
    # Get both text and HTML versions
    text_content = message.get("text", "")
    html_content = message.get("html", [])

    # Combine for comprehensive search
    if isinstance(html_content, list):
        html_content = " ".join(html_content)

    combined_content = f"{text_content}\n\n{html_content}"

    # Unescape HTML entities
    for _ in range(3):
        unescaped = html.unescape(combined_content)
        if unescaped == combined_content:
            break
        combined_content = unescaped

    # Clean HTML tags
    normalized = re.sub(r"<br\s*/?>", "\n", combined_content, flags=re.I)
    normalized = re.sub(r"</p\s*>", "\n", normalized, flags=re.I)
    normalized = re.sub(r"<[^>]+>", " ", normalized)
    normalized = re.sub(r"[ \t]+", " ", normalized)

    print("[*] Extracting credentials from email...")
    print("[*] Email preview:")
    print(normalized[:500])

    # Extraction patterns based on the example email format
    username = None
    password = None
    hostname = None

    # Username patterns
    username_patterns = [
        r"Username\s*:?\s*([A-Z0-9]{10,})",  # GABSSZY5RS format
        r"Username\s*:?\s*([^\s\n<]+)",
        r"User\s*:?\s*([^\s\n<]+)",
    ]

    for pattern in username_patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            username = match.group(1).strip()
            print(f"[*] Found username: {username}")
            break

    # Password patterns
    password_patterns = [
        r"Password\s*:?\s*(\d{8,})",  # 49180341 format
        r"Password\s*:?\s*([^\s\n<]+)",
        r"Pass\s*:?\s*([^\s\n<]+)",
    ]

    for pattern in password_patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            password = match.group(1).strip()
            print(f"[*] Found password: {_redact(password)}")
            break

    # Hostname/Server Address patterns
    hostname_patterns = [
        r"Server Address[^:]*:?\s*(https?://[^\s<>'\"]+)",
        r"Playlist Host[^:]*:?\s*(https?://[^\s<>'\"]+)",
        r"Host[^:]*:?\s*(https?://[^\s<>'\"]+)",
        r"Server[^:]*:?\s*(https?://[^\s<>'\"]+)",
    ]

    for pattern in hostname_patterns:
        match = re.search(pattern, normalized, re.I)
        if match:
            hostname = match.group(1).strip().rstrip(".,)")
            print(f"[*] Found hostname: {hostname}")
            break

    # Fallback: find any URL that's not iptvv.ca or common services
    if not hostname:
        urls = re.findall(r"https?://[^\s<>'\"]+", normalized)
        for url in urls:
            lowered = url.lower()
            if "iptvv.ca" not in lowered and "mail.tm" not in lowered:
                hostname = url.rstrip(".,)")
                print(f"[*] Found hostname (fallback): {hostname}")
                break

    return username, password, hostname


def save_to_iboplayer(username, password, hostname, max_retries=3):
    """
    Save IPTVV playlist to IBO Player using their API.

    Args:
        username: IPTVV username
        password: IPTVV password
        hostname: IPTVV server hostname/URL
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        bool: True if successful, False otherwise
    """
    if not IPTVV_IBOPLAYER_ENABLED:
        print("[*] IBO Player integration is disabled (IPTVV_IBOPLAYER_ENABLED=False)")
        return False

    if not IPTVV_IBOPLAYER_COOKIE or not IPTVV_IBOPLAYER_PLAYLIST_URL_ID:
        print("[!] IBO Player integration enabled but missing required credentials:")
        print(f"    - IPTVV_IBOPLAYER_COOKIE: {'Set' if IPTVV_IBOPLAYER_COOKIE else 'Missing'}")
        print(f"    - IPTVV_IBOPLAYER_PLAYLIST_URL_ID: {'Set' if IPTVV_IBOPLAYER_PLAYLIST_URL_ID else 'Missing'}")
        return False

    if not hostname:
        print("[!] Cannot save to IBO Player: missing hostname/server URL from credentials")
        return False

    api_url = "https://iboplayer.com/frontend/device/savePlaylist"

    headers = {
        "Content-Type": "application/json",
        "Cookie": IPTVV_IBOPLAYER_COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    }

    # Construct the playlist URL using the hostname from credentials
    playlist_url = hostname.rstrip("/")

    payload = {
        "current_playlist_url_id": IPTVV_IBOPLAYER_PLAYLIST_URL_ID,
        "password": password,
        "pin": "",
        "playlist_name": IPTVV_IBOPLAYER_PLAYLIST_NAME,
        "playlist_type": "xc",  # Xtream Codes format
        "playlist_url": playlist_url,
        "protect": "false",
        "username": username,
        "xml_url": ""
    }

    print("\n" + "=" * 60)
    print("[*] Saving playlist to IBO Player...")
    print("=" * 60)
    print(f"[*] API URL: {api_url}")
    print(f"[*] Playlist Name: {IPTVV_IBOPLAYER_PLAYLIST_NAME}")
    print(f"[*] Playlist URL: {playlist_url}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {_redact(password)}")
    print("=" * 60)

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code == 200:
                body = response.text or ""
                content_type = response.headers.get("Content-Type", "").lower()
                try:
                    response_data = response.json()
                except ValueError:
                    response_data = None

                # An expired session cookie is bounced to a login page, which the
                # endpoint returns as HTML with a 200 status. Treat that as failure.
                head = body[:200].lower()
                if response_data is None and ("text/html" in content_type
                                              or "<html" in head or "login" in head):
                    print("[!] IBO Player returned an HTML/login page on a 200 response.")
                    print("[!] The IPTVV_IBOPLAYER_COOKIE has likely expired - refresh it.")
                    return False

                # If the body is JSON, honour an explicit failure flag.
                if isinstance(response_data, dict):
                    status_val = response_data.get("status", response_data.get("success"))
                    if status_val in (False, 0, "0", "false", "error"):
                        print(f"[!] IBO Player reported failure in a 200 response: {response_data}")
                        return False
                    print(f"[*] IBO Player response: {response_data}")
                else:
                    print(f"[*] IBO Player response (non-JSON): {body[:200]}")

                print(f"[OK] Playlist saved to IBO Player successfully!")
                return True

            elif 400 <= response.status_code < 500:
                # Client error - don't retry, configuration issue
                print(f"[!] IBO Player API error {response.status_code}: {response.text[:200]}")
                print(f"[!] This is a configuration error - please check your IBO Player credentials")
                return False

            else:
                # Server error - retry with exponential backoff
                print(f"[!] IBO Player API error {response.status_code} (attempt {attempt}/{max_retries})")
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # Exponential backoff: 2s, 4s, 8s
                    print(f"[*] Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)

        except requests.exceptions.Timeout:
            print(f"[!] IBO Player API timeout (attempt {attempt}/{max_retries})")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

        except Exception as e:
            print(f"[!] IBO Player API exception (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)

    print(f"[!] Failed to save playlist to IBO Player after {max_retries} attempts")
    return False


# ═══════════════════════════════════════════════════════════
# Webhook & Notification Integration
# ═══════════════════════════════════════════════════════════

def send_webhook_callback(callback_url, user_id, status, username=None, password=None, host=None, m3u_url=None, error=None, max_retries=3):
    """Send webhook callback to Laravel backend."""
    if not callback_url:
        print("[*] No callback URL provided, skipping webhook")
        return False

    payload = {
        "user_id": user_id,
        "status": status,
        "timestamp": datetime.now().isoformat(),
    }
    if status == "success":
        payload.update({
            "username": username,
            "password": password,
            "host": host,
            "m3u_url": m3u_url
        })
    else:
        payload["error"] = error

    headers = {"Content-Type": "application/json", "User-Agent": "IPTVV-Canada-Automation/1.0"}
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


def send_telegram_notification(status, message, details=None):
    """Send Telegram notification using the notifier module."""
    try:
        if status == "success":
            notifier.notify_success(message, details, None)
        else:
            notifier.notify_error(message, details, None)
    except Exception as exc:
        print(f"[!] Telegram notification failed: {exc}")


# ═══════════════════════════════════════════════════════════
# Main Automation Flow
# ═══════════════════════════════════════════════════════════

def main(user_id=None, callback_url=None):
    """Main automation workflow."""
    driver = None
    is_laravel_mode = bool(user_id and callback_url)

    print("\n" + "=" * 60)
    print("IPTVV CANADA - AUTOMATED TRIAL CREATION")
    print("=" * 60)
    print(f"[*] User ID: {user_id if user_id else 'N/A'}")
    print(f"[*] Callback URL: {callback_url if callback_url else 'N/A'}")
    print(f"[*] Laravel integration mode: {is_laravel_mode}")
    print("=" * 60 + "\n")

    try:
        print("\n[*] Creating trial using public IP (direct connection)")
        print("=" * 60)

        # Step 1: Initialize browser and confirm IPTVV checkout is reachable.
        driver = get_driver()

        # Step 2: Navigate to cart and start trial process.
        navigate_to_cart_and_get_free_trial(driver)

        # Step 3: Select full channel package.
        select_full_channel_package(driver)

        # Step 4: Allocate the receiving email only after the checkout form is reachable.
        email_session = create_email_account()
        if not email_session:
            raise RuntimeError("Failed to allocate a receiving email account")
        email_address = email_session["address"]

        # Step 5: Fill checkout form with the receiving email
        fill_checkout_form(driver, email_address)

        # Step 6: Submit form
        submit_checkout_form(driver)

        # Step 7: Wait for credentials email (this can take 5-45 minutes)
        print("\n" + "=" * 60)
        print(f"[*] Order submitted! Monitoring inbox for: {email_address}")
        print("=" * 60 + "\n")

        credentials_message = wait_for_credentials_email(email_session)
        if not credentials_message:
            raise RuntimeError(f"Timeout: Credentials email not received after {EMAIL_MAX_WAIT_SECONDS} seconds")

        # Step 8: Extract credentials from email
        username, password, hostname = extract_credentials_from_email(credentials_message)

        if not username or not password or not hostname:
            raise RuntimeError("Failed to extract complete credentials from email")

        # Construct M3U URL
        m3u_url = f"{hostname}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

        # Success!
        print("\n" + "=" * 60)
        print("\u2713 IPTVV CANADA CREDENTIALS EXTRACTED SUCCESSFULLY")
        print("=" * 60)
        print(f"[*] Server Address: {hostname}")
        print(f"[*] Username: {username}")
        print(f"[*] Password: {password}")
        print(f"[*] M3U URL: {m3u_url}")
        print("=" * 60 + "\n")

        # Save to IBO Player if enabled
        save_to_iboplayer(username, password, hostname)

        # Send success notifications
        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="success",
                username=username,
                password=password,
                host=hostname,
                m3u_url=m3u_url
            )

        send_telegram_notification(
            "success",
            f"IPTVV Canada trial created for {email_address}",
            f"Username: {username}\nHost: {hostname}"
        )

        print("[OK] IPTVV Canada automation complete")

    except (CloudflareBlockedError, TrialRejectedError) as exc:
        # No proxy fallback configured - report on the public IP result and exit.
        print(f"\n[!] IPTVV Canada automation failed: {exc}")

        if driver:
            try:
                driver.quit()
                driver = None
            except:
                pass

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="failed",
                error=str(exc)
            )
        send_telegram_notification("error", type(exc).__name__, str(exc))
        raise SystemExit(1)

    except Exception as exc:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"\n[!] IPTVV Canada automation failed: {exc}")
        print(error_traceback)

        if driver:
            try:
                driver.quit()
                driver = None
            except:
                pass

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="failed",
                error=f"{exc}\n\n{error_traceback}"
            )

        send_telegram_notification("error", str(exc), error_traceback)
        raise SystemExit(1)

    finally:
        if driver and AUTO_EXIT:
            try:
                driver.quit()
                print("[*] Browser closed")
            except:
                pass
        elif driver:
            print("[*] AUTO_EXIT disabled; browser will remain open")
            while True:
                time.sleep(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="IPTVV Canada - Automated Trial Account Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python iptvvcanada_automation.py
  python iptvvcanada_automation.py --preflight-only
  python iptvvcanada_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/iptvv-automation
        """,
    )
    parser.add_argument(
        "--preflight-only",
        action="store_true",
        help="Only verify IPTVV checkout reachability and browser egress; do not create a trial",
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    args = parser.parse_args()

    if args.preflight_only:
        try:
            preflight_checkout_access()
        except Exception as exc:
            import traceback
            error_traceback = traceback.format_exc()
            print(f"\n[!] IPTVV Canada preflight failed: {exc}")
            print(error_traceback)
            raise SystemExit(1)
    else:
        main(user_id=args.user_id, callback_url=args.callback_url)
