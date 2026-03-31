"""
Zazy TV - Automated Playlist Creation and IBO Player Integration
Automates the Zazy TV free trial signup process and saves the playlist to IBO Player.

Features:
- Automated account creation with strong password generation
- Automatic reCAPTCHA solving using 2captcha service
- M3U playlist extraction with username and password
- Automatic playlist saving to IBO Player via API

Install deps: pip install selenium webdriver-manager 2captcha-python python-dotenv requests
"""

import os
import time
import random
import string
import json
import requests
from datetime import datetime
from dotenv import load_dotenv
from twocaptcha import TwoCaptcha
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

# Load environment variables from .env file
load_dotenv()

# Import Telegram notifier (optional - gracefully handle if not available)
try:
    from telegram_notifier import notifier
    TELEGRAM_AVAILABLE = True
except ImportError:
    print("[!] Warning: telegram_notifier module not found. Telegram notifications will be disabled.")
    TELEGRAM_AVAILABLE = False
    # Create a dummy notifier object
    class DummyNotifier:
        def notify_success(self, *args, **kwargs):
            return False
        def notify_error(self, *args, **kwargs):
            return False
    notifier = DummyNotifier()

# Configuration from environment variables
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY")
BASE_URL = os.getenv("BASE_URL", "https://zazyhost.com")
HOME_URL = os.getenv("HOME_URL", "https://zazytv.com/")
PROMO_CODE = os.getenv("PROMO_CODE", "")
LOGIN_EMAIL = os.getenv("LOGIN_EMAIL", "your@email.com")
LOGIN_PASSWORD = os.getenv("LOGIN_PASSWORD", "yourpassword")
SKIP_LOGIN = os.getenv("SKIP_LOGIN", "True").lower() == "true"

# IBO Player API Configuration
IBOPLAYER_COOKIE = os.getenv("IBOPLAYER_COOKIE", "")
IBOPLAYER_PLAYLIST_URL_ID = os.getenv("IBOPLAYER_PLAYLIST_URL_ID", "")
IBOPLAYER_PLAYLIST_NAME = os.getenv("IBOPLAYER_PLAYLIST_NAME", "Zazy")
IBOPLAYER_PLAYLIST_URL = os.getenv("IBOPLAYER_PLAYLIST_URL", "http://live.zazytv.com")

# Initialize 2captcha solver
solver = TwoCaptcha(TWOCAPTCHA_API_KEY) if TWOCAPTCHA_API_KEY else None


def get_driver():
    options = Options()

    # Enable headless mode for Docker/server environments
    # Set HEADLESS=False in .env to disable for local development
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

    # Check if ChromeDriver is pre-installed (Docker environment)
    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")

    if os.path.exists(chromedriver_path):
        print(f"[*] Using pre-installed ChromeDriver at {chromedriver_path}")
        driver_service = Service(chromedriver_path)
    else:
        # Fallback to ChromeDriverManager for local development
        print("[*] Downloading/verifying ChromeDriver...")
        try:
            driver_service = Service(ChromeDriverManager().install())
        except Exception as e:
            print(f"[!] Failed to download ChromeDriver: {e}")
            print("[!] If running in Docker, ensure ChromeDriver is installed in the image")
            print("[!] If network is unavailable, ChromeDriver must be pre-installed")
            raise

    driver = webdriver.Chrome(service=driver_service, options=options)
    return driver


def dump_page_links(driver, label=""):
    """Print all links on the current page for debugging."""
    links = driver.find_elements(By.TAG_NAME, "a")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    print(f"\n--- [{label}] Links found ({len(links)}) ---")
    for el in links:
        text = el.text.strip()
        href = el.get_attribute("href") or ""
        if text or href:
            print(f"  TEXT='{text}'  HREF='{href}'")
    print(f"\n--- [{label}] Buttons found ({len(buttons)}) ---")
    for el in buttons:
        print(f"  TEXT='{el.text.strip()}'")
    print("---\n")


def debug_password_fields(driver):
    """Debug helper to inspect all password-related fields on the page."""
    print("\n=== PASSWORD FIELDS DEBUG ===")

    # Find all input fields
    all_inputs = driver.find_elements(By.TAG_NAME, "input")
    print(f"[*] Total input fields on page: {len(all_inputs)}")

    # Find password fields
    pw_fields = driver.find_elements(By.XPATH, "//input[@type='password']")
    print(f"[*] Password fields (type='password'): {len(pw_fields)}")

    for idx, field in enumerate(pw_fields, 1):
        name = field.get_attribute("name") or "N/A"
        id_attr = field.get_attribute("id") or "N/A"
        placeholder = field.get_attribute("placeholder") or "N/A"
        visible = field.is_displayed()
        print(f"    Field {idx}: name='{name}', id='{id_attr}', placeholder='{placeholder}', visible={visible}")

    # Check for fields with 'password' in name/id
    other_pwd_fields = driver.find_elements(By.XPATH, "//input[contains(@name, 'password') or contains(@id, 'password')]")
    if other_pwd_fields:
        print(f"[*] Fields with 'password' in name/id: {len(other_pwd_fields)}")
        for idx, field in enumerate(other_pwd_fields, 1):
            field_type = field.get_attribute("type") or "N/A"
            name = field.get_attribute("name") or "N/A"
            id_attr = field.get_attribute("id") or "N/A"
            print(f"    Field {idx}: type='{field_type}', name='{name}', id='{id_attr}'")

    print("=== END DEBUG ===\n")


def find_element_flexible(driver, keywords, timeout=15):
    """
    Try multiple strategies to find a clickable element whose
    visible text or href contains one of the given keywords (case-insensitive).
    Returns the element or raises TimeoutException.
    """
    end = time.time() + timeout
    while time.time() < end:
        # Search anchors
        for el in driver.find_elements(By.TAG_NAME, "a"):
            text = (el.text or "").lower()
            href = (el.get_attribute("href") or "").lower()
            if any(kw in text or kw in href for kw in keywords):
                return el
        # Search buttons
        for el in driver.find_elements(By.TAG_NAME, "button"):
            text = (el.text or "").lower()
            if any(kw in text for kw in keywords):
                return el
        # Search inputs (type=submit / type=button)
        for el in driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button']"):
            val = (el.get_attribute("value") or "").lower()
            if any(kw in val for kw in keywords):
                return el
        time.sleep(0.5)

    raise TimeoutError(f"Could not find element with keywords: {keywords}")


def safe_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
    time.sleep(0.5)
    try:
        el.click()
    except Exception:
        driver.execute_script("arguments[0].click();", el)


def navigate_and_add_free_trial(driver):
    print(f"[*] Navigating to {HOME_URL}...")
    driver.get(HOME_URL)
    time.sleep(3)  # let JS render

    print(f"[*] Current URL: {driver.current_url}")
    print(f"[*] Page title : {driver.title}")

    # Debug: show all links/buttons on landing page
    dump_page_links(driver, "HOME PAGE")

    # ── Step 1: click Free Trial ──────────────────────────────────────────────
    print("[*] Looking for 'Free Trial' element...")
    try:
        el = find_element_flexible(driver, ["free trial", "free-trial", "freetrial"])
        print(f"[✓] Found Free Trial element: text='{el.text}' href='{el.get_attribute('href')}'")
        safe_click(driver, el)
    except TimeoutError as e:
        print(f"[✗] {e}")
        dump_page_links(driver, "FREE TRIAL NOT FOUND")
        raise

    time.sleep(3)
    print(f"[*] After Free Trial click — URL: {driver.current_url}")
    dump_page_links(driver, "AFTER FREE TRIAL CLICK")

    # ── Step 2: click Checkout ────────────────────────────────────────────────
    print("[*] Looking for 'Checkout' element...")
    try:
        el = find_element_flexible(driver, ["checkout"])
        print(f"[✓] Found Checkout element: text='{el.text}' href='{el.get_attribute('href')}'")
        safe_click(driver, el)
    except TimeoutError as e:
        print(f"[✗] {e}")
        dump_page_links(driver, "CHECKOUT NOT FOUND")
        raise

    time.sleep(3)
    print(f"[*] After Checkout click — URL: {driver.current_url}")


def generate_strong_password(length=16):
    """
    Generate a strong random password with uppercase, lowercase, digits, and special characters.

    Args:
        length: Password length (default: 16)

    Returns:
        A strong random password string
    """
    # Define character sets
    uppercase = string.ascii_uppercase
    lowercase = string.ascii_lowercase
    digits = string.digits
    special_chars = "!@#$%^&*()-_=+[]{}|;:,.<>?"

    # Ensure at least one character from each set
    password_chars = [
        random.choice(uppercase),
        random.choice(uppercase),
        random.choice(lowercase),
        random.choice(lowercase),
        random.choice(digits),
        random.choice(digits),
        random.choice(special_chars),
        random.choice(special_chars),
    ]

    # Fill the rest with random characters from all sets
    all_chars = uppercase + lowercase + digits + special_chars
    password_chars += [random.choice(all_chars) for _ in range(length - len(password_chars))]

    # Shuffle to avoid predictable patterns
    random.shuffle(password_chars)

    return ''.join(password_chars)


def fill_checkout_form(driver):
    """
    Fill the checkout form with random user data.
    Returns the generated password for later use.
    """
    print("[*] Waiting for checkout form fields to load...")
    try:
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.NAME, "firstname"))
        )
    except Exception:
        print("[!] Registration form not found immediately. Proceeding anyway.")
        return ""

    print("[*] Filling registration form...")
    rnd = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))

    # Generic user data
    form_data = {
        "firstname": "John",
        "lastname": "Doe",
        "email": f"johndoe{rnd}@example.com",
        "phonenumber": "2125551234",
        "address1": "123 Main St",
        "city": "New York",
        "state": "NY",
        "postcode": "10001",
    }

    for name, val in form_data.items():
        try:
            el = driver.find_element(By.NAME, name)
            el.clear()
            el.send_keys(val)
        except Exception:
            pass

    # Country (usually a select element)
    try:
        from selenium.webdriver.support.ui import Select
        country_el = driver.find_element(By.NAME, "country")
        Select(country_el).select_by_value("US")
    except Exception:
        pass

    # Generate strong password (16 characters with mixed case, digits, special chars)
    pwd = generate_strong_password(length=16)
    print(f"[*] Generated strong password: {pwd}")

    # Debug: Show what password fields exist on the page
    debug_password_fields(driver)

    # Wait for password fields to be present
    print("[*] Waiting for password fields to load...")
    time.sleep(2)  # Give the page time to fully render

    # Fill password fields with multiple strategies
    try:
        # Try to wait for at least one password field
        try:
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.XPATH, "//input[@type='password']"))
            )
            print("[✓] Password fields detected")
        except:
            print("[!] WARNING: Password fields not detected within timeout")

        # Strategy 1: Find by type='password'
        pw_inputs = driver.find_elements(By.XPATH, "//input[@type='password']")

        # Strategy 2: Also try common password field names/IDs
        if not pw_inputs:
            print("[*] Trying alternative password field selectors...")
            selectors = [
                "//input[contains(@name, 'password')]",
                "//input[contains(@id, 'password')]",
                "//input[contains(@class, 'password')]",
            ]
            for selector in selectors:
                pw_inputs = driver.find_elements(By.XPATH, selector)
                if pw_inputs:
                    break

        if pw_inputs:
            print(f"[*] Found {len(pw_inputs)} password field(s)")
            for idx, el in enumerate(pw_inputs, 1):
                try:
                    # Scroll into view
                    driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", el)
                    time.sleep(0.3)

                    # Click to focus
                    el.click()
                    time.sleep(0.2)

                    # Clear existing value
                    el.clear()
                    time.sleep(0.2)

                    # Fill using send_keys
                    el.send_keys(pwd)
                    time.sleep(0.2)

                    # Verify the value was set
                    value = el.get_attribute('value')
                    if value == pwd:
                        print(f"[✓] Password field {idx} filled successfully")
                    else:
                        # Fallback: try JavaScript injection
                        print(f"[*] Field {idx} verification failed, using JavaScript...")
                        driver.execute_script("arguments[0].value = arguments[1];", el, pwd)
                        # Trigger change event
                        driver.execute_script("""
                            arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                            arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                        """, el)
                        time.sleep(0.2)
                        value = el.get_attribute('value')
                        if value == pwd:
                            print(f"[✓] Password field {idx} filled via JavaScript")
                        else:
                            print(f"[!] WARNING: Password field {idx} may not be filled correctly")

                except Exception as e:
                    print(f"[!] Error filling password field {idx}: {e}")

            print(f"[✓] Password filling complete")
        else:
            print("[!] WARNING: No password fields found on the page!")
            print("[!] This may cause registration to fail")

    except Exception as e:
        print(f"[!] Error during password filling: {e}")
        import traceback
        traceback.print_exc()

    # Accept ToS if present
    try:
        tos = driver.find_element(By.ID, "accepttos")
        if not tos.is_selected():
            driver.execute_script("arguments[0].click();", tos)
    except Exception:
        pass

    print(f"[✓] Form filled! Email: {form_data['email']}  |  Password: {pwd}")
    return pwd


def login(driver):
    print("[*] Navigating to login page...")
    driver.get(f"{BASE_URL}/billing/clientarea.php")

    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.NAME, "username"))
    )

    print("[*] Entering credentials...")
    driver.find_element(By.NAME, "username").send_keys(LOGIN_EMAIL)
    driver.find_element(By.NAME, "password").send_keys(LOGIN_PASSWORD)

    print("[*] Submitting login form...")
    try:
        driver.find_element(By.ID, "login").click()
    except Exception:
        driver.find_element(By.XPATH, "//button[@type='submit']").click()

    WebDriverWait(driver, 10).until(EC.url_contains("clientarea.php"))
    print(f"[✓] Logged in as {LOGIN_EMAIL}")


def apply_promo(driver):
    if not PROMO_CODE:
        return

    print(f"[*] Applying promo code '{PROMO_CODE}'...")
    try:
        promo_input = WebDriverWait(driver, 5).until(
            EC.presence_of_element_located((By.NAME, "promocode"))
        )
        promo_input.clear()
        promo_input.send_keys(PROMO_CODE)
        driver.find_element(
            By.XPATH, "//button[@value='Validate Code' or contains(text(), 'Validate Code')]"
        ).click()
        time.sleep(2)
        print("[✓] Promo code submitted")
    except Exception as e:
        print(f"[!] Could not apply promo code: {e}")


def solve_recaptcha_v2(driver, timeout=120, max_retries=2):
    """
    Automatically solve reCAPTCHA v2 using 2captcha service.
    Returns True if solved successfully, False otherwise.
    """
    if not solver:
        print("[!] 2captcha solver not initialized. Check TWOCAPTCHA_API_KEY in .env")
        return False

    print("[*] Detecting reCAPTCHA...")
    try:
        # Find reCAPTCHA iframe
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        recaptcha_iframe = None
        site_key = None

        for iframe in iframes:
            src = iframe.get_attribute("src") or ""
            if "recaptcha" in src and "api2/anchor" in src:
                recaptcha_iframe = iframe
                # Extract site key from iframe src
                # Format: https://www.google.com/recaptcha/api2/anchor?k=SITEKEY&...
                if "k=" in src:
                    site_key = src.split("k=")[1].split("&")[0]
                break

        if not recaptcha_iframe or not site_key:
            print("[!] reCAPTCHA not found or site key couldn't be extracted")
            return False

        print(f"[*] Found reCAPTCHA with site key: {site_key}")
        current_url = driver.current_url
        print(f"[*] Current URL: {current_url}")

        # Check balance first
        try:
            balance = solver.balance()
            print(f"[*] 2captcha balance: ${balance}")
            if float(balance) < 0.50:
                print("[!] WARNING: Low balance. Please top up at https://2captcha.com/enterpage")
        except Exception as e:
            print(f"[!] Could not check balance: {e}")

        # Retry loop
        for attempt in range(1, max_retries + 1):
            print(f"[*] Attempt {attempt}/{max_retries}: Submitting reCAPTCHA to 2captcha...")
            print("[*] This may take 20-60 seconds...")

            try:
                # Submit captcha to 2captcha with additional parameters
                result = solver.recaptcha(
                    sitekey=site_key,
                    url=current_url,
                    version='v2',
                    invisible=0  # Explicitly set to visible captcha
                )

                recaptcha_token = result['code']
                print(f"[✓] reCAPTCHA solved! Token received: {recaptcha_token[:50]}...")

                # Inject the token into the page
                print("[*] Injecting solution token into page...")

                # Find the g-recaptcha-response textarea and inject the token
                inject_script = f"""
                document.getElementById('g-recaptcha-response').innerHTML = '{recaptcha_token}';
                """
                driver.execute_script(inject_script)

                # Also try to trigger the callback if it exists
                callback_script = """
                var textarea = document.getElementById('g-recaptcha-response');
                if (textarea) {{
                    textarea.value = arguments[0];
                    textarea.innerHTML = arguments[0];
                }}

                // Try to find and trigger the callback
                if (typeof grecaptcha !== 'undefined') {{
                    try {{
                        // Some sites use data-callback attribute
                        var recaptchaElement = document.querySelector('.g-recaptcha');
                        if (recaptchaElement) {{
                            var callback = recaptchaElement.getAttribute('data-callback');
                            if (callback && typeof window[callback] === 'function') {{
                                window[callback](arguments[0]);
                            }}
                        }}
                    }} catch(e) {{
                        console.log('Callback trigger failed:', e);
                    }}
                }}
                """
                driver.execute_script(callback_script, recaptcha_token)

                print("[✓] Token injected successfully!")
                time.sleep(2)  # Give the page a moment to process
                return True

            except Exception as e:
                error_msg = str(e)
                print(f"[!] 2captcha solving failed (attempt {attempt}/{max_retries}): {error_msg}")

                # Provide specific troubleshooting based on error type
                if "500" in error_msg:
                    print("[!] Server error (500) - 2captcha service might be experiencing issues")
                    print("[!] Retrying in 5 seconds..." if attempt < max_retries else "[!] Max retries reached")
                    if attempt < max_retries:
                        time.sleep(5)
                        continue
                elif "ZERO_BALANCE" in error_msg:
                    print("[!] Insufficient balance. Please add funds at https://2captcha.com/enterpage")
                    return False
                elif "ERROR_WRONG_USER_KEY" in error_msg:
                    print("[!] Invalid API key. Check your .env file")
                    return False
                elif "ERROR_KEY_DOES_NOT_EXIST" in error_msg:
                    print("[!] API key doesn't exist. Check your .env file")
                    return False
                else:
                    print(f"[!] Unknown error. Check 2captcha status at https://2captcha.com/")

                # If this was the last attempt, return False
                if attempt >= max_retries:
                    return False

        return False

    except Exception as e:
        print(f"[!] Error during reCAPTCHA detection: {e}")
        import traceback
        traceback.print_exc()
        return False


def complete_order(driver, pwd=""):
    """
    Complete the order, handling reCAPTCHA automatically with 2captcha.

    Args:
        driver: Selenium WebDriver instance
        pwd: Password used for registration (for fallback display)
    """
    print("[*] Waiting for Checkout / Complete Order button...")
    time.sleep(2)  # Give JS a moment to stabilize

    # Try to handle reCAPTCHA automatically with 2captcha
    print("[*] Checking for reCAPTCHA...")

    recaptcha_solved = False
    try:
        # Check if reCAPTCHA exists on the page
        iframes = driver.find_elements(By.TAG_NAME, "iframe")
        has_recaptcha = any("recaptcha" in (iframe.get_attribute("src") or "") for iframe in iframes)

        if has_recaptcha:
            print("[*] reCAPTCHA detected! Attempting automated solving with 2captcha...")
            recaptcha_solved = solve_recaptcha_v2(driver)

            if recaptcha_solved:
                print("[✓] reCAPTCHA solved automatically!")
            else:
                print("[!] Automatic solving failed. Falling back to manual solving...")
                print("\n[!!!] ACTION REQUIRED [!!!]")
                print("Please manually solve the CAPTCHA in the browser window.")
                if pwd:
                    print(f"Password being used is: {pwd}")
                input("Press ENTER in this terminal when the captcha is complete...")
        else:
            print("[*] No reCAPTCHA detected on this page.")

    except Exception as e:
        print(f"[!] Error during reCAPTCHA handling: {e}")
        print("[!] If there's a CAPTCHA on the page, please solve it manually.")
        if pwd:
            print(f"Password being used is: {pwd}")
        input("Press ENTER when ready to continue...")

    print("[*] Clicking 'Complete Order'...")
    try:
        # WHMCS usually has an ID btnCompleteOrder
        try:
            btn = driver.find_element(By.ID, "btnCompleteOrder")
        except Exception:
            btn = find_element_flexible(driver, ["complete order", "complete"], timeout=5)
            
        safe_click(driver, btn)
        print("[✓] 'Complete Order' clicked. Waiting for processing...")
        time.sleep(5)
        print(f"[*] Final URL: {driver.current_url}")
    except Exception as e:
        print(f"[!] Could not click 'Complete Order': {e}")


def get_m3u_playlist(driver):
    """
    Navigate to the service details page and extract the M3U Playlist URL.

    Steps:
    1. Wait for redirect to cart.php?a=complete
    2. Click "Services" in left menu
    3. Click "My Services"
    4. Click on the service row in the table
    5. Extract and print the M3U Playlist URL

    Returns:
        tuple: (m3u_url, username, password) or (None, None, None) if extraction fails
    """
    print("\n[*] Navigating to service details to retrieve M3U Playlist...")

    # Initialize credentials
    username = None
    password = None

    # Wait for the order completion page
    try:
        print("[*] Waiting for order completion page...")
        WebDriverWait(driver, 15).until(
            lambda d: "cart.php?a=complete" in d.current_url or "viewinvoice" in d.current_url or "clientarea" in d.current_url
        )
        time.sleep(3)  # Let the page fully load
        print(f"[*] Current URL: {driver.current_url}")
    except Exception as e:
        print(f"[!] Order completion page not detected: {e}")
        print(f"[*] Current URL: {driver.current_url}")

    # Step 1: Click "Services" in the left menu
    print("[*] Looking for 'Services' in the menu...")
    try:
        services_link = find_element_flexible(driver, ["services"], timeout=10)
        print(f"[✓] Found Services link")
        safe_click(driver, services_link)
        time.sleep(2)
        print(f"[*] After clicking Services — URL: {driver.current_url}")
    except Exception as e:
        print(f"[!] Could not find/click 'Services': {e}")
        # Try alternative approach - direct navigation
        print("[*] Trying direct navigation to services page...")
        driver.get(f"{BASE_URL}/billing/clientarea.php?action=services")
        time.sleep(3)

    # Step 2: Click "My Services"
    print("[*] Looking for 'My Services'...")
    try:
        my_services_link = find_element_flexible(driver, ["my services", "myservices"], timeout=10)
        print(f"[✓] Found My Services link")
        safe_click(driver, my_services_link)
        time.sleep(3)
        print(f"[*] After clicking My Services — URL: {driver.current_url}")
    except Exception as e:
        print(f"[!] Could not find/click 'My Services': {e}")
        # Might already be on the services page
        if "action=services" not in driver.current_url:
            driver.get(f"{BASE_URL}/billing/clientarea.php?action=services")
            time.sleep(3)

    # Step 3: Find and click the service row in the table
    print("[*] Looking for service in the table...")
    try:
        # Find all rows in the services table
        # Try multiple selectors for service rows
        service_rows = driver.find_elements(By.XPATH, "//table//tr[contains(@class, 'service') or contains(@onclick, 'productdetails')]")

        if not service_rows:
            # Try finding any clickable table rows
            service_rows = driver.find_elements(By.XPATH, "//table[@id='tableServicesList']//tbody//tr")

        if not service_rows:
            # Try finding links to product details
            service_links = driver.find_elements(By.XPATH, "//a[contains(@href, 'productdetails')]")
            if service_links:
                print(f"[*] Found {len(service_links)} service link(s)")
                service_link = service_links[0]
                print("[*] Clicking on the first service...")
                safe_click(driver, service_link)
                time.sleep(3)
            else:
                print("[!] No service rows or links found")
                dump_page_links(driver, "SERVICES PAGE")
                return None
        else:
            print(f"[*] Found {len(service_rows)} service row(s)")
            # Click on the first active service
            service_row = service_rows[0]
            print("[*] Clicking on the first service row...")
            safe_click(driver, service_row)
            time.sleep(3)

        print(f"[*] After clicking service — URL: {driver.current_url}")

    except Exception as e:
        print(f"[!] Error navigating to service: {e}")
        import traceback
        traceback.print_exc()
        return None

    # Step 4: Extract the M3U Playlist URL
    print("[*] Searching for M3U Playlist...")
    try:
        # Wait for the product details page to load
        WebDriverWait(driver, 10).until(
            lambda d: "productdetails" in d.current_url
        )

        # Try multiple strategies to find M3U Playlist
        m3u_url = None

        # Strategy 1: Look for element with ID "m3ulinks" (most reliable)
        print("[*] Strategy 1: Looking for element with ID 'm3ulinks'...")
        try:
            m3u_element = driver.find_element(By.ID, "m3ulinks")
            m3u_url = m3u_element.text.strip()

            # If the element contains the URL
            if m3u_url and m3u_url.startswith("http"):
                print(f"[✓] Found M3U URL in #m3ulinks element")
            else:
                # Try to get the value attribute
                m3u_url = m3u_element.get_attribute("value") or m3u_element.get_attribute("href")
                if m3u_url:
                    print(f"[✓] Found M3U URL in #m3ulinks attribute")
                else:
                    # Try to find a link or input inside this element
                    try:
                        link = m3u_element.find_element(By.TAG_NAME, "a")
                        m3u_url = link.get_attribute("href") or link.text
                        print(f"[✓] Found M3U URL in link inside #m3ulinks")
                    except:
                        try:
                            input_field = m3u_element.find_element(By.TAG_NAME, "input")
                            m3u_url = input_field.get_attribute("value")
                            print(f"[✓] Found M3U URL in input inside #m3ulinks")
                        except:
                            # Get all text from the element
                            m3u_url = m3u_element.text.strip()
                            print(f"[*] Got text from #m3ulinks: {m3u_url[:100]}")

            # Clean up the URL if needed
            if m3u_url:
                # Remove any labels like "M3U Playlist :"
                import re
                url_match = re.search(r'(https?://[^\s<>"\']+)', m3u_url)
                if url_match:
                    m3u_url = url_match.group(1)

        except Exception as e:
            print(f"[!] Strategy 1 failed (element #m3ulinks not found): {e}")

        # Strategy 2: Find by label "M3U Playlist" and adjacent value
        if not m3u_url:
            print("[*] Strategy 2: Looking for 'M3U Playlist' label...")
            try:
                # Look for elements containing "M3U Playlist"
                m3u_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'M3U Playlist')]")
                if m3u_elements:
                    print(f"[*] Found {len(m3u_elements)} element(s) with 'M3U Playlist' text")
                    for elem in m3u_elements:
                        # Try to find the value in the same row or next element
                        parent = elem.find_element(By.XPATH, "./..")
                        # Get the full text of the parent element
                        full_text = parent.text
                        print(f"[*] Full text: {full_text}")

                        # Extract URL from the text
                        if "http" in full_text:
                            import re
                            url_match = re.search(r'(https?://[^\s<>"\']+)', full_text)
                            if url_match:
                                m3u_url = url_match.group(1)
                                break
            except Exception as e:
                print(f"[!] Strategy 2 failed: {e}")

        # Strategy 3: Use regex to find M3U URL in page source
        if not m3u_url:
            print("[*] Strategy 3: Searching page source with regex...")
            import re
            page_source = driver.page_source
            # Look for patterns like "M3U Playlist : http://..." or similar
            patterns = [
                r'M3U Playlist\s*:?\s*(https?://[^\s<>"\']+)',
                r'm3u["\']?\s*:?\s*["\']?(https?://[^\s<>"\']+)',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, page_source, re.IGNORECASE)
                if matches:
                    m3u_url = matches[0]
                    print(f"[✓] Found M3U URL via regex")
                    break

        # Strategy 4: Find any URL that contains "m3u" in page text
        if not m3u_url:
            print("[*] Strategy 4: Searching for any 'm3u' URL in page text...")
            try:
                all_text = driver.find_element(By.TAG_NAME, "body").text
                lines = all_text.split("\n")
                for line in lines:
                    if "m3u" in line.lower() and "http" in line:
                        # Extract the URL
                        import re
                        urls = re.findall(r'https?://[^\s<>"\']+', line)
                        if urls:
                            m3u_url = urls[0]
                            print(f"[✓] Found M3U URL in page text")
                            break
            except Exception as e:
                print(f"[!] Strategy 4 failed: {e}")

        if m3u_url:
            # Always try to extract username and password from the page
            print("[*] Extracting username and password credentials...")

            # Strategy 1: Look for elements with specific IDs or labels
            try:
                # Try to find Username field
                username_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Username') or contains(text(), 'username')]")
                for elem in username_elements:
                    parent = elem.find_element(By.XPATH, "./..")
                    text = parent.text
                    # Extract value after the label
                    if ":" in text:
                        parts = text.split(":", 1)
                        if len(parts) > 1:
                            potential_username = parts[1].strip()
                            if potential_username and potential_username != "USERNAME" and len(potential_username) > 3:
                                username = potential_username
                                print(f"[✓] Found username: {username}")
                                break

                # Try to find Password - look for input field with id="pass_word"
                print("[*] Looking for password input field...")
                try:
                    password_input = driver.find_element(By.ID, "pass_word")
                    password = password_input.get_attribute("value")
                    if password and password != "PASSWORD" and len(password) > 3:
                        print(f"[✓] Found password in #pass_word input: {password}")
                    else:
                        print(f"[!] Password input found but value seems invalid: {password}")
                        password = None

                except Exception as e:
                    print(f"[!] Could not find password input #pass_word: {e}")

                    # Fallback 1: Try clicking show button and reading from strong.text-domain
                    try:
                        print("[*] Trying alternative: clicking show button...")
                        show_btn = driver.find_element(By.ID, "showbtnspan")
                        show_btn.click()
                        time.sleep(1)
                        print("[✓] Clicked show password button")

                        password_element = driver.find_element(By.CSS_SELECTOR, "strong.text-domain")
                        password = password_element.text.strip()
                        if password and password != "PASSWORD" and len(password) > 3:
                            print(f"[✓] Found password in strong.text-domain: {password}")
                        else:
                            password = None

                    except Exception as e2:
                        print(f"[!] Could not get password via show button: {e2}")

                        # Fallback 2: try to find password using labels
                        password_elements = driver.find_elements(By.XPATH, "//*[contains(text(), 'Password') or contains(text(), 'password')]")
                        for elem in password_elements:
                            parent = elem.find_element(By.XPATH, "./..")
                            text = parent.text
                            # Extract value after the label
                            if ":" in text:
                                parts = text.split(":", 1)
                                if len(parts) > 1:
                                    potential_password = parts[1].strip()
                                    if potential_password and potential_password != "PASSWORD" and len(potential_password) > 3:
                                        password = potential_password
                                        print(f"[✓] Found password via label: {password}")
                                        break

            except Exception as e:
                print(f"[!] Error searching for credentials with labels: {e}")

            # Strategy 2: Use regex to find credentials in page text
            if not username or not password:
                import re
                page_text = driver.find_element(By.TAG_NAME, "body").text

                # Look for patterns like "Username: value" or "username: value"
                if not username:
                    username_patterns = [
                        r'Username\s*:?\s*([^\s\n]+)',
                        r'username\s*:?\s*([^\s\n]+)',
                        r'User\s*:?\s*([^\s\n]+)',
                    ]
                    for pattern in username_patterns:
                        match = re.search(pattern, page_text)
                        if match:
                            potential_username = match.group(1).strip()
                            if potential_username != "USERNAME" and len(potential_username) > 3:
                                username = potential_username
                                print(f"[✓] Found username via regex: {username}")
                                break

                # Look for patterns like "Password: value" or "password: value"
                if not password:
                    password_patterns = [
                        r'Password\s*:?\s*([^\s\n]+)',
                        r'password\s*:?\s*([^\s\n]+)',
                    ]
                    for pattern in password_patterns:
                        match = re.search(pattern, page_text)
                        if match:
                            potential_password = match.group(1).strip()
                            if potential_password != "PASSWORD" and len(potential_password) > 3:
                                password = potential_password
                                print(f"[✓] Found password via regex: {password}")
                                break

            # Check if URL contains placeholders and replace them
            if "USERNAME" in m3u_url or "PASSWORD" in m3u_url:
                print("[*] M3U URL contains placeholders. Attempting to replace them...")
                if username:
                    m3u_url = m3u_url.replace("USERNAME", username)
                    print(f"[✓] Replaced USERNAME placeholder with: {username}")
                else:
                    print("[!] WARNING: Could not find actual username, URL still contains USERNAME placeholder")

                if password:
                    m3u_url = m3u_url.replace("PASSWORD", password)
                    print(f"[✓] Replaced PASSWORD placeholder with: {password}")
                else:
                    print("[!] WARNING: Could not find actual password, URL still contains PASSWORD placeholder")

            print("\n" + "=" * 60)
            print("M3U PLAYLIST URL FOUND:")
            print("=" * 60)
            print(m3u_url)
            print("=" * 60 + "\n")
            print(f"[*] Username: {username}")
            print(f"[*] Password: {password}")
            return (m3u_url, username, password)
        else:
            print("[!] M3U Playlist URL not found on the page")
            print("[*] Page text preview:")
            try:
                body_text = driver.find_element(By.TAG_NAME, "body").text
                print(body_text[:500] + "...")
            except:
                pass
            return (None, None, None)

    except Exception as e:
        print(f"[!] Error extracting M3U Playlist: {e}")
        import traceback
        traceback.print_exc()
        return (None, None, None)


def save_to_iboplayer(username, password, max_retries=3):
    """
    Save the playlist to IBO Player using their API.

    Args:
        username: The username from the Zazy service
        password: The password from the Zazy service
        max_retries: Maximum number of retry attempts (default: 3)

    Returns:
        True if successful, False otherwise
    """
    print("\n[*] Saving playlist to IBO Player...")

    # Validate required configuration
    if not IBOPLAYER_COOKIE:
        print("[!] IBOPLAYER_COOKIE not configured in .env file")
        return False

    if not IBOPLAYER_PLAYLIST_URL_ID:
        print("[!] IBOPLAYER_PLAYLIST_URL_ID not configured in .env file")
        return False

    # Prepare the API endpoint
    api_url = "https://iboplayer.com/frontend/device/savePlaylist"

    # Prepare the request headers
    headers = {
        "Content-Type": "application/json",
        "Cookie": IBOPLAYER_COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

    # Prepare the payload
    payload = {
        "current_playlist_url_id": IBOPLAYER_PLAYLIST_URL_ID,
        "password": password,
        "pin": "",
        "playlist_name": IBOPLAYER_PLAYLIST_NAME,
        "playlist_type": "xc",
        "playlist_url": IBOPLAYER_PLAYLIST_URL,
        "protect": "false",
        "username": username,
        "xml_url": ""
    }

    print(f"[*] API URL: {api_url}")
    print(f"[*] Playlist Name: {IBOPLAYER_PLAYLIST_NAME}")
    print(f"[*] Playlist URL: {IBOPLAYER_PLAYLIST_URL}")
    print(f"[*] Username: {username}")
    print(f"[*] Password: {password}")

    # Retry loop with exponential backoff
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\n[*] Attempt {attempt}/{max_retries}: Sending request to IBO Player API...")

            # Make the POST request
            response = requests.post(
                api_url,
                json=payload,
                headers=headers,
                timeout=30
            )

            # Check the response
            print(f"[*] Response status code: {response.status_code}")

            if response.status_code == 200:
                print("[✓] Playlist saved to IBO Player successfully!")
                try:
                    response_data = response.json()
                    print(f"[*] Response: {json.dumps(response_data, indent=2)}")
                except:
                    print(f"[*] Response text: {response.text}")
                return True

            elif response.status_code >= 400 and response.status_code < 500:
                # Client error - don't retry
                print(f"[!] Client error ({response.status_code}): {response.text}")
                print("[!] This is likely a configuration issue. Please check your IBO Player settings.")
                return False

            else:
                # Server error or other - retry
                print(f"[!] Server error ({response.status_code}): {response.text}")

                if attempt < max_retries:
                    # Exponential backoff: 2, 4, 8 seconds
                    wait_time = 2 ** attempt
                    print(f"[*] Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print("[!] Max retries reached. Could not save playlist to IBO Player.")
                    return False

        except requests.exceptions.Timeout:
            print(f"[!] Request timed out (attempt {attempt}/{max_retries})")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("[!] Max retries reached. Request timed out.")
                return False

        except requests.exceptions.RequestException as e:
            print(f"[!] Request failed (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("[!] Max retries reached. Request failed.")
                return False

        except Exception as e:
            print(f"[!] Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            return False

    return False


def download_m3u_file(host, username, password, output_filename=None):
    """
    Download M3U playlist from Xtream Codes API.

    Args:
        host: The Xtream host URL (e.g., http://live.zazytv.com)
        username: The username for the Xtream service
        password: The password for the Xtream service
        output_filename: Optional custom filename. If not provided, generates timestamped name.

    Returns:
        tuple: (success: bool, file_path: str or None)
    """
    print("\n[*] Downloading M3U playlist file...")

    if not host or not username or not password:
        print("[!] Missing required parameters: host, username, or password")
        return (False, None)

    # Create playlists directory if it doesn't exist
    playlists_dir = os.path.join(os.getcwd(), "playlists")
    os.makedirs(playlists_dir, exist_ok=True)
    print(f"[*] Playlists directory: {playlists_dir}")

    # Generate timestamped filename if not provided
    if output_filename is None:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        output_filename = f"zazy_playlist_{timestamp}.m3u"

    print(f"[*] Output file: {output_filename}")

    # Remove trailing slash from host if present
    host = host.rstrip('/')

    # Construct the Xtream Codes API URL for M3U download
    # Format: http://host/get.php?username=XXX&password=YYY&type=m3u_plus&output=ts
    m3u_url = f"{host}/get.php?username={username}&password={password}&type=m3u_plus&output=ts"

    print(f"[*] M3U Download URL: {m3u_url}")

    try:
        # Make the request to download the M3U file
        print("[*] Sending request to Xtream API...")
        response = requests.get(m3u_url, timeout=30)

        # Check if the request was successful
        if response.status_code == 200:
            # Save the content to file in playlists directory
            file_path = os.path.join(playlists_dir, output_filename)

            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(response.text)

            # Get file size
            file_size = os.path.getsize(file_path)
            file_size_kb = file_size / 1024

            print(f"[✓] M3U playlist downloaded successfully!")
            print(f"[*] File saved to: {file_path}")
            print(f"[*] File size: {file_size_kb:.2f} KB ({file_size} bytes)")

            # Display first few lines as confirmation
            lines = response.text.split('\n')[:5]
            print("\n[*] File preview (first 5 lines):")
            for line in lines:
                print(f"    {line}")

            return (True, file_path)

        else:
            print(f"[!] Failed to download M3U file. Status code: {response.status_code}")
            print(f"[!] Response: {response.text[:200]}")
            return (False, None)

    except requests.exceptions.Timeout:
        print("[!] Request timed out while downloading M3U file")
        return (False, None)

    except requests.exceptions.RequestException as e:
        print(f"[!] Request failed: {e}")
        return (False, None)

    except IOError as e:
        print(f"[!] Failed to save file: {e}")
        return (False, None)

    except Exception as e:
        print(f"[!] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return (False, None)


def main():
    driver = get_driver()

    try:
        navigate_and_add_free_trial(driver)

        # Fill the registration form on the checkout page
        pwd = fill_checkout_form(driver)

        if not SKIP_LOGIN:
            login(driver)

        apply_promo(driver)

        complete_order(driver, pwd)

        # Navigate to service details and extract M3U Playlist
        m3u_url, username, password = get_m3u_playlist(driver)

        if m3u_url:
            print("\n[✓] SUCCESS! M3U Playlist URL has been retrieved.")
            print(f"[*] M3U URL: {m3u_url}")

            # Save playlist to IBO Player if credentials are available
            if username and password:
                print("\n[*] Attempting to save playlist to IBO Player...")
                ibo_success = save_to_iboplayer(username, password)

                if ibo_success:
                    print("\n[✓] Playlist successfully saved to IBO Player!")
                else:
                    print("\n[!] Failed to save playlist to IBO Player. Check the logs above for details.")

                # Download M3U playlist file
                download_success, m3u_file_path = download_m3u_file(
                    IBOPLAYER_PLAYLIST_URL,
                    username,
                    password
                )

                if download_success:
                    print(f"\n[✓] M3U playlist file downloaded successfully to: {m3u_file_path}")
                else:
                    print("\n[!] Failed to download M3U playlist file. Check the logs above for details.")
            else:
                print("\n[!] Could not save to IBO Player: username or password not found.")
                print("[*] Username and password are required for the IBO Player API.")
        else:
            print("\n[!] Could not retrieve M3U Playlist URL automatically.")
            print("[*] You may need to navigate manually to the service details.")

        print("\n[✓] Automation complete!")

        # Get 2captcha balance for notification
        captcha_balance = None
        if solver:
            try:
                captcha_balance = solver.balance()
                print(f"[*] 2captcha balance: ${captcha_balance}")
            except Exception as e:
                print(f"[!] Could not retrieve 2captcha balance: {e}")

        # Send completion success notification
        if m3u_url:
            notifier.notify_success(m3u_url, username, captcha_balance)

        # Check if we should keep browser open or exit
        auto_exit = os.getenv("AUTO_EXIT", "True").lower() == "true"

        if auto_exit:
            print("[*] AUTO_EXIT enabled - closing browser and exiting...")
            driver.quit()
            print("[✓] Browser closed. Script completed successfully.")
        else:
            print("[*] The browser will remain open to view the result.")
            print("[*] Press Ctrl+C in this terminal when you are ready to quit.")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                print("\n[*] Interrupt received. Closing browser...")
                driver.quit()

    except Exception as e:
        import traceback
        error_traceback = traceback.format_exc()
        print(f"\n[✗] An error occurred: {e}")
        print(error_traceback)

        # Get 2captcha balance for notification
        captcha_balance = None
        if solver:
            try:
                captcha_balance = solver.balance()
                print(f"[*] 2captcha balance: ${captcha_balance}")
            except Exception as balance_error:
                print(f"[!] Could not retrieve 2captcha balance: {balance_error}")

        # Send error notification
        notifier.notify_error(str(e), error_traceback, captcha_balance)

        # Always quit driver on error
        try:
            driver.quit()
            print("[*] Browser closed.")
        except:
            pass

        # Exit with error code for cron/docker monitoring
        exit(1)


if __name__ == '__main__':
    main()