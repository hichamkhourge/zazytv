"""
IPTVV Canada - Automated Trial Account Creation

Automates the IPTVV.ca cart checkout flow using temporary mail.tm emails
and extracts Xtream credentials from the received email.

Install deps: pip install selenium webdriver-manager 2captcha-python python-dotenv requests
"""

import argparse
import html
import json
import os
import random
import re
import socket
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
IPTVV_BASE_URL = os.getenv("IPTVV_BASE_URL", "https://iptvv.ca").rstrip("/")
IPTVV_CART_URL = os.getenv("IPTVV_CART_URL", f"{IPTVV_BASE_URL}/cart/")
MAILTM_API_BASE = os.getenv("MAILTM_API_BASE", "https://api.mail.tm")
EMAIL_POLL_SECONDS = int(os.getenv("IPTVV_EMAIL_POLL_SECONDS", "30"))
EMAIL_MAX_WAIT_SECONDS = int(os.getenv("IPTVV_EMAIL_MAX_WAIT_SECONDS", "2700"))  # 45 minutes
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"

# Tor configuration
USE_TOR = os.getenv("USE_TOR", "False").lower() == "true"
TOR_SOCKS_HOST = os.getenv("TOR_SOCKS_HOST", "127.0.0.1")
TOR_SOCKS_PORT = int(os.getenv("TOR_SOCKS_PORT", "9050"))
TOR_CONTROL_HOST = os.getenv("TOR_CONTROL_HOST", "127.0.0.1")
TOR_CONTROL_PORT = int(os.getenv("TOR_CONTROL_PORT", "9051"))
TOR_CONTROL_PASSWORD = os.getenv("TOR_CONTROL_PASSWORD", "")

CREDENTIALS_EMAIL_SUBJECT = "Your trial is now active"
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY else None


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


def wait_for_credentials_email(auth_token, max_wait_seconds=EMAIL_MAX_WAIT_SECONDS):
    """
    Poll mail.tm inbox until credentials email arrives.

    Args:
        auth_token: Bearer token for authentication
        max_wait_seconds: Maximum time to wait (default: 2700 seconds / 45 minutes)

    Returns:
        dict: Full message object with credentials, or None if timeout
    """
    print(f"[*] Waiting for credentials email (max {max_wait_seconds}s / {max_wait_seconds//60} minutes)...")
    deadline = time.time() + max_wait_seconds
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        remaining = int(deadline - time.time())
        print(f"[*] Checking mail.tm inbox (attempt {attempt}, {remaining}s remaining)...")

        messages = get_mailtm_messages(auth_token)

        # Look for email from IPTVV Canada with credentials
        for msg in messages:
            subject = msg.get("subject", "")
            from_addr = msg.get("from", {}).get("address", "")

            print(f"    - From: {from_addr}, Subject: {subject}")

            # Check if this is the credentials email
            if "iptvv" in from_addr.lower() or CREDENTIALS_EMAIL_SUBJECT.lower() in subject.lower():
                print(f"[OK] Credentials email found!")
                # Fetch full message content
                full_message = get_mailtm_message_by_id(auth_token, msg["id"])
                if full_message:
                    return full_message

        if messages:
            print(f"[*] Found {len(messages)} email(s), but credentials email not yet received")
        else:
            print(f"[*] Inbox is empty")

        print(f"[*] Waiting {EMAIL_POLL_SECONDS}s before next check...")
        time.sleep(EMAIL_POLL_SECONDS)

    print(f"[!] Timeout: Credentials email not received after {max_wait_seconds}s")
    return None


# ═══════════════════════════════════════════════════════════
# Tor Network Helper Functions
# ═══════════════════════════════════════════════════════════

def check_tor_running():
    """Check if Tor service is running by testing SOCKS connection."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        result = sock.connect_ex((TOR_SOCKS_HOST, TOR_SOCKS_PORT))
        sock.close()
        return result == 0
    except Exception as e:
        print(f"[!] Tor check failed: {e}")
        return False


def renew_tor_ip():
    """
    Request Tor to establish a new circuit (new IP address).

    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Connect to Tor control port
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(10)
        sock.connect((TOR_CONTROL_HOST, TOR_CONTROL_PORT))

        # Authenticate if password is set
        if TOR_CONTROL_PASSWORD:
            sock.sendall(f'AUTHENTICATE "{TOR_CONTROL_PASSWORD}"\r\n'.encode())
        else:
            sock.sendall(b'AUTHENTICATE\r\n')

        response = sock.recv(1024).decode()
        if "250 OK" not in response:
            print(f"[!] Tor authentication failed: {response}")
            sock.close()
            return False

        # Send NEWNYM signal to get new circuit
        sock.sendall(b'SIGNAL NEWNYM\r\n')
        response = sock.recv(1024).decode()
        sock.close()

        if "250 OK" in response:
            print("[OK] Tor circuit renewed - new IP address assigned")
            time.sleep(3)  # Wait for new circuit to establish
            return True
        else:
            print(f"[!] Tor NEWNYM failed: {response}")
            return False

    except Exception as e:
        print(f"[!] Failed to renew Tor IP: {e}")
        return False


def get_current_ip():
    """
    Get current public IP address (useful for verifying Tor).

    Returns:
        str: IP address or "Unknown"
    """
    try:
        # Use httpbin.org to check IP
        proxies = {}
        if USE_TOR:
            proxies = {
                'http': f'socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}',
                'https': f'socks5h://{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}'
            }

        response = requests.get('https://httpbin.org/ip', proxies=proxies, timeout=10)
        if response.status_code == 200:
            ip = response.json().get('origin', 'Unknown')
            return ip
    except Exception as e:
        print(f"[!] Could not fetch IP: {e}")
        return "Unknown"


# ═══════════════════════════════════════════════════════════
# Selenium Browser Automation
# ═══════════════════════════════════════════════════════════

def get_driver():
    """Initialize Chrome WebDriver with appropriate options."""
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

    # Configure Tor proxy if enabled
    if USE_TOR:
        if not check_tor_running():
            print("[!] ERROR: USE_TOR=True but Tor is not running!")
            print(f"[!] Please start Tor service: sudo systemctl start tor")
            print(f"[!] Or install Tor: sudo apt-get install tor")
            raise RuntimeError("Tor proxy not available")

        proxy_address = f"{TOR_SOCKS_HOST}:{TOR_SOCKS_PORT}"
        options.add_argument(f'--proxy-server=socks5://{proxy_address}')
        print(f"[*] Using Tor SOCKS5 proxy: {proxy_address}")

        # Get and display current IP
        current_ip = get_current_ip()
        print(f"[*] Current IP address (via Tor): {current_ip}")

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    if os.path.exists(chromedriver_path):
        print(f"[*] Using pre-installed ChromeDriver at {chromedriver_path}")
        service = Service(chromedriver_path)
    else:
        print("[*] Downloading/verifying ChromeDriver...")
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)


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
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)
        print(f"[*] Checkout URL: {driver.current_url}")

        # Verify we're on checkout page
        if "checkout" not in driver.current_url.lower():
            print("[!] WARNING: Not on checkout page after navigation")
            # Try alternative: look for "View Cart" or "Proceed to Checkout" button
            try:
                checkout_btn = find_clickable_by_text(driver, ["proceed to checkout", "checkout", "view cart"], timeout=10)
                safe_click(driver, checkout_btn)
                time.sleep(3)
                print(f"[*] After clicking checkout button: {driver.current_url}")
            except:
                pass

    except TimeoutError:
        print("[!] 'Get Free Trial' link not found")
        # Try direct URL for adding product to cart
        print("[*] Trying direct add-to-cart URL...")
        driver.get("https://iptvv.ca/?add-to-cart=7758")
        time.sleep(5)
        print(f"[*] After add-to-cart URL: {driver.current_url}")

        # Navigate to checkout
        checkout_url = f"{IPTVV_BASE_URL}/checkout/"
        driver.get(checkout_url)
        WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
        time.sleep(3)
        print(f"[*] Checkout URL: {driver.current_url}")


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
    # Handle "Device Select" checkboxes (required by IPTVV.ca)
    print("[*] Looking for device selection checkboxes...")
    device_filled = False
    try:
        # Find device checkboxes by name="device_select[]"
        device_checkboxes = driver.find_elements(By.NAME, "device_select[]")
        if device_checkboxes:
            print(f"[*] Found {len(device_checkboxes)} device checkboxes")
            # Check the first device checkbox (Android TV Box or Firestick)
            for checkbox in device_checkboxes:
                value = checkbox.get_attribute("value") or ""
                # Prefer Android Box or Smart TV
                if value in ["androidbox", "smarttv", "firetv"]:
                    if not checkbox.is_selected():
                        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                        time.sleep(0.3)
                        safe_click(driver, checkbox)
                        print(f"[OK] Selected device: {value}")
                        filled_fields.append("device_select")
                        device_filled = True
                        break

            # Fallback: check first device if none selected
            if not device_filled and len(device_checkboxes) > 0:
                checkbox = device_checkboxes[0]
                if not checkbox.is_selected():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                    time.sleep(0.3)
                    safe_click(driver, checkbox)
                    value = checkbox.get_attribute("value") or "first"
                    print(f"[OK] Selected device (first): {value}")
                    filled_fields.append("device_select")
                    device_filled = True
        else:
            print("[!] No device checkboxes found with name='device_select[]'")
    except Exception as e:
        print(f"[!] Device checkbox error: {e}")

    # Handle "Billing Channel Packages" field (required by IPTVV.ca)
    # This might be a multiselect, checkbox group, or hidden field
    try:
        # Strategy 1: Try to find checkboxes or radio buttons for packages
        package_checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox' and (contains(@id, 'channel') or contains(@name, 'channel') or contains(@id, 'package') or contains(@name, 'package'))]")
        if package_checkboxes:
            # Check all packages (or first one for "full channel")
            for checkbox in package_checkboxes[:1]:  # Select first/main package
                if not checkbox.is_selected():
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                    time.sleep(0.3)
                    safe_click(driver, checkbox)
                    print(f"[OK] Selected channel package checkbox")
                    filled_fields.append("billing_channel_packages")
                    break

        # Strategy 2: Try to find a select dropdown for packages
        else:
            for package_id in ["billing_channel_packages", "billing_packages", "channel_packages", "packages"]:
                try:
                    package_field = driver.find_element(By.ID, package_id)
                    if package_field.tag_name == "select":
                        select = Select(package_field)
                        # Select option containing "full" or "all" or just first option
                        selected = False
                        for option in select.options:
                            if "full" in option.text.lower() or "all" in option.text.lower():
                                select.select_by_visible_text(option.text)
                                print(f"[OK] Selected package: {option.text}")
                                filled_fields.append("billing_channel_packages")
                                selected = True
                                break
                        if not selected and len(select.options) > 1:
                            select.select_by_index(1)
                            print(f"[OK] Selected package (first option): {select.options[1].text}")
                            filled_fields.append("billing_channel_packages")
                        break
                except:
                    continue
    except Exception as e:
        print(f"[!] Could not find or fill channel packages field: {e}")

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
            print(f"[*] Found password: {password}")
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

    # Step 1: Create mail.tm account
    email_address, email_password, auth_token = create_mailtm_account()
    if not email_address:
        error_msg = "Failed to create mail.tm temporary email account"
        print(f"[!] {error_msg}")
        if is_laravel_mode:
            send_webhook_callback(callback_url, user_id, "failed", error=error_msg)
        send_telegram_notification("error", error_msg, None)
        raise SystemExit(1)

    try:
        # Step 2: Initialize browser
        driver = get_driver()

        # Step 3: Navigate to cart and start trial process
        navigate_to_cart_and_get_free_trial(driver)

        # Step 4: Select full channel package
        select_full_channel_package(driver)

        # Step 5: Fill checkout form with mail.tm email
        fill_checkout_form(driver, email_address)

        # Step 6: Submit form
        submit_checkout_form(driver)

        # Step 7: Wait for credentials email (this can take 5-45 minutes)
        print("\n" + "=" * 60)
        print(f"[*] Order submitted! Monitoring mail.tm inbox: {email_address}")
        print("=" * 60 + "\n")

        credentials_message = wait_for_credentials_email(auth_token)
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
        print("✓ IPTVV CANADA CREDENTIALS EXTRACTED SUCCESSFULLY")
        print("=" * 60)
        print(f"[*] Server Address: {hostname}")
        print(f"[*] Username: {username}")
        print(f"[*] Password: {password}")
        print(f"[*] M3U URL: {m3u_url}")
        print("=" * 60 + "\n")

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

    except Exception as exc:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"\n[!] IPTVV Canada automation failed: {exc}")
        print(error_traceback)

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="failed",
                error=f"{exc}\n\n{error_traceback}"
            )

        send_telegram_notification("error", str(exc), error_traceback)

        if driver:
            try:
                driver.quit()
            except:
                pass
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
  python iptvvcanada_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/iptvv-automation
        """,
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    args = parser.parse_args()

    main(user_id=args.user_id, callback_url=args.callback_url)
