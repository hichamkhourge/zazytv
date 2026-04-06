"""
UGEEN API Scraper - Automated Service Activation

Automates the UGEEN.LIVE service activation process with anti-detection measures
and automatic CAPTCHA solving.
"""

import requests
import json
import base64
import time
import random
import os
import sys
import traceback
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import undetected_chromedriver as uc
from fake_useragent import UserAgent
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC

# Load environment variables
load_dotenv()

# Configuration from environment variables
UGEEN_EMAIL = os.getenv('UGEEN_EMAIL', '')
UGEEN_PASSWORD = os.getenv('UGEEN_PASSWORD', '')
UGEEN_URL = os.getenv('UGEEN_URL', 'http://ugeen.live')
UGEEN_PACKAGE_ID = os.getenv('UGEEN_PACKAGE_ID', '384')
TWOCAPTCHA_API_KEY = os.getenv('TWOCAPTCHA_API_KEY', '')

# UGEEN-specific settings
UGEEN_HEADLESS = os.getenv('UGEEN_HEADLESS', 'True').lower() == 'true'
UGEEN_SESSION_DIR = os.getenv('UGEEN_SESSION_DIR', './ugeen_sessions')
UGEEN_DATA_DIR = os.getenv('UGEEN_DATA_DIR', './ugeen_data')

# Create directories if they don't exist
Path(UGEEN_SESSION_DIR).mkdir(parents=True, exist_ok=True)
Path(UGEEN_DATA_DIR).mkdir(parents=True, exist_ok=True)

# Session file path
SESSION_FILE = os.path.join(UGEEN_SESSION_DIR, 'ugeen_session.json')
MAX_LOGIN_RETRIES = 5

# Import Telegram notifier
try:
    from telegram_notifier import TelegramNotifier
    telegram = TelegramNotifier()
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    print("[!] telegram_notifier not available, notifications disabled")

def log_message(message: str, level: str = "INFO"):
    """Print timestamped log message"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}")

def notify_telegram(status: str, message: str, details: str = None):
    """Send Telegram notification if available"""
    if TELEGRAM_AVAILABLE:
        # Customize message for UGEEN
        full_message = f"🔧 <b>UGEEN Scraper</b>\n\n<b>{status}</b>\n{message}"
        telegram.send_notification(status, message, details)
    return None

def decode_jwt(token):
    """Decode JWT token without verification"""
    try:
        parts = token.split('.')
        if len(parts) != 3: return None

        header, payload = parts[0], parts[1]
        header += '=' * (4 - len(header) % 4)
        payload += '=' * (4 - len(payload) % 4)

        return {
            'header': json.loads(base64.urlsafe_b64decode(header)),
            'payload': json.loads(base64.urlsafe_b64decode(payload))
        }
    except Exception as e:
        print(f'Error decoding JWT: {e}')
        return None

def random_delay(min_seconds=1, max_seconds=3):
    """Sleep for a random amount of time to mimic human behavior"""
    time.sleep(random.uniform(min_seconds, max_seconds))

def type_like_human(element, text, min_delay=0.05, max_delay=0.2):
    """Type text character by character with random delays"""
    element.clear()
    random_delay(0.3, 0.7)
    for char in text:
        element.send_keys(char)
        time.sleep(random.uniform(min_delay, max_delay))

def scroll_randomly(driver):
    """Perform random scrolling to appear more human-like"""
    scroll_amount = random.randint(100, 500)
    direction = random.choice(['down', 'up'])
    if direction == 'down':
        driver.execute_script(f"window.scrollBy(0, {scroll_amount});")
    else:
        driver.execute_script(f"window.scrollBy(0, -{scroll_amount});")
    random_delay(0.5, 1.5)

def move_mouse_randomly(driver):
    """Simulate random mouse movements"""
    try:
        # Move mouse to random coordinates
        x = random.randint(100, 800)
        y = random.randint(100, 600)
        driver.execute_script(f"""
            var event = new MouseEvent('mousemove', {{
                'clientX': {x},
                'clientY': {y},
                'bubbles': true
            }});
            document.dispatchEvent(event);
        """)
    except:
        pass

def detect_recaptcha(driver):
    """Check if reCAPTCHA is present AND blocking on the page"""
    try:
        # Check for reCAPTCHA challenge iframe (the actual blocking one)
        recaptcha_frames = driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha"]')

        # Check if it's visible and blocking (not just the invisible badge)
        for frame in recaptcha_frames:
            try:
                # Check if frame is visible and has dimensions
                if frame.is_displayed():
                    size = frame.size
                    # Badge is small (~70x70), challenge is bigger (~400x600)
                    if size['width'] > 300 or size['height'] > 300:
                        print(f"   [DEBUG] Found visible reCAPTCHA challenge: {size['width']}x{size['height']}px")
                        return True
            except:
                pass

        # Check for reCAPTCHA overlay/popup (aggressive blocking)
        overlay = driver.find_elements(By.CSS_SELECTOR, '.rc-anchor, .recaptcha-checkbox')
        if overlay and any(el.is_displayed() for el in overlay):
            print("   [DEBUG] Found visible reCAPTCHA checkbox")
            return True

        return False
    except:
        return False

def get_recaptcha_sitekey(driver):
    """Extract reCAPTCHA site key from the page"""
    try:
        # Method 1: Check iframe src
        iframes = driver.find_elements(By.CSS_SELECTOR, 'iframe[src*="recaptcha"]')
        for iframe in iframes:
            src = iframe.get_attribute('src')
            if 'k=' in src:
                sitekey = src.split('k=')[1].split('&')[0]
                return sitekey

        # Method 2: Check div with data-sitekey
        recaptcha_divs = driver.find_elements(By.CSS_SELECTOR, '[data-sitekey]')
        for div in recaptcha_divs:
            sitekey = div.get_attribute('data-sitekey')
            if sitekey:
                return sitekey

        # Method 3: Check JavaScript variables
        sitekey = driver.execute_script(r"""
            var sitekey = null;
            var scripts = document.getElementsByTagName('script');
            for (var i = 0; i < scripts.length; i++) {
                var text = scripts[i].textContent || scripts[i].innerText;
                var match = text.match(/sitekey['":\s]+([a-zA-Z0-9_-]{40})/);
                if (match) return match[1];
            }
            return null;
        """)
        if sitekey:
            return sitekey

        return None
    except Exception as e:
        print(f"Error extracting sitekey: {e}")
        return None

def solve_recaptcha_with_2captcha(driver, page_url):
    """Solve reCAPTCHA using 2captcha service"""
    try:
        print("🔧 Attempting to solve reCAPTCHA with 2captcha...")

        # Get the site key
        sitekey = get_recaptcha_sitekey(driver)
        if not sitekey:
            print("✗ Could not find reCAPTCHA sitekey")
            return False

        print(f"✓ Found reCAPTCHA sitekey: {sitekey}")

        # Submit captcha to 2captcha
        print("Submitting captcha to 2captcha service...")
        submit_url = "http://2captcha.com/in.php"
        submit_params = {
            'key': TWOCAPTCHA_API_KEY,
            'method': 'userrecaptcha',
            'googlekey': sitekey,
            'pageurl': page_url,
            'json': 1
        }

        response = requests.get(submit_url, params=submit_params, timeout=30)
        result = response.json()

        if result.get('status') != 1:
            print(f"✗ 2captcha submission failed: {result.get('request', 'Unknown error')}")
            return False

        captcha_id = result.get('request')
        print(f"✓ Captcha submitted. ID: {captcha_id}")
        print("Waiting for solution (this may take 30-60 seconds)...")

        # Poll for solution
        result_url = "http://2captcha.com/res.php"
        max_attempts = 30  # 30 attempts x 5 seconds = 2.5 minutes max

        for attempt in range(max_attempts):
            time.sleep(5)  # Wait 5 seconds between checks

            result_params = {
                'key': TWOCAPTCHA_API_KEY,
                'action': 'get',
                'id': captcha_id,
                'json': 1
            }

            response = requests.get(result_url, params=result_params, timeout=30)
            result = response.json()

            if result.get('status') == 1:
                # Solution ready!
                captcha_solution = result.get('request')
                print(f"✓ reCAPTCHA solved! (took {(attempt + 1) * 5} seconds)")

                # Inject the solution into the page
                print("Injecting solution into page...")
                driver.execute_script(f"""
                    // Set the response in the textarea
                    var textarea = document.getElementById('g-recaptcha-response');
                    if (textarea) {{
                        textarea.innerHTML = '{captcha_solution}';
                        textarea.value = '{captcha_solution}';
                    }}

                    // Override grecaptcha.getResponse
                    if (typeof grecaptcha !== 'undefined') {{
                        grecaptcha.getResponse = function() {{ return '{captcha_solution}'; }};
                    }}
                """)

                # Try multiple ways to submit the form
                print("Attempting to submit form after captcha...")
                try:
                    # Method 1: Try to find and execute callback
                    driver.execute_script("""
                        var callback = window.recaptchaCallback || window.onRecaptchaSuccess;
                        if (callback && typeof callback === 'function') {
                            callback();
                            return true;
                        }
                        return false;
                    """)

                    # Method 2: Close/remove any reCAPTCHA overlays
                    print("Removing reCAPTCHA overlays...")
                    driver.execute_script("""
                        // Remove the reCAPTCHA overlay/backdrop
                        var overlays = document.querySelectorAll('div[style*="z-index: 2000000000"]');
                        overlays.forEach(function(el) { el.remove(); });

                        // Try to close reCAPTCHA iframe
                        var iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                        iframes.forEach(function(iframe) {
                            if (iframe.style.width === '100%' || iframe.style.height === '100%') {
                                iframe.style.display = 'none';
                            }
                        });
                    """)

                    # Method 3: Try to click the submit button using JavaScript (bypasses overlay)
                    time.sleep(2)
                    print("Clicking submit button via JavaScript...")
                    driver.execute_script("""
                        var submitBtn = document.getElementById('submit');
                        if (submitBtn) {
                            submitBtn.click();
                        }
                    """)
                    print("✓ Form resubmitted after captcha")
                except Exception as e:
                    print(f"Note: Could not resubmit form: {e}")

                time.sleep(5)
                return True

            elif result.get('request') == 'CAPCHA_NOT_READY':
                print(f"  Waiting for solution... ({attempt + 1}/{max_attempts})")
                continue
            else:
                print(f"✗ 2captcha error: {result.get('request', 'Unknown error')}")
                return False

        print("✗ Timeout waiting for captcha solution")
        return False

    except Exception as e:
        print(f"✗ Error solving reCAPTCHA: {e}")
        import traceback
        traceback.print_exc()
        return False

def save_session(cookies, jwt_token):
    """Save cookies and JWT token for session reuse"""
    try:
        session_data = {
            'cookies': cookies,
            'jwt_token': jwt_token,
            'timestamp': time.time()
        }
        with open(SESSION_FILE, 'w') as f:
            json.dump(session_data, f)
        print(f"✓ Session saved to {SESSION_FILE}")
        return True
    except Exception as e:
        print(f"Warning: Could not save session: {e}")
        return False

def load_session():
    """Load saved session if available and not expired"""
    try:
        if not os.path.exists(SESSION_FILE):
            return None

        with open(SESSION_FILE, 'r') as f:
            session_data = json.load(f)

        # Check if session is less than 24 hours old
        age = time.time() - session_data.get('timestamp', 0)
        if age > 86400:  # 24 hours in seconds
            print("Session expired (>24 hours old)")
            return None

        print(f"✓ Loaded saved session (age: {int(age/3600)} hours)")
        return session_data
    except Exception as e:
        print(f"Warning: Could not load session: {e}")
        return None

def verify_session(jwt_token, api_base):
    """Verify if saved JWT token is still valid"""
    try:
        headers = {
            'Authorization': f'Bearer {jwt_token}',
            'Accept': 'application/json',
        }
        # Try to make a simple API call
        response = requests.get(f"{api_base}/codes", headers=headers, timeout=5)
        return response.status_code != 401
    except:
        return False

def create_stealth_driver(proxy=None, headless=None):
    """Create undetected Chrome driver with stealth options"""
    # Use global setting if not specified
    if headless is None:
        headless = UGEEN_HEADLESS

    # Random user agent
    ua = UserAgent()
    user_agent = ua.random

    # Find Chrome binary manually first
    browser_path = None
    search_paths = [
        '/usr/bin/google-chrome',
        '/usr/bin/google-chrome-stable',
        '/usr/bin/chromium-browser',
        '/usr/bin/chromium',
        '/snap/bin/chromium',
        '/usr/bin/chrome',
        '/opt/google/chrome/chrome'
    ]

    for path in search_paths:
        if os.path.exists(path) and os.path.isfile(path):
            browser_path = path
            print(f"Found Chrome at: {browser_path}")
            break

    if not browser_path:
        print("⚠️ Could not find Chrome binary, letting undetected-chromedriver auto-detect...")

    # Detect version
    version_main = None
    if browser_path:
        try:
            import subprocess
            result = subprocess.run([browser_path, '--version'],
                                 capture_output=True, text=True, timeout=5)
            version_str = result.stdout.strip()
            version_parts = version_str.split()
            for part in version_parts:
                if part and part[0].isdigit():
                    version_main = int(part.split('.')[0])
                    print(f"Detected Chrome version: {version_main}")
                    break
        except Exception as e:
            print(f"⚠️ Could not detect version: {e}")

    # Create minimal options - let undetected-chromedriver handle most things
    options = uc.ChromeOptions()

    # Only add essential arguments
    options.add_argument('--no-sandbox')
    options.add_argument('--disable-dev-shm-usage')

    if headless:
        options.add_argument('--headless=new')

    if proxy:
        options.add_argument(f'--proxy-server={proxy}')
        print(f"Using proxy: {proxy}")

    # Create driver - pass browser_executable_path directly to uc.Chrome
    # This is the proper way to specify Chrome location for undetected-chromedriver
    try:
        print("Creating undetected-chromedriver instance...")
        if browser_path:
            driver = uc.Chrome(
                browser_executable_path=browser_path,
                options=options,
                version_main=version_main,
                use_subprocess=False
            )
        else:
            # Let it auto-detect everything
            driver = uc.Chrome(
                options=options,
                use_subprocess=False
            )
    except Exception as e:
        print(f"⚠️ Driver creation failed: {e}")
        # Last resort - absolute minimum configuration
        print("Trying with minimal configuration...")
        options_minimal = uc.ChromeOptions()
        options_minimal.add_argument('--no-sandbox')
        options_minimal.add_argument('--disable-dev-shm-usage')
        driver = uc.Chrome(options=options_minimal, use_subprocess=False)

    # Set user agent via CDP
    try:
        driver.execute_cdp_cmd('Network.setUserAgentOverride', {
            "userAgent": user_agent
        })
        driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    except:
        pass

    print(f"✓ Stealth browser created")
    return driver

def perform_login_with_retries(driver, wait, config, retry_count=0):
    """Perform login with human-like behavior and retry logic"""
    if retry_count >= MAX_LOGIN_RETRIES:
        print(f"✗ Max login retries ({MAX_LOGIN_RETRIES}) reached")
        return None

    try:
        print(f"\n{'='*50}")
        print(f"Login attempt {retry_count + 1}/{MAX_LOGIN_RETRIES}")
        print(f"{'='*50}")

        # Navigate to login page
        driver.get(config['url'])

        # Wait longer on first load to let page fully render
        initial_wait = random.uniform(3, 6) + (retry_count * 2)  # Longer on retries
        print(f"Waiting {initial_wait:.1f}s for page to fully load...")
        time.sleep(initial_wait)

        # Simulate human behavior - more activity on retries
        for _ in range(random.randint(2, 4)):
            scroll_randomly(driver)
            move_mouse_randomly(driver)
            random_delay(0.5, 1.5)

        # Wait for and fill email field
        print('Locating email field...')
        username_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '#email')))
        random_delay(1, 2)  # Longer pause before starting to type

        # Type like human
        print('Entering email...')
        type_like_human(username_field, config['username'])
        random_delay(1, 2)  # Longer pause between fields

        # Fill password field
        print('Entering password...')
        password_field = driver.find_element(By.CSS_SELECTOR, '#password')
        type_like_human(password_field, config['password'], min_delay=0.08, max_delay=0.25)
        random_delay(1.5, 3)  # Even longer pause before clicking

        # More human-like behavior before clicking login
        scroll_randomly(driver)
        move_mouse_randomly(driver)
        random_delay(1, 2)

        # Check for reCAPTCHA before clicking (only block if actually visible)
        print("Checking for reCAPTCHA...")
        if detect_recaptcha(driver):
            print("⚠️ reCAPTCHA challenge is BLOCKING the page!")

            # Try to solve with 2captcha
            if solve_recaptcha_with_2captcha(driver, config['url']):
                print("✓ reCAPTCHA solved successfully with 2captcha!")
                random_delay(2, 3)
            else:
                print("⚠️ 2captcha solving failed, waiting 30 seconds...")
                time.sleep(30)

                # Check again - maybe it passed
                if detect_recaptcha(driver):
                    print("reCAPTCHA still blocking. Retrying with new browser instance...")
                    driver.quit()
                    random_delay(10, 15)  # Much longer delay between retries

                    # Create new driver with different fingerprint
                    driver = create_stealth_driver()
                    wait = WebDriverWait(driver, 20)
                    return perform_login_with_retries(driver, wait, config, retry_count + 1)
                else:
                    print("✓ reCAPTCHA passed automatically!")
        else:
            print("✓ No blocking reCAPTCHA detected")

        # Click login button
        print('Clicking login button...')
        login_button = driver.find_element(By.CSS_SELECTOR, '#submit')
        login_button.click()

        # Wait and check for reCAPTCHA
        print('Waiting for authentication...')
        time.sleep(5)

        if detect_recaptcha(driver):
            print("⚠️ reCAPTCHA appeared AFTER login click!")

            # Try to solve with 2captcha
            if solve_recaptcha_with_2captcha(driver, driver.current_url):
                print("✓ reCAPTCHA solved successfully with 2captcha!")
                time.sleep(3)  # Wait for submission
            else:
                print("⚠️ 2captcha solving failed, waiting 20 seconds...")
                time.sleep(20)

                # Check again
                if detect_recaptcha(driver):
                    print("reCAPTCHA still present. Retrying with different pattern...")
                    driver.quit()
                    random_delay(5, 10)  # Longer delay between retries

                    # Create new driver with different fingerprint
                    driver = create_stealth_driver()
                    wait = WebDriverWait(driver, 20)
                    return perform_login_with_retries(driver, wait, config, retry_count + 1)

        # Try to extract JWT token
        print('Extracting JWT token from localStorage...')
        jwt_token = None

        # Poll for up to 30 seconds
        for i in range(15):
            jwt_token = driver.execute_script("return window.localStorage.getItem('jsonwebToken');")
            if jwt_token:
                print(f"✓ JWT token extracted successfully!")
                break
            print(f"  Waiting for token... ({i+1}/15)")
            time.sleep(2)

        if not jwt_token:
            print("✗ JWT token not found in localStorage")

            # Check if we're still on login page (login failed)
            current_url = driver.current_url
            if 'signin' in current_url:
                print("Still on login page - login likely failed")
                print("Retrying with different approach...")
                return perform_login_with_retries(driver, wait, config, retry_count + 1)

            return None

        # Get cookies for session persistence
        cookies = driver.get_cookies()

        return {
            'jwt_token': jwt_token,
            'cookies': cookies,
            'driver': driver
        }

    except Exception as e:
        print(f"Login error: {e}")
        import traceback
        traceback.print_exc()

        if retry_count < MAX_LOGIN_RETRIES - 1:
            print(f"Retrying... (attempt {retry_count + 2}/{MAX_LOGIN_RETRIES})")
            random_delay(3, 6)
            return perform_login_with_retries(driver, wait, config, retry_count + 1)
        return None

def scrape_with_api_auth(proxy=None):
    # Validate configuration
    if not UGEEN_EMAIL or not UGEEN_PASSWORD:
        log_message("UGEEN_EMAIL and UGEEN_PASSWORD must be set in environment", "ERROR")
        notify_telegram("❌ ERROR", "Missing UGEEN credentials in environment variables")
        return False

    if not TWOCAPTCHA_API_KEY:
        log_message("TWOCAPTCHA_API_KEY must be set in environment", "ERROR")
        notify_telegram("❌ ERROR", "Missing 2captcha API key in environment variables")
        return False

    config = {
        'url': f'{UGEEN_URL}/signin.html',
        'username': UGEEN_EMAIL,
        'password': UGEEN_PASSWORD,
        'package_id': UGEEN_PACKAGE_ID,
        'api_base': f'{UGEEN_URL}/api/v1'
    }

    print('\n' + '='*60)
    print('🚀 UGEEN API Scraper with Anti-Detection (Enhanced)')
    print('='*60 + '\n')

    # Try to load existing session first
    print('=== STEP 0: Checking for existing session ===')
    session = load_session()
    jwt_token = None

    if session:
        print('Verifying saved session...')
        if verify_session(session['jwt_token'], config['api_base']):
            print('✓ Saved session is still valid! Skipping login.')
            jwt_token = session['jwt_token']
        else:
            print('✗ Saved session expired or invalid')
            session = None

    # If no valid session, perform login
    if not jwt_token:
        print('\n=== STEP 1: Logging in with Stealth Browser ===')
        driver = None

        try:
            # Create stealth browser
            driver = create_stealth_driver(proxy=proxy)
            wait = WebDriverWait(driver, 20)

            # Perform login with retries
            login_result = perform_login_with_retries(driver, wait, config)

            if not login_result:
                print('\n✗ Login failed after all retries')
                return False

            jwt_token = login_result['jwt_token']
            cookies = login_result['cookies']

            # Save session for future use
            save_session(cookies, jwt_token)

            # Clean up browser
            driver.quit()
            print('✓ Browser closed')

        except Exception as e:
            print(f"Error during login: {e}")
            import traceback
            traceback.print_exc()
            if driver:
                driver.quit()
            return False

    # Now navigate to renew page and request code via browser
    print('\n=== STEP 2: Requesting Code via Browser ===')

    driver = None
    try:
        # Need to recreate browser with authenticated session
        print("Opening browser with authenticated session...")
        driver = create_stealth_driver()
        wait = WebDriverWait(driver, 20)

        # Load the saved session
        driver.get('http://ugeen.live')  # Navigate to site first
        time.sleep(2)

        # Set the JWT token in localStorage
        driver.execute_script(f"window.localStorage.setItem('jsonwebToken', '{jwt_token}');")

        # Navigate to renew page
        print("Navigating to renew page...")
        driver.get('http://ugeen.live/renew.html')
        random_delay(3, 5)

        # Human behavior
        scroll_randomly(driver)
        move_mouse_randomly(driver)
        random_delay(1, 2)

        # Click request code button
        print("Looking for request code button...")
        request_button = None

        # Try multiple selectors
        try:
            request_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'a.request-code')))
        except:
            try:
                request_button = driver.find_element(By.CSS_SELECTOR, 'a.btn.btn-primary.request-code')
            except:
                try:
                    request_button = driver.find_element(By.XPATH, '//a[contains(@class, "request-code")]')
                except:
                    pass

        if not request_button:
            print("✗ Request code button not found!")
            driver.quit()
            return False

        print("✓ Found request code button. Clicking...")
        request_button.click()

        # Wait for JavaScript to set the token
        print("Waiting for token to be generated...")
        time.sleep(5)

        # Extract downloadToken from localStorage
        print("Extracting downloadToken from localStorage...")
        download_token = driver.execute_script("return window.localStorage.getItem('downloadToken');")

        if not download_token:
            print("✗ downloadToken not found in localStorage!")
            driver.quit()
            return False

        print("✓ Successfully got download token!")

    except Exception as e:
        print(f"Browser operation failed: {e}")
        import traceback
        traceback.print_exc()
        if driver:
            driver.quit()
        return False

    print('\n=== STEP 3: Decoding Token ===')
    decoded = decode_jwt(download_token)
    activation_code = decoded['payload']['code']['code'] if (decoded and 'payload' in decoded and 'code' in decoded['payload']) else None

    if not activation_code:
        print("Failed to extract activation code from token.")
        return False

    print(f"✓ Decoded Activation Code: {activation_code}")

    print('\n=== STEP 4: Submit Subscription via Form ===')

    try:
        # Enter the activation code in the form
        print("Looking for code input field...")
        code_input = None

        try:
            code_input = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, 'input[type="text"]')))
        except:
            try:
                code_input = driver.find_element(By.NAME, 'code')
            except:
                try:
                    code_input = driver.find_element(By.ID, 'code')
                except:
                    pass

        if not code_input:
            print("✗ Code input field not found!")
            driver.quit()
            return False

        print("✓ Found code input field. Entering code...")
        type_like_human(code_input, activation_code)
        random_delay(1, 2)

        # Select the package option
        print(f"Selecting package option {config['package_id']}...")
        package_selected = False

        try:
            # Strategy 1: Try as a dropdown <select> element
            try:
                select_element = driver.find_element(By.CSS_SELECTOR, 'select')
                select = Select(select_element)
                # Try selecting by value
                try:
                    select.select_by_value(config['package_id'])
                    print(f"✓ Package {config['package_id']} selected from dropdown (by value)")
                    package_selected = True
                except:
                    # Try selecting by visible text
                    try:
                        for option in select.options:
                            if config['package_id'] in option.get_attribute('value') or config['package_id'] in option.text:
                                option.click()
                                print(f"✓ Package {config['package_id']} selected from dropdown (by text)")
                                package_selected = True
                                break
                    except:
                        pass
            except:
                pass

            # Strategy 2: Try as a clickable element with ID
            if not package_selected:
                try:
                    package_selector = f"#pack-plan-{config['package_id']}"
                    package_option = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, package_selector)))
                    package_option.click()
                    print(f"✓ Package {config['package_id']} clicked")
                    package_selected = True
                except:
                    pass

            # Strategy 3: Try as a radio button or checkbox by value
            if not package_selected:
                try:
                    radio_option = driver.find_element(By.CSS_SELECTOR, f'input[value="{config["package_id"]}"]')
                    radio_option.click()
                    print(f"✓ Package {config['package_id']} selected (radio/checkbox)")
                    package_selected = True
                except:
                    pass

            if package_selected:
                random_delay(1, 2)
            else:
                print(f"⚠️ Could not select package, but continuing (might be pre-selected)")

        except Exception as e:
            print(f"⚠️ Package selection error: {e}")
            print("Continuing anyway - package might be pre-selected or optional")

        # Click submit button
        print("Looking for submit button...")
        submit_button = None

        try:
            submit_button = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, 'button.submit')))
        except:
            try:
                submit_button = driver.find_element(By.CSS_SELECTOR, 'button.btn.btn-primary')
            except:
                try:
                    submit_button = driver.find_element(By.XPATH, '//button[contains(@class, "submit")]')
                except:
                    pass

        if not submit_button:
            print("✗ Submit button not found!")
            driver.quit()
            return False

        print("✓ Found submit button. Waiting 2 minutes before clicking...")
        time.sleep(120)  # Wait 2 minutes before clicking the last button

        print("Clicking submit button...")
        submit_button.click()

        # Wait for submission to complete
        print("Waiting for submission to complete...")
        time.sleep(5)

        # Check for success message or redirect
        current_url = driver.current_url
        page_source = driver.page_source.lower()

        if 'success' in page_source or 'activated' in page_source or 'dashboard' in current_url:
            log_message("SUCCESS! Subscription Activated!", "SUCCESS")

            # Save activation data
            activation_data = {
                'timestamp': datetime.now().isoformat(),
                'email': UGEEN_EMAIL,
                'package_id': UGEEN_PACKAGE_ID,
                'activation_code': activation_code,
                'status': 'success'
            }

            data_file = os.path.join(UGEEN_DATA_DIR, f'activation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            with open(data_file, 'w') as f:
                json.dump(activation_data, f, indent=2)
            log_message(f"Activation data saved to {data_file}", "INFO")

            notify_telegram(
                "✅ SUCCESS",
                "UGEEN subscription activated successfully!",
                f"Email: {UGEEN_EMAIL}\nPackage: {UGEEN_PACKAGE_ID}\nCode: {activation_code}"
            )

            driver.quit()
            return True
        else:
            log_message("Submission completed but success not confirmed", "WARNING")
            log_message(f"Current URL: {current_url}", "INFO")

            # Save data anyway
            activation_data = {
                'timestamp': datetime.now().isoformat(),
                'email': UGEEN_EMAIL,
                'package_id': UGEEN_PACKAGE_ID,
                'activation_code': activation_code,
                'status': 'unknown',
                'current_url': current_url
            }

            data_file = os.path.join(UGEEN_DATA_DIR, f'activation_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
            with open(data_file, 'w') as f:
                json.dump(activation_data, f, indent=2)

            notify_telegram(
                "⚠️ WARNING",
                "UGEEN submission completed but success not confirmed",
                f"Current URL: {current_url}"
            )

            driver.quit()
            return True  # Assume success if no error

    except Exception as e:
        log_message(f"Form submission failed: {e}", "ERROR")
        error_trace = traceback.format_exc()
        print(error_trace)

        notify_telegram(
            "❌ ERROR",
            f"Form submission failed: {str(e)}",
            error_trace[-500:]
        )

        if driver:
            driver.quit()
        return False

def main():
    """Main entry point with proxy support and proper exit codes"""
    try:
        log_message("UGEEN API Scraper starting", "INFO")
        notify_telegram("🚀 STARTED", "UGEEN scraper automation has begun")

        # Optional: Add your proxy here if you have one
        # Format: 'http://username:password@proxy_host:proxy_port'
        # or: 'http://proxy_host:proxy_port'
        proxy = None

        success = scrape_with_api_auth(proxy=proxy)

        if success:
            log_message("All done! Scraper completed successfully", "SUCCESS")
            sys.exit(0)  # Exit with success code
        else:
            log_message("Scraping failed. Check the errors above.", "ERROR")
            notify_telegram("❌ FAILED", "UGEEN scraper failed to complete")
            sys.exit(1)  # Exit with error code

    except KeyboardInterrupt:
        log_message("Scraper interrupted by user", "WARNING")
        notify_telegram("⚠️ INTERRUPTED", "UGEEN scraper was manually stopped")
        sys.exit(130)  # Standard exit code for Ctrl+C

    except Exception as e:
        log_message(f"Unexpected error in main: {e}", "ERROR")
        error_trace = traceback.format_exc()
        print(error_trace)

        notify_telegram(
            "❌ FATAL ERROR",
            f"Unexpected error: {str(e)}",
            error_trace[-500:]
        )
        sys.exit(1)

if __name__ == '__main__':
    main()
