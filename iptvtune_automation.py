"""
IPTVtune - Automated Trial Account Creation

Automates the IPTVtune WHMCS checkout flow (https://iptvtune.com/pay/) and extracts Xtream
credentials from the client-area email history.

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
IPTVTUNE_BASE_URL = os.getenv("IPTVTUNE_BASE_URL", "https://iptvtune.com").rstrip("/")
IPTVTUNE_CART_URL = os.getenv(
    "IPTVTUNE_CART_URL",
    f"{IPTVTUNE_BASE_URL}/pay/cart.php?a=confproduct&i=0",
)
IPTVTUNE_DEVICE_TYPE = os.getenv("IPTVTUNE_DEVICE_TYPE", "Smart TV (Samsung/Sony/LG)")
IPTVTUNE_BOUQUET_MODE = os.getenv("IPTVTUNE_BOUQUET_MODE", "all").lower()
IPTVTUNE_PORTAL_HOST = os.getenv("IPTVTUNE_PORTAL_HOST", "")
EMAIL_POLL_SECONDS = int(os.getenv("IPTVTUNE_EMAIL_POLL_SECONDS", "60"))
EMAIL_MAX_WAIT_SECONDS = int(os.getenv("IPTVTUNE_EMAIL_MAX_WAIT_SECONDS", "3600"))
AUTO_EXIT = os.getenv("AUTO_EXIT", "True").lower() == "true"

# IBO Player playlist update (optional): push the extracted Xtream credentials into an
# existing IBO Player playlist via their savePlaylist API.
IPTVTUNE_IBOPLAYER_ENABLED = os.getenv("IPTVTUNE_IBOPLAYER_ENABLED", "False").lower() == "true"
IPTVTUNE_IBOPLAYER_COOKIE = os.getenv("IPTVTUNE_IBOPLAYER_COOKIE", "")
IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID = os.getenv("IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID", "")
IPTVTUNE_IBOPLAYER_PLAYLIST_NAME = os.getenv("IPTVTUNE_IBOPLAYER_PLAYLIST_NAME", "iptvtune")

READY_EMAIL_SUBJECT = os.getenv("IPTVTUNE_READY_EMAIL_SUBJECT", "IPTV Access Information")
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


def inspect_bouquet_field(driver, label=""):
    """
    Inspect and log the customfield[25] input value for debugging.
    This field stores the selected bouquet IDs on the WHMCS checkout page.
    """
    try:
        # Try to find the customfield25 input
        field = driver.find_element(By.ID, "customfield25")
        value = field.get_attribute("value") or ""
        readonly = field.get_attribute("readonly")

        print(f"\n{'='*60}")
        print(f"[DEBUG] Bouquet Field Inspection {label}")
        print(f"{'='*60}")
        print(f"Field ID: customfield25")
        print(f"Field Name: {field.get_attribute('name')}")
        print(f"Current Value: '{value}'")
        print(f"Value Length: {len(value)}")
        print(f"Is Readonly: {readonly}")
        print(f"Is Displayed: {field.is_displayed()}")
        print(f"{'='*60}\n")

        return value
    except Exception as e:
        print(f"[!] Could not inspect customfield25: {e}")
        return None


def set_customfield_directly(driver, bouquet_ids, format_type="comma"):
    """
    Directly set the customfield[25] input value as a fallback if modal fails.

    Args:
        driver: Selenium WebDriver instance
        bouquet_ids: List of bouquet IDs to set
        format_type: Format to use ('comma', 'json', 'pipe')

    Returns:
        bool: True if successful, False otherwise
    """
    if not bouquet_ids:
        print("[*] No bouquets to set in customfield")
        return True

    try:
        field = driver.find_element(By.ID, "customfield25")

        # Format the bouquet IDs based on the format type
        if format_type == "comma":
            value = ",".join(str(bid) for bid in bouquet_ids)
        elif format_type == "json":
            value = json.dumps(bouquet_ids)
        elif format_type == "pipe":
            value = "|".join(str(bid) for bid in bouquet_ids)
        else:
            value = ",".join(str(bid) for bid in bouquet_ids)  # Default to comma

        print(f"[*] Directly setting customfield25 to: {value}")

        # Use JavaScript to set the readonly field value
        driver.execute_script(
            "arguments[0].value = arguments[1]; "
            "arguments[0].dispatchEvent(new Event('change', {bubbles: true})); "
            "arguments[0].dispatchEvent(new Event('input', {bubbles: true}));",
            field,
            value
        )

        time.sleep(1)

        # Verify it was set
        actual_value = field.get_attribute("value")
        if actual_value == value:
            print(f"[OK] Successfully set customfield25 to: {value}")
            return True
        else:
            print(f"[!] Failed to set customfield25. Expected: {value}, Got: {actual_value}")
            return False

    except Exception as e:
        print(f"[!] Error setting customfield directly: {e}")
        return False


def select_specific_bouquets(driver, bouquet_ids):
    """
    Selects specific bouquets by their IDs in the bouquet picker modal.

    The bouquet checkboxes have values like "1", "3", "60", etc. corresponding to bouquet IDs.
    First, uncheck all bouquets, then check only the requested ones.
    """
    if not bouquet_ids:
        print("[*] No specific bouquets to select, using default selection")
        return

    print(f"[*] Selecting {len(bouquet_ids)} specific bouquets: {bouquet_ids}")

    try:
        # Find all checkbox inputs within the modal
        checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox' and @value]")

        selected_count = 0
        for checkbox in checkboxes:
            value = checkbox.get_attribute("value")

            # Skip if no value or not numeric
            if not value or not value.isdigit():
                continue

            bouquet_id = int(value)
            is_checked = checkbox.is_selected()
            should_be_checked = bouquet_id in bouquet_ids

            # Update checkbox state if it doesn't match desired state
            if is_checked != should_be_checked:
                try:
                    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", checkbox)
                    time.sleep(0.2)
                    safe_click(driver, checkbox)

                    if should_be_checked:
                        selected_count += 1
                        print(f"[OK] Selected bouquet {bouquet_id}")
                except Exception as e:
                    print(f"[!] Could not toggle bouquet {bouquet_id}: {e}")
            elif should_be_checked:
                selected_count += 1

        print(f"[OK] {selected_count} bouquets selected")
        time.sleep(1)

    except Exception as e:
        print(f"[!] Error selecting specific bouquets: {e}")
        print("[*] Continuing with default selection")


def click_bouquet_wizard_until_applied(driver, bouquets=None):
    """
    The bouquet picker is a multi-step modal. The same #savebqbtn advances through content
    types and eventually applies the selection.

    If bouquets is provided (list of bouquet IDs), selects only those specific bouquets.
    Otherwise, selects all bouquets (default behavior).

    Returns:
        bool: True if bouquets were successfully applied, False otherwise
    """
    # Inspect field BEFORE opening modal
    print("[*] Checking customfield25 BEFORE bouquet selection...")
    inspect_bouquet_field(driver, label="(BEFORE MODAL)")

    for step in range(1, 8):
        try:
            btn = WebDriverWait(driver, 8).until(
                EC.element_to_be_clickable((By.ID, "savebqbtn"))
            )
        except Exception:
            print("[*] Bouquet wizard button is no longer visible")
            break

        label = (btn.get_attribute("value") or btn.text or "").strip()
        print(f"[*] Bouquet wizard step {step}: {label or 'button'}")

        # If specific bouquets are requested, select them before clicking the button
        if bouquets and step == 1:
            select_specific_bouquets(driver, bouquets)

        safe_click(driver, btn)
        time.sleep(1.5)

        if "apply" in label.lower():
            try:
                WebDriverWait(driver, 8).until_not(
                    EC.visibility_of_element_located((By.ID, "savebqbtn"))
                )
            except Exception:
                pass
            break

    # Wait longer for JavaScript to fully populate the field
    time.sleep(5)

    # Inspect field AFTER modal closes
    print("[*] Checking customfield25 AFTER bouquet selection...")
    field_value = inspect_bouquet_field(driver, label="(AFTER MODAL)")

    # Verify the field was populated
    if bouquets and field_value:
        # Parse field value to extract bouquet IDs (support multiple formats)
        field_str = str(field_value).strip()
        found_ids = []

        try:
            # Try comma-separated format: "60,63"
            if ',' in field_str:
                found_ids = [int(x.strip()) for x in field_str.split(',') if x.strip().isdigit()]
            # Try JSON array format: "[60,63]"
            elif field_str.startswith('[') and field_str.endswith(']'):
                found_ids = json.loads(field_str)
            # Try pipe-separated format: "60|63"
            elif '|' in field_str:
                found_ids = [int(x.strip()) for x in field_str.split('|') if x.strip().isdigit()]
            # Single number
            elif field_str.isdigit():
                found_ids = [int(field_str)]
        except Exception as e:
            print(f"[!] Could not parse customfield25 value: {e}")
            found_ids = []

        # Check how many requested IDs are present in the field
        matching_ids = set(bouquets) & set(found_ids)
        match_percentage = len(matching_ids) / len(bouquets) * 100 if bouquets else 0

        print(f"[*] Verification: Requested {len(bouquets)} bouquets, found {len(found_ids)} IDs in field")
        print(f"[*] Matching IDs: {list(matching_ids)} ({match_percentage:.0f}% match)")

        if len(matching_ids) >= len(bouquets) * 0.8:  # At least 80% of requested bouquets
            print("[OK] Bouquet field contains requested IDs")
            return True
        else:
            print(f"[!] WARNING: Only {len(matching_ids)}/{len(bouquets)} bouquets matched")
            print(f"[!] Requested: {bouquets}")
            print(f"[!] Found in field: {found_ids}")
            return False
    elif not bouquets:
        # If no specific bouquets requested, just check if field has some value
        if field_value and len(field_value) > 0:
            print("[OK] Bouquet field has been populated (all bouquets mode)")
            return True
        else:
            print("[!] WARNING: Bouquet field is empty")
            return False
    else:
        # Field is empty but we requested specific bouquets
        print("[!] WARNING: Bouquet field is empty after modal")
        return False


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
        "//a[contains(@class, 'btn-order-now') or contains(., 'Order Now') or contains(@href, 'free-trial')]",
    )
    for link in product_links:
        href = link.get_attribute("href") or ""
        if "free-trial" in href or "trial" in href:
            print(f"[*] Opening trial product from category page: {href}")
            safe_click(driver, link)
            WebDriverWait(driver, 20).until(
                lambda d: "confproduct" in d.current_url
                or "free-trial" in d.current_url
                or "smart tv" in d.page_source.lower()
            )
            time.sleep(2)
            print(f"[*] Product configuration URL: {driver.current_url}")
            return


def configure_product(driver, bouquets=None):
    print(f"[*] Navigating to IPTVtune cart: {IPTVTUNE_CART_URL}")
    driver.get(IPTVTUNE_CART_URL)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)
    print(f"[*] Cart URL: {driver.current_url}")

    ensure_product_configuration_page(driver)

    print(f"[*] Selecting device type: {IPTVTUNE_DEVICE_TYPE}")
    selected = False
    for select_el in driver.find_elements(By.TAG_NAME, "select"):
        option_text = " ".join(option.text for option in Select(select_el).options).lower()
        name = (select_el.get_attribute("name") or "").lower()
        if "smart tv" in option_text or "device" in name:
            chosen = select_dropdown_value(select_el, IPTVTUNE_DEVICE_TYPE)
            print(f"[OK] Device selected: {chosen}")
            selected = True
            break
    if not selected:
        print("[!] Device dropdown not found. Continuing; product may already be configured.")

    print("[*] Opening bouquet selector...")
    bouquet_success = False
    try:
        try:
            bouquet_button = driver.find_element(By.ID, "selectbouquetsbtn")
        except Exception:
            bouquet_button = find_clickable_by_text(driver, ["select bouquets", "bouquets"], timeout=10)
        safe_click(driver, bouquet_button)
        time.sleep(2)

        print("[*] Selecting bouquets via modal...")
        bouquet_success = click_bouquet_wizard_until_applied(driver, bouquets=bouquets)

        # If modal failed and we have specific bouquets, try direct field manipulation
        if not bouquet_success and bouquets:
            print("[!] Modal-based selection failed or didn't populate field correctly")
            print("[*] Attempting fallback: direct field manipulation...")

            # Try different formats to see which one works
            for format_type in ["comma", "json", "pipe"]:
                print(f"[*] Trying format: {format_type}")
                if set_customfield_directly(driver, bouquets, format_type=format_type):
                    bouquet_success = True
                    print(f"[OK] Successfully set bouquets using {format_type} format")
                    break

            if not bouquet_success:
                print("[!] All fallback attempts failed")

    except Exception as exc:
        print(f"[!] Error during bouquet selection: {exc}")
        bouquet_success = False

    # If bouquet selection was requested but failed completely, abort
    if bouquets and not bouquet_success:
        print("\n" + "="*60)
        print("[CRITICAL] BOUQUET SELECTION FAILED")
        print("="*60)
        print("Specific bouquets were requested but could not be applied.")
        print("Aborting automation to prevent creating account with wrong bouquets.")
        print("="*60 + "\n")

        # Take screenshot for debugging
        try:
            screenshot_path = f"/tmp/iptvtune_bouquet_fail_{int(time.time())}.png"
            driver.save_screenshot(screenshot_path)
            print(f"[*] Debug screenshot saved: {screenshot_path}")
        except Exception as e:
            print(f"[!] Could not save screenshot: {e}")

        raise RuntimeError("Failed to apply bouquet selection - aborting automation")

    # FINAL verification before clicking Continue button
    if bouquets:
        print("[*] Final verification of customfield25 before proceeding to checkout...")
        final_field_value = inspect_bouquet_field(driver, label="(FINAL CHECK BEFORE CONTINUE)")

        # Parse and verify the field contains our bouquets
        field_str = str(final_field_value).strip()
        found_ids = []

        try:
            if ',' in field_str:
                found_ids = [int(x.strip()) for x in field_str.split(',') if x.strip().isdigit()]
            elif field_str.startswith('[') and field_str.endswith(']'):
                found_ids = json.loads(field_str)
            elif '|' in field_str:
                found_ids = [int(x.strip()) for x in field_str.split('|') if x.strip().isdigit()]
            elif field_str.isdigit():
                found_ids = [int(field_str)]
        except Exception as e:
            print(f"[!] Could not parse final field value: {e}")

        if not found_ids or len(found_ids) == 0:
            print("[!] CRITICAL: customfield25 is EMPTY before clicking Continue!")
            print("[!] This would result in wrong channels being delivered.")

            # Take screenshot for debugging
            try:
                screenshot_path = f"/tmp/iptvtune_empty_field_{int(time.time())}.png"
                driver.save_screenshot(screenshot_path)
                print(f"[*] Debug screenshot saved: {screenshot_path}")
            except Exception as e:
                print(f"[!] Could not save screenshot: {e}")

            print("[!] Aborting automation to prevent wrong channel delivery.")
            raise RuntimeError("Bouquet field empty before checkout - selection not applied")

        # Check if we have reasonable match (at least 80% of requested bouquets)
        matching_ids = set(bouquets) & set(found_ids)
        match_percentage = (len(matching_ids) / len(bouquets) * 100) if bouquets else 0

        print(f"[*] Final verification result:")
        print(f"    Requested: {bouquets}")
        print(f"    Found in field: {found_ids}")
        print(f"    Matching: {list(matching_ids)} ({match_percentage:.1f}%)")

        if match_percentage < 80:
            print(f"[!] WARNING: Only {match_percentage:.1f}% of requested bouquets are set!")
            print("[!] This may result in incorrect channels.")

            # Take screenshot for debugging
            try:
                screenshot_path = f"/tmp/iptvtune_partial_match_{int(time.time())}.png"
                driver.save_screenshot(screenshot_path)
                print(f"[*] Debug screenshot saved: {screenshot_path}")
            except Exception as e:
                print(f"[!] Could not save screenshot: {e}")

            print("[!] Aborting automation due to insufficient match.")
            raise RuntimeError(f"Bouquet verification failed: only {match_percentage:.1f}% match")

        print(f"[OK] Final verification passed: {match_percentage:.1f}% match")

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
    emails_url = f"{IPTVTUNE_BASE_URL}/pay/clientarea.php?action=emails"
    deadline = time.time() + EMAIL_MAX_WAIT_SECONDS
    attempt = 0

    while time.time() < deadline:
        attempt += 1
        print(f"[*] Checking IPTVtune email history (attempt {attempt})...")
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
            email_url = f"{IPTVTUNE_BASE_URL}/pay/{match.group(1)}"
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
        # IPTVtune labels the server as "URL:" (the bare server line precedes "M3U PLAYLIST URL:").
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
            if "iptvtune.com" not in lowered and "maxcdn" not in lowered:
                portal_url = url.rstrip(".,)")
                break

    return username, password, portal_url


def extract_credentials_from_ready_email(driver):
    row = wait_for_ready_email(driver)
    open_email_row(driver, row)
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))
    time.sleep(2)

    # IPTVtune renders the email body inside an iframe, so collect text from the main page
    # plus every iframe (the credentials live in the message iframe, not the outer page).
    texts = [driver.find_element(By.TAG_NAME, "body").text]
    for iframe in driver.find_elements(By.TAG_NAME, "iframe"):
        try:
            driver.switch_to.frame(iframe)
            texts.append(driver.find_element(By.TAG_NAME, "body").text)
        except Exception:
            pass
        finally:
            driver.switch_to.default_content()
    body_text = "\n".join(t for t in texts if t)

    username, password, portal_url = extract_credentials_from_text(body_text)

    if not (username and password and portal_url):
        print("[!] Direct text extraction incomplete; searching page source")
        username2, password2, portal2 = extract_credentials_from_text(driver.page_source)
        username = username or username2
        password = password or password2
        portal_url = portal_url or portal2

    if IPTVTUNE_PORTAL_HOST and not portal_url:
        portal_url = IPTVTUNE_PORTAL_HOST

    if not username or not password or not portal_url:
        print("[*] Email text preview:")
        print(body_text[:1000])
        raise RuntimeError("Could not extract username, password, and portal URL from ready email")

    portal_url = portal_url.rstrip("/")
    m3u_url = f"{portal_url}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

    print("\n" + "=" * 60)
    print("IPTVTUNE CREDENTIALS FOUND:")
    print("=" * 60)
    print(f"[*] Portal URL: {portal_url}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {password}")
    print(f"[*] M3U URL: {m3u_url}")
    print("=" * 60 + "\n")

    return portal_url, username, password, m3u_url


def save_to_iboplayer(username, password, hostname, max_retries=3):
    """
    Update the IBO Player playlist with the extracted Xtream credentials.

    Uses the same savePlaylist API as the other providers; current_playlist_url_id targets
    the existing playlist to update.

    Args:
        username: IPTVtune Xtream username
        password: IPTVtune Xtream password
        hostname: IPTVtune server/portal URL (e.g. http://tunestream.me:8080)
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        bool: True if successful, False otherwise
    """
    if not IPTVTUNE_IBOPLAYER_ENABLED:
        print("[*] IBO Player integration is disabled (IPTVTUNE_IBOPLAYER_ENABLED=False)")
        return False

    if not IPTVTUNE_IBOPLAYER_COOKIE or not IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID:
        print("[!] IBO Player integration enabled but missing required credentials:")
        print(f"    - IPTVTUNE_IBOPLAYER_COOKIE: {'Set' if IPTVTUNE_IBOPLAYER_COOKIE else 'Missing'}")
        print(f"    - IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID: {'Set' if IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID else 'Missing'}")
        return False

    api_url = "https://iboplayer.com/frontend/device/savePlaylist"
    headers = {
        "Content-Type": "application/json",
        "Cookie": IPTVTUNE_IBOPLAYER_COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    }

    playlist_url = hostname.rstrip("/")
    payload = {
        "current_playlist_url_id": IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID,
        "password": password,
        "pin": "",
        "playlist_name": IPTVTUNE_IBOPLAYER_PLAYLIST_NAME,
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
    print(f"[*] Playlist Name: {IPTVTUNE_IBOPLAYER_PLAYLIST_NAME}")
    print(f"[*] Playlist URL: {playlist_url}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {password}")
    print("=" * 60)

    for attempt in range(1, max_retries + 1):
        try:
            response = requests.post(api_url, json=payload, headers=headers, timeout=30)
            print(f"[*] IBO Player response status: {response.status_code}")
            if response.status_code == 200:
                print("[OK] Playlist saved to IBO Player successfully!")
                try:
                    print(f"[*] IBO Player response: {response.json()}")
                except Exception:
                    pass
                return True
            if 400 <= response.status_code < 500:
                print(f"[!] IBO Player API error {response.status_code}: {response.text[:200]}")
                print("[!] This is a configuration error - please check your IBO Player credentials")
                return False
            print(f"[!] IBO Player server error {response.status_code}: {response.text[:200]}")
        except requests.RequestException as exc:
            print(f"[!] IBO Player request failed (attempt {attempt}/{max_retries}): {exc}")
        if attempt < max_retries:
            time.sleep(2 ** attempt)
    return False


def send_webhook_callback(callback_url, user_id, status, username=None, password=None, host=None, m3u_url=None, error=None, bouquets=None, max_retries=3):
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
        if bouquets:
            payload["bouquets"] = bouquets
    else:
        payload["error"] = error

    headers = {"Content-Type": "application/json", "User-Agent": "IPTVtune-Automation/1.0"}
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


def main(user_id=None, callback_url=None, bouquets=None):
    driver = get_driver()
    is_laravel_mode = bool(user_id and callback_url)

    print("\n[*] Starting IPTVtune automation...")
    print(f"[*] User ID: {user_id if user_id else 'N/A'}")
    print(f"[*] Callback URL: {callback_url if callback_url else 'N/A'}")
    print(f"[*] Bouquets: {bouquets if bouquets else 'Default (all)'}")
    print(f"[*] Laravel integration mode: {is_laravel_mode}")

    try:
        configure_product(driver, bouquets=bouquets)
        fill_checkout_form(driver)
        complete_order(driver)
        host, username, password, m3u_url = extract_credentials_from_ready_email(driver)

        # Push the credentials into the configured IBO Player playlist (no-op if disabled).
        ibo_saved = save_to_iboplayer(username, password, host)

        if is_laravel_mode:
            send_webhook_callback(
                callback_url=callback_url,
                user_id=user_id,
                status="success",
                username=username,
                password=password,
                host=host,
                m3u_url=m3u_url,
                bouquets=bouquets,
            )
        else:
            notifier.notify_success(m3u_url, username, None)
            if IPTVTUNE_IBOPLAYER_ENABLED:
                try:
                    notifier.notify_ibo_saved() if ibo_saved else notifier.notify_ibo_failed()
                except Exception:
                    pass

        print("[OK] IPTVtune automation complete")
    except Exception as exc:
        import traceback

        error_traceback = traceback.format_exc()
        print(f"\n[!] IPTVtune automation failed: {exc}")
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
        description="IPTVtune - Automated Account Creation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python iptvtune_automation.py
  python iptvtune_automation.py --user-id 123 --callback-url https://app.com/api/webhooks/iptvtune-automation
  python iptvtune_automation.py --user-id 123 --callback-url https://... --bouquets 1,3,60,63
        """,
    )
    parser.add_argument("--user-id", type=int, help="Laravel IPTV account ID")
    parser.add_argument("--callback-url", type=str, help="Webhook callback URL")
    parser.add_argument("--bouquets", type=str, help="Comma-separated list of bouquet IDs (e.g., 1,3,60,63)")
    args = parser.parse_args()

    # Parse bouquet IDs from comma-separated string
    bouquet_list = None
    if args.bouquets:
        try:
            bouquet_list = [int(bid.strip()) for bid in args.bouquets.split(",") if bid.strip()]
            print(f"[*] Parsed bouquet IDs: {bouquet_list}")
        except ValueError as e:
            print(f"[!] Invalid bouquet format: {e}")
            print("[*] Expected format: --bouquets 1,3,60,63")

    main(user_id=args.user_id, callback_url=args.callback_url, bouquets=bouquet_list)
