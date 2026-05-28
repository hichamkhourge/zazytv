"""
Submit form and capture exact error
"""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import os
import random
import string

os.environ["HEADLESS"] = "True"

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

chromedriver_path = "/usr/local/bin/chromedriver"
service = Service(chromedriver_path) if os.path.exists(chromedriver_path) else Service()

driver = webdriver.Chrome(service=service, options=options)

try:
    # Add product to cart
    driver.get("https://iptvv.ca/?add-to-cart=7758")
    time.sleep(3)

    # Go to checkout
    driver.get("https://iptvv.ca/checkout/")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
    time.sleep(3)

    # Fill form quickly
    email = f"test{''.join(random.choices(string.digits, k=8))}@wshu.net"
    driver.find_element(By.ID, "billing_email").send_keys(email)
    driver.find_element(By.ID, "billing_first_name").send_keys("John")
    driver.find_element(By.ID, "billing_last_name").send_keys("Doe")
    driver.find_element(By.ID, "billing_phone").send_keys("+15145551234")

    # Select device checkbox
    devices = driver.find_elements(By.NAME, "device_select[]")
    if devices:
        devices[0].click()

    # Select channel checkbox
    channels = driver.find_elements(By.XPATH, "//input[@type='checkbox' and (contains(@name, 'channel') or contains(@name, 'package'))]")
    if channels:
        channels[0].click()

    print(f"[*] Submitting with email: {email}")

    # Click submit
    submit_btn = driver.find_element(By.ID, "place_order")
    submit_btn.click()
    time.sleep(12)

    print(f"[*] After submit URL: {driver.current_url}")

    # Check for errors
    try:
        # Try multiple error selectors
        for selector in [".woocommerce-error", ".woocommerce-NoticeGroup", "ul.woocommerce-error", ".woocommerce-notices-wrapper"]:
            try:
                error_el = driver.find_element(By.CSS_SELECTOR, selector)
                print(f"\n[!] Found error element with selector: {selector}")
                print(f"[!] Error HTML: {error_el.get_attribute('outerHTML')[:500]}")
                print(f"[!] Error text: '{error_el.text}'")
                print(f"[!] Error visible: {error_el.is_displayed()}")
            except:
                pass

        # Get ALL elements with 'error' in class
        all_error_elements = driver.find_elements(By.XPATH, "//*[contains(@class, 'error') or contains(@class, 'Error')]")
        print(f"\n[*] Found {len(all_error_elements)} elements with 'error' in class")
        for i, el in enumerate(all_error_elements[:5], 1):
            if el.is_displayed():
                print(f"  {i}. {el.tag_name}.{el.get_attribute('class')}: '{el.text[:100]}'")

    except Exception as e:
        print(f"[!] Error checking: {e}")

    # Save full page
    with open('/tmp/iptvv_after_submit.html', 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    print("\n[*] Full HTML after submit saved to: /tmp/iptvv_after_submit.html")

finally:
    driver.quit()
