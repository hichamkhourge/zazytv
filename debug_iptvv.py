"""
Debug script to test IPTVV.ca checkout in GUI mode and pause before submission
"""

import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from webdriver_manager.chrome import ChromeDriverManager

# Force GUI mode for debugging
os.environ["HEADLESS"] = "False"
os.environ["AUTO_EXIT"] = "False"

def get_driver():
    options = Options()
    options.add_argument("--start-maximized")
    options.add_experimental_option("detach", True)

    chromedriver_path = os.getenv("CHROMEDRIVER_PATH", "/usr/local/bin/chromedriver")
    if os.path.exists(chromedriver_path):
        service = Service(chromedriver_path)
    else:
        service = Service(ChromeDriverManager().install())

    return webdriver.Chrome(service=service, options=options)

print("="*60)
print("IPTVV.CA DEBUG MODE")
print("="*60)
print("This will open the browser and show you what's happening")
print("="*60)

driver = get_driver()

try:
    # Navigate to cart
    print("\n[1] Navigating to https://iptvv.ca/cart/")
    driver.get("https://iptvv.ca/cart/")
    time.sleep(5)

    print(f"[*] Current URL: {driver.current_url}")
    print(f"[*] Page title: {driver.title}")

    # Take screenshot
    driver.save_screenshot("/tmp/iptvv_step1_cart.png")
    print("[*] Screenshot saved: /tmp/iptvv_step1_cart.png")

    # Check page source for clues
    page_source = driver.page_source.lower()

    print("\n[*] Page analysis:")
    print(f"    - Contains 'free trial': {'YES' if 'free trial' in page_source else 'NO'}")
    print(f"    - Contains 'checkout': {'YES' if 'checkout' in page_source else 'NO'}")
    print(f"    - Contains 'email': {'YES' if 'email' in page_source else 'NO'}")
    print(f"    - Contains 'place order': {'YES' if 'place order' in page_source else 'NO'}")

    # Try to find all buttons
    print("\n[*] Looking for all buttons on the page...")
    buttons = driver.find_elements(By.TAG_NAME, "button")
    links = driver.find_elements(By.TAG_NAME, "a")
    inputs = driver.find_elements(By.XPATH, "//input[@type='submit' or @type='button']")

    print(f"\n[*] Found {len(buttons)} <button> elements")
    for i, btn in enumerate(buttons[:10]):  # Show first 10
        if btn.is_displayed():
            print(f"    {i+1}. Text: '{btn.text}' | Value: '{btn.get_attribute('value')}'")

    print(f"\n[*] Found {len(links)} <a> elements (showing clickable ones)")
    for i, link in enumerate(links[:20]):  # Show first 20
        if link.is_displayed() and 'trial' in link.text.lower():
            print(f"    {i+1}. Text: '{link.text}' | Href: '{link.get_attribute('href')}'")

    print(f"\n[*] Found {len(inputs)} <input> buttons")
    for i, inp in enumerate(inputs[:10]):
        if inp.is_displayed():
            print(f"    {i+1}. Value: '{inp.get_attribute('value')}' | Type: '{inp.get_attribute('type')}'")

    # Check forms
    forms = driver.find_elements(By.TAG_NAME, "form")
    print(f"\n[*] Found {len(forms)} <form> elements")
    for i, form in enumerate(forms):
        print(f"    Form {i+1}:")
        print(f"      - Action: {form.get_attribute('action')}")
        print(f"      - Method: {form.get_attribute('method')}")

        # Get all inputs in this form
        form_inputs = form.find_elements(By.TAG_NAME, "input")
        print(f"      - Inputs: {len(form_inputs)}")
        for inp in form_inputs[:5]:  # Show first 5
            print(f"          * Name: {inp.get_attribute('name')}, Type: {inp.get_attribute('type')}")

    print("\n" + "="*60)
    print("BROWSER IS NOW OPEN")
    print("="*60)
    print("Please manually inspect the page to see:")
    print("1. Is there a 'Get Free Trial' button?")
    print("2. What fields are in the checkout form?")
    print("3. Are there any validation messages?")
    print("4. Does the mail.tm email get rejected?")
    print("="*60)
    print("\nPress Ctrl+C when done inspecting...")

    # Keep browser open
    while True:
        time.sleep(1)

except KeyboardInterrupt:
    print("\n\n[*] Closing browser...")
    driver.quit()
except Exception as exc:
    print(f"\n[!] ERROR: {exc}")
    import traceback
    traceback.print_exc()
    print("\n[*] Browser will remain open for inspection...")
    while True:
        time.sleep(1)
