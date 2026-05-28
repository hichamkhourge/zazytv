"""
Fetch the actual checkout page HTML with a product in cart
"""
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait, Select
from selenium.webdriver.support import expected_conditions as EC
import os

os.environ["HEADLESS"] = "True"

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

chromedriver_path = "/usr/local/bin/chromedriver"
if os.path.exists(chromedriver_path):
    service = Service(chromedriver_path)
else:
    from webdriver_manager.chrome import ChromeDriverManager
    service = Service(ChromeDriverManager().install())

driver = webdriver.Chrome(service=service, options=options)

try:
    # Add product to cart
    print("[*] Adding product to cart...")
    driver.get("https://iptvv.ca/?add-to-cart=7758")
    time.sleep(3)

    # Navigate to checkout
    print("[*] Going to checkout...")
    driver.get("https://iptvv.ca/checkout/")
    WebDriverWait(driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "form")))
    time.sleep(3)

    print(f"[*] Current URL: {driver.current_url}")

    # Get all form fields
    print("\n[*] ANALYZING FORM FIELDS:\n")

    # Find all select dropdowns
    selects = driver.find_elements(By.TAG_NAME, "select")
    print(f"Found {len(selects)} SELECT dropdowns:")
    for i, sel in enumerate(selects, 1):
        sel_id = sel.get_attribute("id") or "NO_ID"
        sel_name = sel.get_attribute("name") or "NO_NAME"
        select_obj = Select(sel)
        options = [opt.text for opt in select_obj.options[:5]]  # First 5 options
        print(f"  {i}. ID='{sel_id}', NAME='{sel_name}'")
        print(f"     Options: {options}")
        print()

    # Find all checkboxes
    checkboxes = driver.find_elements(By.XPATH, "//input[@type='checkbox']")
    print(f"\nFound {len(checkboxes)} CHECKBOXES:")
    for i, cb in enumerate(checkboxes[:10], 1):  # First 10
        cb_id = cb.get_attribute("id") or "NO_ID"
        cb_name = cb.get_attribute("name") or "NO_NAME"
        label_text = ""
        try:
            if cb_id != "NO_ID":
                label = driver.find_element(By.XPATH, f"//label[@for='{cb_id}']")
                label_text = label.text[:50]
        except:
            pass
        print(f"  {i}. ID='{cb_id}', NAME='{cb_name}', Label: '{label_text}'")

    # Save full HTML
    with open('/tmp/iptvv_checkout_full.html', 'w', encoding='utf-8') as f:
        f.write(driver.page_source)
    print("\n[OK] Full HTML saved to: /tmp/iptvv_checkout_full.html")

    # Look for "device" or "channel" in page source
    page_lower = driver.page_source.lower()
    if "billing_device" in page_lower:
        print("\n[!] FOUND: 'billing_device' exists in HTML")
    if "device" in page_lower:
        print("[!] FOUND: 'device' keyword exists in HTML")

finally:
    driver.quit()
