"""
Debug script to capture UGEEN login API endpoint
Runs with Chrome DevTools logging to see actual API calls
"""

import os
import json
import time
from dotenv import load_dotenv
import undetected_chromedriver as uc
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

load_dotenv()

UGEEN_EMAIL = os.getenv('UGEEN_EMAIL')
UGEEN_PASSWORD = os.getenv('UGEEN_PASSWORD')
UGEEN_URL = os.getenv('UGEEN_URL', 'http://ugeen.live')

def capture_network_logs(driver):
    """Extract network requests from Chrome performance logs"""
    logs = driver.get_log('performance')

    api_requests = []
    for log in logs:
        try:
            message = json.loads(log['message'])
            method = message.get('message', {}).get('method', '')

            # Look for Network requests
            if 'Network.request' in method or 'Network.response' in method:
                params = message.get('message', {}).get('params', {})
                request = params.get('request', {})
                response = params.get('response', {})

                url = request.get('url', '') or response.get('url', '')

                # Filter for login/auth related endpoints
                if any(keyword in url.lower() for keyword in ['login', 'auth', 'signin', 'api']):
                    api_requests.append({
                        'method': method,
                        'url': url,
                        'request': request,
                        'response': response
                    })
        except:
            pass

    return api_requests

def main():
    print("🔍 UGEEN API Endpoint Discovery Tool")
    print("=" * 60)

    # Create Chrome options with performance logging enabled
    options = uc.ChromeOptions()
    options.add_argument('--enable-logging')
    options.add_argument('--v=1')
    options.set_capability('goog:loggingPrefs', {'performance': 'ALL', 'browser': 'ALL'})

    # Create driver
    print("Creating Chrome driver with logging enabled...")
    driver = uc.Chrome(options=options)
    wait = WebDriverWait(driver, 20)

    try:
        # Navigate to login page
        login_url = f'{UGEEN_URL}/signin.html'
        print(f"\n1. Navigating to: {login_url}")
        driver.get(login_url)
        time.sleep(3)

        # Fill in credentials
        print("2. Entering credentials...")
        email_field = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, '#email')))
        email_field.send_keys(UGEEN_EMAIL)

        password_field = driver.find_element(By.CSS_SELECTOR, '#password')
        password_field.send_keys(UGEEN_PASSWORD)
        time.sleep(1)

        # Click submit
        print("3. Clicking submit button...")
        print("   NOTE: If reCAPTCHA appears, please solve it manually!")
        login_button = driver.find_element(By.CSS_SELECTOR, '#submit')
        login_button.click()

        # Wait for either success or captcha
        print("4. Waiting for login response (30 seconds)...")
        print("   Watching for API calls...")
        time.sleep(30)

        # Capture network logs
        print("\n5. Analyzing network requests...")
        api_requests = capture_network_logs(driver)

        if api_requests:
            print(f"\n✓ Found {len(api_requests)} API-related requests:")
            print("=" * 60)

            for i, req in enumerate(api_requests, 1):
                print(f"\n[{i}] {req.get('url', 'N/A')}")

                request_data = req.get('request', {})
                if request_data:
                    print(f"   Method: {request_data.get('method', 'N/A')}")
                    print(f"   Headers: {json.dumps(request_data.get('headers', {}), indent=6)}")
                    if request_data.get('postData'):
                        print(f"   Body: {request_data.get('postData', 'N/A')}")

            print("\n" + "=" * 60)
        else:
            print("\n✗ No API requests captured")

        # Check if JWT token exists in localStorage
        print("\n6. Checking localStorage for JWT...")
        jwt_token = driver.execute_script("return window.localStorage.getItem('jsonwebToken');")

        if jwt_token:
            print(f"✓ JWT Token found: {jwt_token[:50]}...")
        else:
            print("✗ JWT Token not found in localStorage")

        # Check current URL
        print(f"\n7. Current URL: {driver.current_url}")

        print("\n" + "=" * 60)
        print("Press Enter to close the browser...")
        input()

    finally:
        driver.quit()
        print("Browser closed.")

if __name__ == '__main__':
    main()
