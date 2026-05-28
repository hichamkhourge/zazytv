# IPTVV Canada Automation Script

Automated trial account creation for IPTVV.ca using temporary mail.tm emails and Selenium automation.

## Features

- ✅ Automatic temporary email creation via mail.tm API
- ✅ Full website automation with Selenium
- ✅ Automatic "full channel" package selection
- ✅ Smart email polling (waits 5-45 minutes for credentials)
- ✅ Credential extraction (username, password, server address)
- ✅ Laravel webhook integration
- ✅ Telegram notifications
- ✅ 2captcha integration (if reCAPTCHA present)
- ✅ Headless and GUI modes

## Installation

```bash
# All dependencies are already in requirements.txt
pip install -r requirements.txt
```

## Configuration

Add these variables to your `.env` file:

```bash
# Required for 2captcha (if site has CAPTCHA)
TWOCAPTCHA_API_KEY=your_2captcha_api_key

# Optional: Override default URLs
IPTVV_BASE_URL=https://iptvv.ca
IPTVV_CART_URL=https://iptvv.ca/cart/

# Email polling settings
IPTVV_EMAIL_POLL_SECONDS=30          # Check inbox every 30 seconds
IPTVV_EMAIL_MAX_WAIT_SECONDS=2700    # Max wait time: 45 minutes

# Browser settings
HEADLESS=True                         # Set to False for GUI mode
AUTO_EXIT=True                        # Close browser when done
CHROMEDRIVER_PATH=/usr/local/bin/chromedriver

# Tor integration (bypass IP-based trial limits)
USE_TOR=False                         # Set to True to route traffic through Tor
TOR_SOCKS_HOST=127.0.0.1             # Tor SOCKS proxy host
TOR_SOCKS_PORT=9050                  # Tor SOCKS proxy port
TOR_CONTROL_HOST=127.0.0.1           # Tor control port host
TOR_CONTROL_PORT=9051                # Tor control port
TOR_CONTROL_PASSWORD=                # Leave empty if no password set

# Laravel webhook integration
WEBHOOK_AUTH_TOKEN=your_bearer_token

# Telegram notifications (optional)
TELEGRAM_ENABLED=True
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

## Usage

### Standalone Mode (No Laravel)

```bash
# Run with default settings
python iptvvcanada_automation.py

# The script will:
# 1. Create temporary email via mail.tm
# 2. Navigate to iptvv.ca/cart
# 3. Click "Get Free Trial"
# 4. Select "full channel" package
# 5. Fill checkout form
# 6. Submit order
# 7. Wait for credentials email (up to 45 minutes)
# 8. Extract and print credentials
```

### Laravel Integration Mode

```bash
python iptvvcanada_automation.py \
  --user-id 123 \
  --callback-url https://your-app.com/api/webhooks/iptvv-automation
```

Webhook payload (success):
```json
{
  "user_id": 123,
  "status": "success",
  "timestamp": "2026-05-28T10:30:00",
  "username": "GABSSZY5RS",
  "password": "49180341",
  "host": "http://56668610.97qaz.com",
  "m3u_url": "http://56668610.97qaz.com/get.php?username=GABSSZY5RS&password=49180341&type=m3u_plus&output=ts"
}
```

Webhook payload (failure):
```json
{
  "user_id": 123,
  "status": "failed",
  "timestamp": "2026-05-28T10:30:00",
  "error": "Timeout: Credentials email not received after 2700 seconds"
}
```


### 1. Temporary Email Creation (mail.tm)

The script uses the free mail.tm API to create temporary email addresses:

- GET /domains - Fetch available domains
- POST /accounts - Create temporary account
- POST /token - Get authentication token
- GET /messages - Poll inbox for new emails
- GET /messages/{id} - Fetch full email content

**No API key required** - mail.tm is completely free!

### 2. Website Automation Flow

1. Navigate to `https://iptvv.ca/cart/`
2. Click "Get Free Trial" button
3. Select "full channel" package (tries dropdowns, radio buttons, checkboxes)
4. Fill checkout form with:
   - Temporary mail.tm email
   - Random Canadian name/address
   - Random phone number
5. Solve reCAPTCHA (if present) using 2captcha
6. Submit form

### 3. Email Polling & Credential Extraction

- Polls mail.tm inbox every 30 seconds (configurable)
- Waits up to 45 minutes for credentials email (configurable)
- Looks for email from IPTVV Canada with subject "Your trial is now active"
- Extracts credentials using regex patterns:
  - Username: `GABSSZY5RS` format (10+ alphanumeric)
  - Password: `49180341` format (8+ digits)
  - Server Address: `http://56668610.97qaz.com` (full URL)

### 4. Output & Notifications

**Console Output:**
```
═══════════════════════════════════════════════════════════
✓ IPTVV CANADA CREDENTIALS EXTRACTED SUCCESSFULLY
═══════════════════════════════════════════════════════════
[*] Server Address: http://56668610.97qaz.com
[*] Username: GABSSZY5RS
[*] Password: 49180341
[*] M3U URL: http://56668610.97qaz.com/get.php?username=...
═══════════════════════════════════════════════════════════
```

**Telegram Notification:**
```
🤖 Zazy Automation
✓ SUCCESS
2026-05-28 10:30:00

IPTVV Canada trial created for randomuser123@mail.tm

Details:
Username: GABSSZY5RS
Host: http://56668610.97qaz.com
```

## Troubleshooting

### Issue: "No mail.tm domains available"

**Solution:** mail.tm API might be temporarily down. Wait and retry.

### Issue: "Timeout: Credentials email not received"

**Possible causes:**
- IPTVV.ca is taking longer than 45 minutes to send email
- Email went to spam/different folder
- Order submission failed

**Solution:**
- Increase `IPTVV_EMAIL_MAX_WAIT_SECONDS` to 3600 (1 hour)
- Check if order was actually submitted on the website
- Run in GUI mode (HEADLESS=False) to debug

### Issue: "Could not find clickable element containing: ['get free trial']"

**Solution:**
- The cart URL might already be on the checkout page
- Try accessing the site manually to see the current structure
- Update the button text patterns in `find_clickable_by_text()`

### Issue: "Failed to solve reCAPTCHA"

**Solution:**
- Verify TWOCAPTCHA_API_KEY is set correctly
- Check your 2captcha balance
- Run in GUI mode and solve manually

### Issue: "Could not find 'full channel' option"

**Possible causes:**
- Package might already be pre-selected
- Site structure changed

**Solution:**
- Run in GUI mode to verify what options are available
- Update selection logic in `select_full_channel_package()` function

## Development Notes

### Key Functions

- `create_mailtm_account()` - Creates temporary email and returns auth token
- `wait_for_credentials_email()` - Polls inbox with exponential backoff
- `extract_credentials_from_email()` - Regex-based credential extraction
- `fill_checkout_form()` - Smart form filling with multiple fallback strategies
- `select_full_channel_package()` - Multi-strategy package selection
- `send_webhook_callback()` - Laravel webhook integration with retries

### Extending the Script

**To add new extraction patterns:**

```python
# In extract_credentials_from_email()
username_patterns = [
    r"Username\s*:?\s*([A-Z0-9]{10,})",  # Existing
    r"Your username is:\s*(\w+)",        # Add new pattern
]
```

**To change email polling frequency:**

```bash
# In .env
IPTVV_EMAIL_POLL_SECONDS=60  # Check every minute instead of 30 seconds
```

**To add more form field mappings:**

```python
# In fill_checkout_form()
field_mappings = {
    "email": ["email", "e-mail", "mail"],
    "province": ["province", "state"],  # Add new field
}
```

## Comparison with Other Scripts

| Feature | LayerSeven | Zazy | IPTVV Canada |
|---------|-----------|------|--------------|
| Email Method | WHMCS client area | Service page | **mail.tm API** |
| Email Wait Time | 7 minutes | Immediate | **5-45 minutes** |
| Form Complexity | High (device + bouquets) | Medium | Low |
| CAPTCHA | Yes (reCAPTCHA v2) | Yes | **Maybe** |
| Unique Feature | Bouquet selection | IBO integration | **Temporary email** |

## Testing Checklist

Before running in production:

- [ ] Test in GUI mode first (HEADLESS=False)
- [ ] Verify "Get Free Trial" button is found
- [ ] Check "full channel" selection works
- [ ] Confirm email arrives within timeout
- [ ] Test credential extraction regex
- [ ] Validate webhook integration
- [ ] Test Telegram notifications
- [ ] Run end-to-end in headless mode

## License

This script is for educational and authorized testing purposes only.
