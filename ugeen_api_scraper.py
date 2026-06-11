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
import argparse
import threading
import tempfile
import shutil
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

# Parse CLI arguments (if running from Flask API)
parser = argparse.ArgumentParser(description='Ugeen account automation (renewal)')
parser.add_argument('--user-id', type=int, help='IPTV account ID')
parser.add_argument('--username', type=str, help='Ugeen master account username (overrides env)')
parser.add_argument('--password', type=str, help='Ugeen master account password (overrides env)')
parser.add_argument('--callback-url', type=str, help='Webhook URL to send progress/results')
parser.add_argument('--test-proxy', action='store_true', help='Only test the Webshare proxy: open the IP-check URL, print the public IP, and exit')
args, unknown = parser.parse_known_args()

# Configuration from environment variables (with CLI override)
UGEEN_EMAIL = args.username if args.username else os.getenv('UGEEN_EMAIL', '')
UGEEN_PASSWORD = args.password if args.password else os.getenv('UGEEN_PASSWORD', '')
UGEEN_URL = os.getenv('UGEEN_URL', 'http://ugeen.live')
UGEEN_PACKAGE_ID = os.getenv('UGEEN_PACKAGE_ID', '384')
TWOCAPTCHA_API_KEY = os.getenv('TWOCAPTCHA_API_KEY', '')

# Webhook configuration
CALLBACK_URL = args.callback_url
USER_ID = args.user_id
WEBHOOK_AUTH_TOKEN = os.getenv('WEBHOOK_AUTH_TOKEN', '')

# UGEEN-specific settings
UGEEN_HEADLESS = os.getenv('UGEEN_HEADLESS', 'True').lower() == 'true'
UGEEN_SESSION_DIR = os.getenv('UGEEN_SESSION_DIR', './ugeen_sessions')
UGEEN_DATA_DIR = os.getenv('UGEEN_DATA_DIR', './ugeen_data')

# Webshare proxy settings (authenticated rotating proxy to change the outgoing IP)
USE_WEBSHARE_PROXY = os.getenv('USE_WEBSHARE_PROXY', 'False').lower() == 'true'
WEBSHARE_PROXY_USER = os.getenv('WEBSHARE_PROXY_USER', '')
WEBSHARE_PROXY_PASS = os.getenv('WEBSHARE_PROXY_PASS', '')
WEBSHARE_PROXY_HOST = os.getenv('WEBSHARE_PROXY_HOST', 'p.webshare.io')
WEBSHARE_PROXY_PORT = os.getenv('WEBSHARE_PROXY_PORT', '80')
PROXY_CHECK_URL = os.getenv('IPTVV_PROXY_CHECK_URL', 'https://api.ipify.org')

# Submit button configuration (for production reliability)
CAPTCHA_POST_SOLVE_WAIT = int(os.getenv('CAPTCHA_POST_SOLVE_WAIT', '3'))  # Seconds to wait after solving CAPTCHA (reduced to prevent token expiry)
SUBMIT_BUTTON_TIMEOUT = int(os.getenv('SUBMIT_BUTTON_TIMEOUT', '20'))  # Max seconds to wait for submit button
SUBMIT_MAX_RETRIES = int(os.getenv('SUBMIT_MAX_RETRIES', '3'))  # Number of submit attempts

# Create directories if they don't exist
Path(UGEEN_SESSION_DIR).mkdir(parents=True, exist_ok=True)
Path(UGEEN_DATA_DIR).mkdir(parents=True, exist_ok=True)

# Session management
MAX_LOGIN_RETRIES = 5

def get_session_file_path(email):
    """Get session file path specific to this email to prevent conflicts between accounts"""
    # Sanitize email for filename (replace @ and . with _)
    safe_email = email.replace('@', '_').replace('.', '_').replace('/', '_')
    return os.path.join(UGEEN_SESSION_DIR, f'ugeen_session_{safe_email}.json')

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

def send_webhook_success(user_id, callback_url, xtream_username=None, xtream_password=None):
    """Send success webhook to Laravel with extracted Xtream credentials"""
    if not callback_url or not user_id:
        return False

    # Always use hardcoded Ugeen host
    payload = {
        'user_id': user_id,
        'status': 'success',
        'host': 'http://ugeen.live:8080',  # Hardcoded Ugeen Xtream API host
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }

    # Add extracted credentials if available
    if xtream_username and xtream_password:
        payload['username'] = xtream_username
        payload['password'] = xtream_password
        log_message(f"Sending webhook with extracted credentials (user: {xtream_username})", "INFO")
    else:
        log_message("Sending webhook without credentials (extraction may have failed)", "WARNING")

    headers = {
        'Authorization': f'Bearer {WEBHOOK_AUTH_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        log_message(f"Sending success webhook to {callback_url}", "INFO")
        response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
        log_message(f"Webhook response: {response.status_code}", "INFO")
        return response.status_code == 200
    except Exception as e:
        log_message(f"Failed to send success webhook: {e}", "ERROR")
        return False

def send_webhook_failure(user_id, callback_url, error_message):
    """Send failure webhook to Laravel"""
    if not callback_url or not user_id:
        return False

    payload = {
        'user_id': user_id,
        'status': 'failed',
        'error': error_message,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }

    headers = {
        'Authorization': f'Bearer {WEBHOOK_AUTH_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        log_message(f"Sending failure webhook to {callback_url}", "ERROR")
        response = requests.post(callback_url, json=payload, headers=headers, timeout=30)
        log_message(f"Failure webhook response: {response.status_code}", "INFO")
        return response.status_code == 200
    except Exception as e:
        log_message(f"Failed to send failure webhook: {e}", "ERROR")
        return False

def send_webhook_progress(user_id, callback_url, message, progress, extra_data=None):
    """Send progress update webhook to Laravel"""
    if not callback_url or not user_id:
        return False

    payload = {
        'user_id': user_id,
        'status': 'in_progress',
        'message': message,
        'progress': progress,
        'timestamp': datetime.utcnow().isoformat() + 'Z'
    }

    # Add extra data if provided
    if extra_data:
        payload.update(extra_data)

    headers = {
        'Authorization': f'Bearer {WEBHOOK_AUTH_TOKEN}',
        'Content-Type': 'application/json'
    }

    try:
        response = requests.post(callback_url, json=payload, headers=headers, timeout=10)
        return response.status_code == 200
    except Exception as e:
        # Don't log progress webhook failures to avoid spam
        return False

# Global variable to track current progress step
current_progress_step = 0
progress_lock = threading.Lock()

def set_progress_step(step):
    """Thread-safe way to update progress step"""
    global current_progress_step
    with progress_lock:
        current_progress_step = step

def progress_reporter(user_id, callback_url, stop_event):
    """
    Background thread that sends detailed progress updates every 10 seconds.
    Runs until stop_event is set.
    """
    # Detailed progress milestones
    progress_steps = [
        (5, "Initializing automation script"),
        (10, "Checking for existing session"),
        (15, "Loading authentication system"),
        (20, "Navigating to login page"),
        (25, "Solving CAPTCHA challenge"),
        (35, "Submitting login credentials"),
        (45, "Verifying authentication"),
        (50, "Loading dashboard"),
        (55, "Navigating to renewal page"),
        (60, "Requesting activation code"),
        (70, "Decoding activation token"),
        (75, "Entering activation code"),
        (80, "Selecting package option"),
        (85, "Preparing subscription form"),
        (90, "Submitting subscription renewal"),
        (95, "Verifying renewal completion"),
    ]

    step_index = 0

    while not stop_event.is_set():
        try:
            # Send current progress step
            if step_index < len(progress_steps):
                progress, message = progress_steps[step_index]

                # Check if we should advance to next step based on global progress
                with progress_lock:
                    if current_progress_step > step_index:
                        step_index = current_progress_step
                        if step_index < len(progress_steps):
                            progress, message = progress_steps[step_index]

                send_webhook_progress(user_id, callback_url, message, progress)
                step_index += 1

            # Wait 10 seconds or until stopped
            stop_event.wait(10)

        except Exception as e:
            # Silently continue on errors
            pass

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

def submit_form_after_captcha(driver, page_url, form_context="login"):
    """
    Robust form submission after CAPTCHA solving with multiple strategies and retries.

    Args:
        driver: Selenium WebDriver instance
        page_url: Current page URL (for screenshots)
        form_context: Context string for logging ("login", "checkout", etc.)

    Returns:
        bool: True if submission attempted successfully, False otherwise
    """
    log_message(f"Attempting to submit {form_context} form after CAPTCHA...", "INFO")

    # Wait after CAPTCHA solution injection to let callbacks execute
    log_message(f"Waiting {CAPTCHA_POST_SOLVE_WAIT}s for CAPTCHA callbacks to complete...", "INFO")
    time.sleep(CAPTCHA_POST_SOLVE_WAIT)

    # Define multiple submit button selectors (in order of preference)
    submit_selectors = [
        (By.ID, 'submit'),                                    # #submit (most common)
        (By.CSS_SELECTOR, 'button[type="submit"]'),          # button[type="submit"]
        (By.CSS_SELECTOR, 'input[type="submit"]'),           # input[type="submit"]
        (By.CSS_SELECTOR, 'button.submit'),                  # button.submit
        (By.CSS_SELECTOR, 'button.btn-primary'),             # button.btn-primary
        (By.XPATH, '//button[contains(text(), "Sign")]'),    # Button with "Sign" text
        (By.XPATH, '//button[contains(text(), "Login")]'),   # Button with "Login" text
        (By.XPATH, '//input[@value="Submit"]'),              # Input with Submit value
    ]

    # Try multiple times with different strategies
    for attempt in range(SUBMIT_MAX_RETRIES):
        log_message(f"Submit attempt {attempt + 1}/{SUBMIT_MAX_RETRIES}", "INFO")

        # Strategy 1: Try each selector with explicit wait
        submit_button = None
        used_selector = None

        for by, selector in submit_selectors:
            try:
                wait = WebDriverWait(driver, SUBMIT_BUTTON_TIMEOUT)
                submit_button = wait.until(EC.presence_of_element_located((by, selector)))

                # Verify it's visible and enabled
                if submit_button.is_displayed() and submit_button.is_enabled():
                    used_selector = f"{by}='{selector}'"
                    log_message(f"  ✓ Found submit button: {used_selector}", "INFO")
                    break
                else:
                    submit_button = None
            except Exception as e:
                continue

        if submit_button:
            try:
                # Scroll element into view
                driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", submit_button)
                time.sleep(0.5)

                # Remove any overlays that might be blocking
                driver.execute_script("""
                    // Remove reCAPTCHA overlays
                    var overlays = document.querySelectorAll('div[style*="z-index: 2000000000"]');
                    overlays.forEach(function(el) { el.remove(); });

                    // Hide reCAPTCHA challenge iframes
                    var iframes = document.querySelectorAll('iframe[src*="recaptcha"]');
                    iframes.forEach(function(iframe) {
                        if (iframe.style.width === '100%' || iframe.style.height === '100%') {
                            iframe.style.display = 'none';
                        }
                    });
                """)

                # Try Selenium click first
                log_message(f"  Attempting Selenium click on {used_selector}...", "INFO")
                submit_button.click()
                log_message(f"  ✓ Selenium click succeeded!", "SUCCESS")

                # Wait a moment and check if it worked
                time.sleep(2)

                # Wait a bit longer for AJAX response
                time.sleep(3)

                # Capture AJAX requests made after form submission
                try:
                    ajax_data = driver.execute_script("""
                        return {
                            requests: window.ajaxRequests || [],
                            responses: window.ajaxResponses || []
                        };
                    """)

                    if ajax_data.get('requests'):
                        log_message(f"  AJAX requests detected: {len(ajax_data['requests'])}", "INFO")
                    elif not ajax_data.get('requests'):
                        log_message("  ⚠️ No AJAX requests detected - form might not be submitting!", "WARNING")

                except Exception as e:
                    log_message(f"  Could not capture AJAX logs: {e}", "DEBUG")

                # Check for any error messages or validation issues
                try:
                    error_check = driver.execute_script("""
                        // Check for common error message patterns
                        var errors = [];

                        // Check for all visible notification/alert elements (including Arabic text)
                        var errorSelectors = [
                            '.error', '.alert', '.alert-danger', '.alert-warning',
                            '.text-danger', '.notification', '.message', '.toast',
                            '[class*="error"]', '[class*="alert"]', '[class*="message"]',
                            '[role="alert"]', '.swal2-popup', '.swal-text'
                        ];

                        errorSelectors.forEach(function(selector) {
                            var elements = document.querySelectorAll(selector);
                            elements.forEach(function(el) {
                                // Check if element is visible
                                if (el.offsetParent !== null && el.offsetHeight > 0) {
                                    var text = el.textContent.trim();
                                    if (text && text.length > 0) {
                                        errors.push(text);
                                    }
                                }
                            });
                        });

                        // Also check for any newly appeared divs with text content (might be error notifications)
                        var allDivs = document.querySelectorAll('div');
                        allDivs.forEach(function(div) {
                            var style = window.getComputedStyle(div);
                            // Check for elements with high z-index (popups/notifications)
                            if (style.zIndex && parseInt(style.zIndex) > 1000) {
                                var text = div.textContent.trim();
                                if (text && text.length > 10 && text.length < 500) {
                                    errors.push('(High z-index element) ' + text);
                                }
                            }
                        });

                        // Remove duplicates
                        errors = [...new Set(errors)];

                        // Check if still on same page (URL didn't change)
                        return {
                            'errors': errors,
                            'current_url': window.location.href,
                            'page_title': document.title
                        };
                    """)
                    if error_check.get('errors'):
                        log_message(f"  ⚠️ Errors/messages detected after submit:", "WARNING")
                        for err in error_check.get('errors', []):
                            log_message(f"    - {err}", "WARNING")
                except Exception as e:
                    log_message(f"  Could not check for errors: {e}", "DEBUG")
                    pass

                return True

            except Exception as e:
                log_message(f"  Selenium click failed: {e}", "WARNING")

                # Fallback: Try JavaScript click
                try:
                    log_message("  Attempting JavaScript click...", "INFO")
                    driver.execute_script("arguments[0].click();", submit_button)
                    log_message("  ✓ JavaScript click succeeded!", "SUCCESS")
                    time.sleep(2)
                    return True
                except Exception as js_error:
                    log_message(f"  JavaScript click also failed: {js_error}", "WARNING")

        # Strategy 2: Direct JavaScript form submission (if button clicks fail)
        if attempt == SUBMIT_MAX_RETRIES - 1:  # Last attempt
            log_message("  All button clicks failed. Trying direct form.submit()...", "WARNING")
            try:
                # Find the form and submit it directly, bypassing validation
                result = driver.execute_script("""
                    var form = document.querySelector('form');
                    if (!form) return {success: false, error: 'No form found'};

                    // First, check if there's a submit handler we should call
                    var submitBtn = document.getElementById('submit');
                    if (submitBtn && submitBtn.onclick) {
                        try {
                            submitBtn.onclick.call(submitBtn);
                            return {success: true, method: 'onclick handler'};
                        } catch (e) {
                            // Continue to other methods
                        }
                    }

                    // Try form.requestSubmit() which triggers validation
                    if (form.requestSubmit) {
                        try {
                            form.requestSubmit();
                            return {success: true, method: 'requestSubmit'};
                        } catch (e) {
                            // Continue to other methods
                        }
                    }

                    // Last resort: form.submit() which bypasses validation
                    try {
                        form.submit();
                        return {success: true, method: 'form.submit'};
                    } catch (e) {
                        return {success: false, error: e.toString()};
                    }
                """)
                log_message(f"  Direct form submission result: {result}", "INFO")
                time.sleep(2)
                return True
            except Exception as form_error:
                log_message(f"  Direct form submission failed: {form_error}", "ERROR")

        # Wait before retry
        if attempt < SUBMIT_MAX_RETRIES - 1:
            wait_time = (attempt + 1) * 2  # Exponential backoff
            log_message(f"  Waiting {wait_time}s before retry...", "INFO")
            time.sleep(wait_time)

    # If we get here, all attempts failed
    log_message(f"✗ All {SUBMIT_MAX_RETRIES} submit attempts failed!", "ERROR")
    return False

def solve_recaptcha_with_2captcha(driver, page_url, use_api_login=True):
    """
    Solve reCAPTCHA using 2captcha service

    Args:
        driver: Selenium WebDriver instance
        page_url: Current page URL
        use_api_login: If True, attempt API login after solving captcha (more reliable for headless)

    Returns:
        tuple: (success: bool, jwt_token: str or None)
    """
    try:
        print("🔧 Attempting to solve reCAPTCHA with 2captcha...")

        # Get the site key
        sitekey = get_recaptcha_sitekey(driver)
        if not sitekey:
            print("✗ Could not find reCAPTCHA sitekey")
            return (False, None)

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
            return (False, None)

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

                # Try API login first (more reliable for headless/Docker environments)
                jwt_token = None
                if use_api_login:
                    print("\n🔑 Attempting API-based login (reliable for headless mode)...")
                    jwt_token = perform_api_login(
                        UGEEN_EMAIL,
                        UGEEN_PASSWORD,
                        captcha_solution,
                        UGEEN_URL
                    )

                    if jwt_token:
                        print("✓ API login successful! Returning JWT token.")
                        return (True, jwt_token)
                    else:
                        print("⚠️ API login failed, falling back to browser automation...")

                # Inject AJAX monitoring BEFORE injecting CAPTCHA solution
                print("Setting up AJAX monitoring...")
                driver.execute_script("""
                    window.ajaxRequests = [];
                    window.ajaxResponses = [];

                    // Intercept XMLHttpRequest
                    var origOpen = XMLHttpRequest.prototype.open;
                    XMLHttpRequest.prototype.open = function() {
                        var xhr = this;
                        this.addEventListener('load', function() {
                            window.ajaxResponses.push({
                                url: xhr._url,
                                status: xhr.status,
                                response: xhr.responseText ? xhr.responseText.substring(0, 500) : null
                            });
                        });
                        xhr._url = arguments[1];
                        xhr._method = arguments[0];
                        window.ajaxRequests.push({url: arguments[1], method: arguments[0]});
                        return origOpen.apply(this, arguments);
                    };
                    console.log('AJAX monitoring active');
                """)

                # Fallback: Inject the solution into the page (traditional browser automation)
                print("Injecting solution into page...")
                driver.execute_script(f"""
                    // Set the response in the textarea
                    var textarea = document.getElementById('g-recaptcha-response');
                    if (textarea) {{
                        textarea.innerHTML = '{captcha_solution}';
                        textarea.value = '{captcha_solution}';
                        textarea.style.display = '';  // Make it visible
                    }}

                    // CRITICAL: Override grecaptcha methods to mark reCAPTCHA as solved
                    if (typeof grecaptcha !== 'undefined') {{
                        // Override getResponse to return the solution
                        grecaptcha.getResponse = function() {{
                            console.log('grecaptcha.getResponse called, returning solution');
                            return '{captcha_solution}';
                        }};

                        // Mark reCAPTCHA as ready/solved
                        if (grecaptcha.enterprise) {{
                            grecaptcha.enterprise.getResponse = function() {{ return '{captcha_solution}'; }};
                        }}
                    }}

                    // CRITICAL: Trigger the reCAPTCHA callback to mark it as "solved"
                    // This is what normally happens when you solve it manually
                    try {{
                        // Find the reCAPTCHA widget ID
                        var widgets = document.querySelectorAll('[id^="rc-anchor"]');
                        if (widgets.length > 0) {{
                            // Mark the checkbox as checked visually
                            var checkmark = document.querySelector('.recaptcha-checkbox-checkmark');
                            if (checkmark) {{
                                checkmark.style.display = 'block';
                            }}

                            // Set aria-checked attribute
                            var checkbox = document.querySelector('.recaptcha-checkbox');
                            if (checkbox) {{
                                checkbox.setAttribute('aria-checked', 'true');
                                checkbox.classList.add('recaptcha-checkbox-checked');
                            }}

                            console.log('reCAPTCHA checkbox marked as checked');
                        }}
                    }} catch (e) {{
                        console.log('Could not mark checkbox: ' + e);
                    }}

                    // Execute any registered callbacks
                    try {{
                        // Look for common callback patterns
                        if (typeof onRecaptchaSuccess === 'function') {{
                            onRecaptchaSuccess('{captcha_solution}');
                        }}
                        if (typeof window.recaptchaCallback === 'function') {{
                            window.recaptchaCallback('{captcha_solution}');
                        }}

                        // Check if there's a data-callback attribute
                        var recaptchaDiv = document.querySelector('[data-callback]');
                        if (recaptchaDiv) {{
                            var callbackName = recaptchaDiv.getAttribute('data-callback');
                            if (typeof window[callbackName] === 'function') {{
                                window[callbackName]('{captcha_solution}');
                                console.log('Called callback: ' + callbackName);
                            }}
                        }}
                    }} catch (e) {{
                        console.log('Callback execution error: ' + e);
                    }}

                    // CRITICAL: Add a hidden input field with the CAPTCHA solution
                    // Many forms expect a 'recaptcha' or 'g-recaptcha-response' POST parameter
                    var form = document.querySelector('form');
                    if (form) {{
                        // Remove any existing recaptcha input to avoid duplicates
                        var existingInput = form.querySelector('input[name="recaptcha"]');
                        if (existingInput) {{
                            existingInput.remove();
                        }}

                        // Create new hidden input with the CAPTCHA solution
                        var input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = 'recaptcha';  // This matches the API endpoint expectation
                        input.value = '{captcha_solution}';
                        form.appendChild(input);

                        console.log('Added recaptcha hidden input to form');
                    }}

                    // Also store in window for callbacks
                    window.recaptchaResponse = '{captcha_solution}';

                    console.log('reCAPTCHA solution fully injected and callbacks triggered');
                """)

                # CRITICAL: Close/remove the reCAPTCHA challenge popup BEFORE submitting
                print("Closing reCAPTCHA challenge popup...")
                driver.execute_script("""
                    // Method 1: Remove ALL reCAPTCHA iframes (challenge popups)
                    var recaptchaIframes = document.querySelectorAll('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]');
                    console.log('Found ' + recaptchaIframes.length + ' reCAPTCHA challenge iframes');
                    recaptchaIframes.forEach(function(iframe) {
                        // Hide the challenge iframe
                        iframe.style.display = 'none';
                        iframe.style.visibility = 'hidden';
                        iframe.style.opacity = '0';
                        // Also try to remove it completely
                        if (iframe.parentElement) {
                            iframe.parentElement.style.display = 'none';
                        }
                    });

                    // Method 2: Remove the backdrop/overlay divs
                    var overlays = document.querySelectorAll('div[style*="z-index"]');
                    overlays.forEach(function(overlay) {
                        var zIndex = window.getComputedStyle(overlay).zIndex;
                        // Remove high z-index overlays (typical for reCAPTCHA: 2000000000+)
                        if (zIndex && parseInt(zIndex) > 1000000) {
                            console.log('Removing overlay with z-index: ' + zIndex);
                            overlay.remove();
                        }
                    });

                    // Method 3: Force hide any visible reCAPTCHA containers
                    var recaptchaContainers = document.querySelectorAll('[class*="recaptcha"], [id*="recaptcha"]');
                    recaptchaContainers.forEach(function(container) {
                        // Don't hide the invisible badge (small size)
                        if (container.offsetHeight > 100 || container.offsetWidth > 300) {
                            container.style.display = 'none';
                            console.log('Hiding large reCAPTCHA container');
                        }
                    });

                    // Method 4: Re-enable body scrolling (reCAPTCHA often disables it)
                    document.body.style.overflow = 'auto';

                    console.log('reCAPTCHA challenge popup removed');
                """)

                # Wait briefly for the DOM to update (reduced to minimize token staleness)
                time.sleep(1)

                print("Verifying CAPTCHA injection and popup removal...")
                verification = driver.execute_script("""
                    var textarea = document.getElementById('g-recaptcha-response');
                    var hiddenInput = document.querySelector('input[name="recaptcha"]');

                    // Check if any visible CAPTCHA challenges remain
                    var visibleChallenges = 0;
                    var challengeIframes = document.querySelectorAll('iframe[src*="recaptcha/api2/bframe"], iframe[src*="recaptcha/enterprise/bframe"]');
                    challengeIframes.forEach(function(iframe) {
                        if (iframe.offsetParent !== null && iframe.style.display !== 'none') {
                            visibleChallenges++;
                        }
                    });

                    return {
                        'textarea_value': textarea ? textarea.value.substring(0, 50) + '...' : null,
                        'hidden_input_value': hiddenInput ? hiddenInput.value.substring(0, 50) + '...' : null,
                        'grecaptcha_available': typeof grecaptcha !== 'undefined',
                        'visible_challenge_iframes': visibleChallenges,
                        'popup_removed': visibleChallenges === 0
                    };
                """)
                print(f"  CAPTCHA injection verification: {verification}")

                if not verification.get('popup_removed'):
                    print(f"  ⚠️ WARNING: {verification.get('visible_challenge_iframes', 0)} reCAPTCHA challenge popups still visible!")
                else:
                    print("  ✓ All reCAPTCHA challenge popups removed")

                # Use the robust submit function
                print("Submitting form after CAPTCHA solution...")
                try:
                    # First, try to execute any reCAPTCHA callbacks
                    callback_executed = driver.execute_script("""
                        var callback = window.recaptchaCallback || window.onRecaptchaSuccess;
                        if (callback && typeof callback === 'function') {
                            callback();
                            return true;
                        }
                        return false;
                    """)
                    if callback_executed:
                        print("  ✓ reCAPTCHA callback executed")

                    # Now use the robust submit function
                    submit_success = submit_form_after_captcha(driver, page_url, form_context="login")

                    if submit_success:
                        print("✓ Form submitted successfully after CAPTCHA")
                    else:
                        print("⚠️ Form submission may have failed, check logs")

                except Exception as e:
                    print(f"⚠️ Error during form submission: {e}")
                    import traceback
                    traceback.print_exc()

                # Give it time to process
                time.sleep(3)
                return (True, None)  # Success but no JWT token yet (will be extracted later)

            elif result.get('request') == 'CAPCHA_NOT_READY':
                print(f"  Waiting for solution... ({attempt + 1}/{max_attempts})")
                continue
            else:
                print(f"✗ 2captcha error: {result.get('request', 'Unknown error')}")
                return (False, None)

        print("✗ Timeout waiting for captcha solution")
        return (False, None)

    except Exception as e:
        print(f"✗ Error solving reCAPTCHA: {e}")
        import traceback
        traceback.print_exc()
        return (False, None)

def save_session(cookies, jwt_token, email):
    """Save cookies and JWT token for session reuse (per email address)"""
    try:
        session_file = get_session_file_path(email)
        session_data = {
            'cookies': cookies,
            'jwt_token': jwt_token,
            'email': email,  # Store which email this session belongs to
            'timestamp': time.time()
        }
        with open(session_file, 'w') as f:
            json.dump(session_data, f)
        print(f"✓ Session saved to {session_file}")
        return True
    except Exception as e:
        print(f"Warning: Could not save session: {e}")
        return False

def load_session(email):
    """Load saved session if available and not expired (for specific email)"""
    try:
        session_file = get_session_file_path(email)
        if not os.path.exists(session_file):
            return None

        with open(session_file, 'r') as f:
            session_data = json.load(f)

        # Verify this session belongs to the correct email
        if session_data.get('email') != email:
            print(f"⚠️ Session email mismatch (expected {email}, got {session_data.get('email')})")
            return None

        # Check if session is less than 24 hours old
        age = time.time() - session_data.get('timestamp', 0)
        if age > 86400:  # 24 hours in seconds
            print(f"Session expired (>24 hours old)")
            return None

        print(f"✓ Loaded saved session for {email} (age: {int(age/3600)} hours)")
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

def check_renewal_eligibility(jwt_token):
    """
    Check if renewal is available via Ugeen API

    Args:
        jwt_token: JWT authentication token

    Returns:
        tuple: (can_renew_now: bool, renew_remaining_minutes: int)
    """
    try:
        headers = {
            'Authorization': f'Bearer {jwt_token}',
            'Accept': 'application/json',
        }

        log_message("Checking renewal eligibility via API...", "INFO")

        # Call the overview API
        response = requests.get(
            'http://176.123.9.60:3000/v1/users/overview',
            headers=headers,
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            can_renew = data.get('can_renew_now', False)
            remaining_minutes = data.get('renew_remaining_minutes', 0)

            log_message(f"Renewal check: can_renew_now={can_renew}, remaining_minutes={remaining_minutes}", "INFO")
            return (can_renew, remaining_minutes)
        else:
            log_message(f"Renewal check failed: HTTP {response.status_code}", "WARNING")
            # On API error, assume we can proceed (fail open)
            return (True, 0)

    except Exception as e:
        log_message(f"Error checking renewal eligibility: {e}", "ERROR")
        # On exception, assume we can proceed (fail open)
        return (True, 0)

def perform_api_login(email, password, recaptcha_solution, api_base):
    """
    Perform direct API login - bypasses browser automation issues
    This is more reliable than browser automation, especially in Docker/headless mode

    Tries multiple possible API endpoints to find the correct one.
    """
    try:
        log_message("Attempting direct API login...", "INFO")

        # Try multiple possible API endpoints
        possible_endpoints = [
            "/auth/login",           # Most common REST pattern
            "/api/auth/login",       # With /api prefix
            "/api/v1/auth/login",    # With versioning
            "/api/login",            # Simpler path
            "/login",                # Direct login
            "/signin",               # Alternative naming
        ]

        # Match the browser's request format exactly
        payload = {
            'email': email,
            'password': password,
            'recaptcha': recaptcha_solution
        }

        headers = {
            'Accept': 'application/json',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
            'Host': UGEEN_URL.replace('http://', '').replace('https://', ''),
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36',
            'X-Requested-With': 'XMLHttpRequest',
            'Origin': api_base,
            'Referer': f"{api_base}/signin.html"
        }

        # Try each endpoint
        for endpoint in possible_endpoints:
            login_url = f"{api_base}{endpoint}"
            log_message(f"Trying POST {login_url}", "INFO")

            try:
                # Make the API request
                response = requests.post(
                    login_url,
                    data=payload,  # Use data instead of json to match jQuery ajax behavior
                    headers=headers,
                    timeout=10
                )

                log_message(f"API Response Status: {response.status_code}", "INFO")

                # If we get 200, process the response
                if response.status_code == 200:
                    try:
                        data = response.json()

                        # Extract JWT token from response
                        # Based on signin.js: localStorage.jsonwebToken = response.access.token
                        jwt_token = data.get('access', {}).get('token')

                        if jwt_token:
                            log_message(f"✓ JWT token extracted from API response (endpoint: {endpoint})!", "SUCCESS")

                            # Also extract other user data if needed
                            user_email = data.get('user', {}).get('email')
                            username = data.get('user', {}).get('username')

                            if user_email:
                                log_message(f"Logged in as: {user_email} ({username})", "INFO")

                            return jwt_token
                        else:
                            log_message(f"✗ No JWT token in API response from {endpoint}", "WARNING")
                            continue  # Try next endpoint

                    except json.JSONDecodeError as e:
                        log_message(f"✗ Failed to parse API response as JSON from {endpoint}: {e}", "WARNING")
                        continue  # Try next endpoint

                # If we get 404, silently try next endpoint
                elif response.status_code == 404:
                    log_message(f"Endpoint {endpoint} not found (404), trying next...", "DEBUG")
                    continue

                # For other errors, log and continue
                else:
                    log_message(f"✗ Endpoint {endpoint} returned status {response.status_code}", "WARNING")
                    try:
                        error_data = response.json()
                        error_msg = error_data.get('message', 'Unknown error')
                        log_message(f"Error message: {error_msg}", "WARNING")
                    except:
                        pass
                    continue  # Try next endpoint

            except requests.exceptions.Timeout:
                log_message(f"✗ Request to {endpoint} timed out", "WARNING")
                continue
            except requests.exceptions.RequestException as e:
                log_message(f"✗ Request to {endpoint} failed: {e}", "WARNING")
                continue

        # If we've tried all endpoints and none worked
        log_message("✗ All API endpoints failed. Falling back to browser automation.", "INFO")
        return None

    except Exception as e:
        log_message(f"✗ Unexpected error in API login: {e}", "ERROR")
        traceback.print_exc()
        return None

def create_proxy_auth_extension(host, port, user, password):
    """Build an unpacked Chrome extension that points Chrome at an authenticated
    proxy and answers the proxy Basic-auth challenge automatically.

    Chrome's --proxy-server flag cannot carry user:pass credentials, so for an
    authenticated proxy (e.g. Webshare) we inject them via chrome.webRequest
    .onAuthRequired. Returns the path to a temp directory holding the unpacked
    extension (caller is responsible for cleanup)."""
    ext_dir = tempfile.mkdtemp(prefix='ugeen_proxy_ext_')

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


def get_public_ip(driver, url=None):
    """Open the IP-check URL in the browser and return the public IP string it shows.

    Used to confirm the proxy is actually changing the outgoing IP. Returns None
    on failure."""
    check_url = url or PROXY_CHECK_URL
    try:
        driver.get(check_url)
        time.sleep(2)
        ip_text = driver.execute_script("return document.body ? document.body.innerText.trim() : '';")
        if ip_text:
            # api.ipify.org returns the bare IP; keep it short for safety
            return ip_text.splitlines()[0].strip()[:64]
    except Exception as e:
        print(f"⚠️ Could not read public IP from {check_url}: {e}")
    return None


def create_stealth_driver(proxy=None, headless=None):
    """Create undetected Chrome driver with stealth options.

    `proxy` may be:
      - None: direct connection
      - a string like 'http://host:port': unauthenticated proxy via --proxy-server
      - a dict {'host','port','user','password'}: authenticated proxy via a
        generated proxy-auth extension (e.g. Webshare)
    """
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

    # Detect if running in Docker with Xvfb virtual display
    in_docker = os.path.exists('/.dockerenv')
    display_set = os.environ.get('DISPLAY') is not None

    if headless:
        # CRITICAL: Skip headless mode when Xvfb is available
        # Chrome will render to the virtual display (DISPLAY=:99) instead
        if in_docker and display_set:
            print(f"✓ Running in headed mode with Xvfb (DISPLAY={os.environ.get('DISPLAY')})")
            print("  This allows JavaScript callbacks to execute properly in production")
        else:
            options.add_argument('--headless=new')
            print("Running in headless mode (no virtual display detected)")

    proxy_ext_dir = None
    if isinstance(proxy, dict) and proxy.get('host'):
        # Authenticated proxy (e.g. Webshare) -> use an extension to inject creds
        proxy_ext_dir = create_proxy_auth_extension(
            proxy['host'], proxy['port'],
            proxy.get('user', ''), proxy.get('password', '')
        )
        options.add_argument(f'--load-extension={proxy_ext_dir}')
        print(f"Using authenticated proxy via extension: {proxy['host']}:{proxy['port']} (user={proxy.get('user', '')})")
    elif proxy:
        # Plain proxy string, no auth
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
        if proxy_ext_dir:
            # Keep the proxy working even on the fallback path
            options_minimal.add_argument(f'--load-extension={proxy_ext_dir}')
        driver = uc.Chrome(options=options_minimal, use_subprocess=False)

    # Remember the proxy extension dir so it can be cleaned up after quit
    driver._proxy_ext_dir = proxy_ext_dir

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


def cleanup_driver(driver):
    """Quit the driver and remove any temp proxy-auth extension it created."""
    ext_dir = getattr(driver, '_proxy_ext_dir', None)
    try:
        driver.quit()
    except Exception:
        pass
    if ext_dir:
        shutil.rmtree(ext_dir, ignore_errors=True)


def build_webshare_proxy():
    """Build the Webshare authenticated-proxy spec from env, or None if disabled/unset."""
    if not USE_WEBSHARE_PROXY:
        return None
    if not WEBSHARE_PROXY_HOST or not WEBSHARE_PROXY_PORT:
        print("⚠️ USE_WEBSHARE_PROXY is True but WEBSHARE_PROXY_HOST/PORT are not set")
        return None
    return {
        'host': WEBSHARE_PROXY_HOST,
        'port': str(WEBSHARE_PROXY_PORT),
        'user': WEBSHARE_PROXY_USER,
        'password': WEBSHARE_PROXY_PASS,
    }

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
        print(f"Entering email: {config['username']}")
        type_like_human(username_field, config['username'])
        random_delay(1, 2)  # Longer pause between fields

        # Fill password field
        print(f"Entering password: {config['password']}")
        password_field = driver.find_element(By.CSS_SELECTOR, '#password')
        type_like_human(password_field, config['password'], min_delay=0.08, max_delay=0.25)
        random_delay(1.5, 3)  # Even longer pause before clicking

        # More human-like behavior before clicking login
        scroll_randomly(driver)
        move_mouse_randomly(driver)
        random_delay(1, 2)

        # Check for reCAPTCHA before clicking (only block if actually visible)
        print("Checking for reCAPTCHA...")
        jwt_token = None
        if detect_recaptcha(driver):
            print("⚠️ reCAPTCHA challenge is BLOCKING the page!")

            # Try to solve with 2captcha
            success, jwt_token = solve_recaptcha_with_2captcha(driver, config['url'])
            if success:
                print("✓ reCAPTCHA solved successfully with 2captcha!")
                if jwt_token:
                    print("✓ JWT token obtained via API login!")
                    return jwt_token  # Success! Return the token directly
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

        # Check if reCAPTCHA appeared after login click
        if not jwt_token and detect_recaptcha(driver):
            print("⚠️ reCAPTCHA appeared AFTER login click!")

            # Use the unified reCAPTCHA solving function (includes API login attempt)
            success, jwt_token_from_captcha = solve_recaptcha_with_2captcha(driver, driver.current_url)

            if success and jwt_token_from_captcha:
                print("✓ JWT token obtained via API login after solving reCAPTCHA!")
                jwt_token = jwt_token_from_captcha
            elif success:
                print("✓ reCAPTCHA solved, but no JWT from API. Will check localStorage...")
            else:
                print("✗ Failed to solve reCAPTCHA")
                return None

        # If we still don't have JWT token, try to extract from localStorage (browser automation method)
        if not jwt_token:
            print('Extracting JWT token from localStorage...')

            # Poll for up to 30 seconds
            for i in range(15):
                jwt_token = driver.execute_script("return window.localStorage.getItem('jsonwebToken');")
                if jwt_token:
                    print(f"✓ JWT token extracted from browser!")
                    break
                print(f"  Waiting for token... ({i+1}/15)")
                time.sleep(2)

        if not jwt_token:
            print("✗ JWT token not found via any method")

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
        error_msg = "UGEEN_EMAIL and UGEEN_PASSWORD must be set in environment"
        log_message(error_msg, "ERROR")
        notify_telegram("❌ ERROR", "Missing UGEEN credentials in environment variables")
        if CALLBACK_URL and USER_ID:
            send_webhook_failure(USER_ID, CALLBACK_URL, error_msg)
        return False

    if not TWOCAPTCHA_API_KEY:
        error_msg = "TWOCAPTCHA_API_KEY must be set in environment"
        log_message(error_msg, "ERROR")
        notify_telegram("❌ ERROR", "Missing 2captcha API key in environment variables")
        if CALLBACK_URL and USER_ID:
            send_webhook_failure(USER_ID, CALLBACK_URL, error_msg)
        return False

    config = {
        'url': f'{UGEEN_URL}/signin.html',
        'username': UGEEN_EMAIL,
        'password': UGEEN_PASSWORD,
        'package_id': UGEEN_PACKAGE_ID,
        'api_base': UGEEN_URL  # Base URL without /api/v1 - endpoints are like /auth/login
    }

    print('\n' + '='*60)
    print('🚀 UGEEN API Scraper with Anti-Detection (Enhanced)')
    print('='*60 + '\n')

    print(f"Using UGEEN credentials -> email: {UGEEN_EMAIL} | password: {UGEEN_PASSWORD}")

    # Try to load existing session first
    print('=== STEP 0: Checking for existing session ===')
    session = load_session(UGEEN_EMAIL)
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

            # Preflight: confirm the outgoing IP (proves the proxy is active)
            if proxy:
                ip = get_public_ip(driver)
                print(f"🌐 Public IP via browser: {ip if ip else 'unknown'}")

            # Perform login with retries
            login_result = perform_login_with_retries(driver, wait, config)

            if not login_result:
                print('\n✗ Login failed after all retries')
                return False

            jwt_token = login_result['jwt_token']
            cookies = login_result['cookies']

            # Save session for future use
            save_session(cookies, jwt_token, UGEEN_EMAIL)

            # Clean up browser
            cleanup_driver(driver)
            print('✓ Browser closed')

        except Exception as e:
            print(f"Error during login: {e}")
            import traceback
            traceback.print_exc()
            if driver:
                cleanup_driver(driver)
            return False

    # Check if renewal is available before proceeding
    print('\n=== STEP 1.5: Checking Renewal Eligibility ===')
    can_renew, remaining_minutes = check_renewal_eligibility(jwt_token)

    if not can_renew:
        log_message(f"Renewal not available yet. Need to wait {remaining_minutes} minutes.", "WARNING")
        print(f'\n⏳ Renewal not available - need to wait {remaining_minutes} minutes')

        # Send progress webhook with remaining time
        if CALLBACK_URL and USER_ID:
            send_webhook_progress(
                USER_ID,
                CALLBACK_URL,
                f"Renewal not available. Retry in {remaining_minutes} minutes.",
                20,  # Progress percentage for pending state
                extra_data={'renew_remaining_minutes': remaining_minutes}
            )

        # Log and terminate gracefully (not an error - just need to retry later)
        log_message("Terminating - Laravel will retry after remaining minutes", "INFO")
        notify_telegram("⏳ RENEWAL PENDING", f"Account {UGEEN_EMAIL} cannot renew yet. Retry in {remaining_minutes} minutes.")
        return True  # Return True since this is not an error condition

    log_message("✓ Renewal is available! Proceeding with renewal process...", "SUCCESS")
    print('✓ Renewal eligibility confirmed - proceeding...\n')

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

        print("✓ Found submit button. Waiting 30 seconds before clicking...")
        time.sleep(30)  # Wait before clicking (reduced from 120s to prevent token expiration)

        print("Clicking submit button...")
        submit_button.click()

        # Wait for submission to complete
        print("Waiting for submission to complete...")
        time.sleep(10)  # Increased from 5s to allow AJAX to complete

        # Capture current state for debugging
        current_url = driver.current_url
        page_source = driver.page_source
        page_source_lower = page_source.lower()

        # Debug output
        log_message(f"After submit - URL: {current_url}", "INFO")
        log_message(f"After submit - Page title: {driver.title}", "INFO")
        log_message(f"After submit - Source length: {len(page_source)} chars", "INFO")

        # Save debug files
        try:
            with open('/tmp/ugeen_after_submit.html', 'w', encoding='utf-8') as f:
                f.write(page_source)
            log_message("Page source saved to /tmp/ugeen_after_submit.html", "INFO")

            driver.save_screenshot('/tmp/ugeen_after_submit.png')
            log_message("Screenshot saved to /tmp/ugeen_after_submit.png", "INFO")
        except Exception as e:
            log_message(f"Could not save debug files: {e}", "WARNING")

        # Check for error messages first
        error_keywords = ['error', 'invalid', 'expired', 'failed', 'خطأ']  # Last one is Arabic for 'error'
        found_errors = []
        for keyword in error_keywords:
            if keyword in page_source_lower:
                found_errors.append(keyword)

        if found_errors:
            log_message(f"Found error keywords on page: {', '.join(found_errors)}", "ERROR")

        # Improved success detection - check multiple indicators
        success_indicators = [
            'success' in page_source_lower,
            'activated' in page_source_lower,
            'dashboard' in current_url,
            'تم التفعيل' in page_source,  # Arabic: "activated"
            'نجح' in page_source,  # Arabic: "success"
            'account renewed' in page_source_lower,
            'subscription renewed' in page_source_lower,
        ]

        if any(success_indicators):
            log_message("SUCCESS! Subscription Activated!", "SUCCESS")

            # Extract Xtream credentials from subscription page
            log_message("Extracting Xtream credentials from subscription page...", "INFO")
            xtream_username = None
            xtream_password = None

            try:
                # Navigate to subscription page
                if 'subscription' not in current_url.lower():
                    log_message("Navigating to subscription page...", "INFO")
                    driver.get(f'{UGEEN_URL}/subscription.html')
                    time.sleep(5)  # Wait for page load

                # Wait for JavaScript to populate credentials (retry up to 3 times)
                for attempt in range(3):
                    log_message(f"Credential extraction attempt {attempt + 1}/3...", "INFO")

                    # Save debug HTML and screenshot
                    try:
                        with open('/tmp/ugeen_subscription.html', 'w', encoding='utf-8') as f:
                            f.write(driver.page_source)
                        driver.save_screenshot('/tmp/ugeen_subscription.png')
                        log_message("Debug files saved: /tmp/ugeen_subscription.html and .png", "INFO")
                    except Exception as debug_err:
                        log_message(f"Could not save debug files: {debug_err}", "WARNING")

                    # Extract credentials from page using correct CSS classes
                    # Try multiple selectors for username (from actual HTML structure)
                    username_selectors = [
                        '.iptv-user',                    # Primary selector
                        'span.iptv-user',                # Specific tag
                        '.copy-value.iptv-user',         # Full class combo
                        'span.copy-value.iptv-user',     # Most specific
                    ]

                    for selector in username_selectors:
                        try:
                            element = driver.find_element(By.CSS_SELECTOR, selector)
                            xtream_username = element.text.strip()
                            if xtream_username and xtream_username not in ['', 'ugeen_xxxxxxxx']:
                                log_message(f"Found username via selector: {selector} -> {xtream_username}", "INFO")
                                break
                        except:
                            continue

                    # Try multiple selectors for password
                    password_selectors = [
                        '.iptv-pass',                    # Primary selector
                        'span.iptv-pass',                # Specific tag
                        '.copy-value.iptv-pass',         # Full class combo
                        'span.copy-value.iptv-pass',     # Most specific
                    ]

                    for selector in password_selectors:
                        try:
                            element = driver.find_element(By.CSS_SELECTOR, selector)
                            xtream_password = element.text.strip()
                            if xtream_password and xtream_password not in ['', 'xxxxxxxx']:
                                log_message(f"Found password via selector: {selector} -> {xtream_password[:4]}***", "INFO")
                                break
                        except:
                            continue

                    # Validate extracted credentials
                    if xtream_username and xtream_password:
                        # Check if these are real values, not placeholders
                        if xtream_username.startswith('ugeen_') and xtream_password != 'xxxxxxxx':
                            log_message("✓ Valid credentials extracted!", "SUCCESS")
                            break
                        else:
                            log_message(f"Extracted values appear to be placeholders, retrying...", "WARNING")
                            log_message(f"  Username: {xtream_username}, starts with ugeen_: {xtream_username.startswith('ugeen_') if xtream_username else False}", "WARNING")
                            log_message(f"  Password: {'***' if xtream_password else 'None'}, is placeholder: {xtream_password == 'xxxxxxxx' if xtream_password else False}", "WARNING")
                            xtream_username = None
                            xtream_password = None

                    # Wait before retry
                    if attempt < 2:
                        time.sleep(3)

                # If still not found, try JavaScript extraction as fallback
                if not xtream_username or not xtream_password:
                    log_message("Trying JavaScript extraction as fallback...", "INFO")
                    credentials = driver.execute_script("""
                        // Extract from specific Ugeen CSS classes
                        var username = document.querySelector('.iptv-user');
                        var password = document.querySelector('.iptv-pass');

                        return {
                            username: username ? username.textContent.trim() : null,
                            password: password ? password.textContent.trim() : null
                        };
                    """)

                    xtream_username = xtream_username or credentials.get('username')
                    xtream_password = xtream_password or credentials.get('password')

                # Final validation
                if xtream_username and xtream_password:
                    # Remove placeholders if still present
                    if xtream_username == 'ugeen_xxxxxxxx':
                        xtream_username = None
                    if xtream_password == 'xxxxxxxx':
                        xtream_password = None

                # Log final results
                if xtream_username and xtream_password:
                    log_message(f"✓ Extracted Xtream username: {xtream_username}", "SUCCESS")
                    log_message(f"✓ Extracted Xtream password: {xtream_password[:4]}***", "SUCCESS")
                else:
                    log_message("⚠️ Could not extract Xtream credentials from subscription page", "WARNING")
                    log_message(f"Username found: {bool(xtream_username)}, Password found: {bool(xtream_password)}", "WARNING")
                    log_message("Please check /tmp/ugeen_subscription.html and .png for debugging", "WARNING")

            except Exception as e:
                log_message(f"Error extracting Xtream credentials: {e}", "ERROR")
                import traceback
                traceback.print_exc()

            # Save activation data
            activation_data = {
                'timestamp': datetime.now().isoformat(),
                'email': UGEEN_EMAIL,
                'package_id': UGEEN_PACKAGE_ID,
                'activation_code': activation_code,
                'xtream_username': xtream_username,
                'xtream_password': xtream_password,
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

            # Send success webhook to Laravel with extracted credentials
            if CALLBACK_URL and USER_ID:
                send_webhook_success(USER_ID, CALLBACK_URL, xtream_username, xtream_password)

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

        # Send failure webhook to Laravel
        if CALLBACK_URL and USER_ID:
            send_webhook_failure(USER_ID, CALLBACK_URL, str(e))

        if driver:
            driver.quit()
        return False

def run_proxy_test():
    """Standalone Webshare proxy check: open the IP-check URL, print the IP, exit.

    Lets you confirm the outgoing IP changed without running the full login flow."""
    print('\n' + '='*60)
    print('🌐 Webshare Proxy Test')
    print('='*60 + '\n')

    # For the test we use the Webshare vars directly, ignoring USE_WEBSHARE_PROXY,
    # so you can verify the proxy without toggling the env flag first.
    if not WEBSHARE_PROXY_HOST or not WEBSHARE_PROXY_PORT:
        print("✗ WEBSHARE_PROXY_HOST/PORT are not set. Configure WEBSHARE_PROXY_* in your environment.")
        return False
    proxy = {
        'host': WEBSHARE_PROXY_HOST,
        'port': str(WEBSHARE_PROXY_PORT),
        'user': WEBSHARE_PROXY_USER,
        'password': WEBSHARE_PROXY_PASS,
    }

    print(f"Proxy: {proxy['host']}:{proxy['port']} (user={proxy['user']})")
    driver = None
    try:
        driver = create_stealth_driver(proxy=proxy)
        ip = get_public_ip(driver)
        if ip:
            print(f"\n✓ Public IP via Webshare proxy: {ip}")
            print("  Compare with your server's direct IP (e.g. `curl https://api.ipify.org`).")
            return True
        print("\n✗ Could not determine the public IP through the proxy.")
        print("  Check the WEBSHARE_PROXY_PORT (Webshare rotating endpoint is often :80).")
        return False
    except Exception as e:
        print(f"\n✗ Proxy test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if driver:
            cleanup_driver(driver)


def main():
    """Main entry point with proxy support and proper exit codes"""
    # Standalone proxy test mode short-circuits the full automation
    if args.test_proxy:
        ok = run_proxy_test()
        sys.exit(0 if ok else 1)

    # Start progress reporter thread if webhook callback is configured
    progress_thread = None
    stop_event = None

    try:
        log_message("UGEEN API Scraper starting", "INFO")
        notify_telegram("🚀 STARTED", "UGEEN scraper automation has begun")

        # Start progress reporter if callback URL is provided
        if CALLBACK_URL and USER_ID:
            log_message("Starting progress reporter thread", "INFO")
            stop_event = threading.Event()
            progress_thread = threading.Thread(
                target=progress_reporter,
                args=(USER_ID, CALLBACK_URL, stop_event),
                daemon=True
            )
            progress_thread.start()

        # Build proxy spec from Webshare env (None if USE_WEBSHARE_PROXY is False)
        proxy = build_webshare_proxy()
        if proxy:
            log_message(f"Webshare proxy enabled: {proxy['host']}:{proxy['port']}", "INFO")
        else:
            log_message("No proxy configured (direct connection)", "INFO")

        success = scrape_with_api_auth(proxy=proxy)

        # Stop progress reporter
        if stop_event:
            stop_event.set()
            if progress_thread:
                progress_thread.join(timeout=2)

        if success:
            log_message("All done! Scraper completed successfully", "SUCCESS")
            sys.exit(0)  # Exit with success code
        else:
            log_message("Scraping failed. Check the errors above.", "ERROR")
            notify_telegram("❌ FAILED", "UGEEN scraper failed to complete")

            # Send failure webhook if not already sent
            if CALLBACK_URL and USER_ID:
                send_webhook_failure(USER_ID, CALLBACK_URL, "Scraping failed")

            sys.exit(1)  # Exit with error code

    except KeyboardInterrupt:
        log_message("Scraper interrupted by user", "WARNING")
        notify_telegram("⚠️ INTERRUPTED", "UGEEN scraper was manually stopped")

        # Stop progress reporter
        if stop_event:
            stop_event.set()

        # Send failure webhook
        if CALLBACK_URL and USER_ID:
            send_webhook_failure(USER_ID, CALLBACK_URL, "Script interrupted by user")

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

        # Stop progress reporter
        if stop_event:
            stop_event.set()

        # Send failure webhook
        if CALLBACK_URL and USER_ID:
            send_webhook_failure(USER_ID, CALLBACK_URL, str(e))

        sys.exit(1)

if __name__ == '__main__':
    main()
