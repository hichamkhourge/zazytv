"""
Analyze IPTVV.ca page structure to understand the checkout flow
"""

import requests
from bs4 import BeautifulSoup
import json

print("="*60)
print("ANALYZING IPTVV.CA PAGE STRUCTURE")
print("="*60)

# Step 1: Check cart page
print("\n[1] Fetching https://iptvv.ca/cart/...")
try:
    response = requests.get("https://iptvv.ca/cart/", timeout=10, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    print(f"[*] Status code: {response.status_code}")
    print(f"[*] Final URL: {response.url}")

    soup = BeautifulSoup(response.text, 'html.parser')
    print(f"[*] Page title: {soup.title.string if soup.title else 'No title'}")

    # Look for forms
    forms = soup.find_all('form')
    print(f"\n[*] Found {len(forms)} form(s)")

    for i, form in enumerate(forms, 1):
        print(f"\n  Form {i}:")
        print(f"    - Action: {form.get('action', 'No action')}")
        print(f"    - Method: {form.get('method', 'No method')}")

        # Find all inputs
        inputs = form.find_all(['input', 'select', 'textarea'])
        print(f"    - Fields ({len(inputs)}):")
        for inp in inputs[:10]:  # Show first 10
            field_type = inp.name
            field_name = inp.get('name', inp.get('id', 'unnamed'))
            field_input_type = inp.get('type', 'N/A') if field_type == 'input' else field_type
            print(f"        * {field_name} ({field_input_type})")

    # Look for buttons and links with "trial" or "checkout"
    print("\n[*] Looking for relevant buttons/links...")
    for tag in ['a', 'button']:
        elements = soup.find_all(tag)
        for el in elements:
            text = el.get_text(strip=True).lower()
            if any(keyword in text for keyword in ['trial', 'checkout', 'free', 'get', 'order']):
                href = el.get('href', '')
                print(f"    - <{tag}>: '{el.get_text(strip=True)}' -> {href}")

    # Save HTML for inspection
    with open('/tmp/iptvv_cart.html', 'w') as f:
        f.write(response.text)
    print("\n[*] Full HTML saved to: /tmp/iptvv_cart.html")

except Exception as exc:
    print(f"[!] Error: {exc}")

# Step 2: Check checkout page directly
print("\n" + "="*60)
print("[2] Fetching https://iptvv.ca/checkout/...")
print("="*60)

try:
    response = requests.get("https://iptvv.ca/checkout/", timeout=10, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    print(f"[*] Status code: {response.status_code}")
    print(f"[*] Final URL: {response.url}")

    soup = BeautifulSoup(response.text, 'html.parser')
    print(f"[*] Page title: {soup.title.string if soup.title else 'No title'}")

    # Look for forms
    forms = soup.find_all('form')
    print(f"\n[*] Found {len(forms)} form(s) on checkout page")

    for i, form in enumerate(forms, 1):
        print(f"\n  Form {i}:")
        print(f"    - Action: {form.get('action', 'No action')}")
        print(f"    - Method: {form.get('method', 'No method')}")
        print(f"    - ID: {form.get('id', 'No ID')}")
        print(f"    - Class: {form.get('class', 'No class')}")

        # Find all inputs with details
        inputs = form.find_all(['input', 'select', 'textarea'])
        print(f"    - Fields ({len(inputs)}):")
        for inp in inputs:
            field_type = inp.name
            field_name = inp.get('name', inp.get('id', 'unnamed'))
            field_input_type = inp.get('type', 'N/A') if field_type == 'input' else field_type
            required = 'REQUIRED' if inp.get('required') else ''
            placeholder = inp.get('placeholder', '')
            print(f"        * {field_name} ({field_input_type}) {required} placeholder='{placeholder}'")

    # Look for submit buttons
    print("\n[*] Submit buttons found:")
    for btn in soup.find_all(['button', 'input']):
        btn_type = btn.get('type', '')
        if btn_type in ['submit', 'button'] or btn.name == 'button':
            print(f"    - {btn.name}: '{btn.get_text(strip=True)}' | type='{btn_type}' | value='{btn.get('value', '')}'")

    # Save HTML
    with open('/tmp/iptvv_checkout.html', 'w') as f:
        f.write(response.text)
    print("\n[*] Full HTML saved to: /tmp/iptvv_checkout.html")

except Exception as exc:
    print(f"[!] Error: {exc}")

print("\n" + "="*60)
print("ANALYSIS COMPLETE")
print("="*60)
print("Check the saved HTML files to understand the form structure:")
print("  - /tmp/iptvv_cart.html")
print("  - /tmp/iptvv_checkout.html")
print("="*60)
