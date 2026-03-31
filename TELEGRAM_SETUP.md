# Telegram Notifications Setup Guide

This guide will help you set up Telegram notifications for the Zazy Playlist Automation script.

## Overview

The script can send you Telegram notifications for:
- ✅ Script start
- 📥 M3U playlist extraction (with credentials)
- 💾 IBO Player save success/failure
- ❌ Errors and failures
- ✅ Successful completion

---

## Prerequisites

- A Telegram account
- Access to the Telegram mobile or desktop app

---

## Step 1: Create a Telegram Bot

1. **Open Telegram** and search for `@BotFather`
   - This is Telegram's official bot for creating and managing bots

2. **Start a chat** with @BotFather by clicking the "START" button

3. **Create a new bot** by sending the command:
   ```
   /newbot
   ```

4. **Choose a name** for your bot (e.g., "Zazy Automation Bot")
   - This is the display name users will see

5. **Choose a username** for your bot (must end in "bot")
   - Example: `zazy_automation_bot` or `my_zazy_bot`

6. **Save your bot token** - BotFather will respond with a message like:
   ```
   Done! Congratulations on your new bot...

   Use this token to access the HTTP API:
   1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890
   ```

   ⚠️ **IMPORTANT**: Keep this token secret! Anyone with this token can control your bot.

---

## Step 2: Get Your Chat ID

There are two methods to get your Chat ID:

### Method A: Using @userinfobot (Recommended - Easiest)

1. **Search for `@userinfobot`** in Telegram
2. **Start a chat** with @userinfobot by clicking "START"
3. The bot will immediately send you your user information
4. **Copy your Chat ID** from the message (it will be a number like `123456789`)

### Method B: Using @RawDataBot (Alternative)

1. **Search for `@RawDataBot`** in Telegram
2. **Send any message** to @RawDataBot
3. The bot will respond with JSON data
4. **Find the "id" field** under "from" - this is your Chat ID
   ```json
   {
     "from": {
       "id": 123456789,
       ...
     }
   }
   ```

### Method C: Manual method (if bots above don't work)

1. **Message your bot** (the one you created with @BotFather)
2. **Open this URL** in your browser (replace `YOUR_BOT_TOKEN` with your actual token):
   ```
   https://api.telegram.org/botYOUR_BOT_TOKEN/getUpdates
   ```
3. **Look for the "chat" object** in the JSON response:
   ```json
   {
     "result": [{
       "message": {
         "chat": {
           "id": 123456789,
           ...
         }
       }
     }]
   }
   ```
4. **Copy the "id" value** - this is your Chat ID

---

## Step 3: Configure Environment Variables

1. **Open your `.env` file** (or create one from `.env.example`)

2. **Add the Telegram configuration**:
   ```env
   # Telegram Notification Configuration
   TELEGRAM_ENABLED=True
   TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz1234567890
   TELEGRAM_CHAT_ID=123456789
   ```

3. **Replace the values**:
   - `TELEGRAM_BOT_TOKEN`: The token you got from @BotFather (Step 1)
   - `TELEGRAM_CHAT_ID`: Your Chat ID from Step 2
   - `TELEGRAM_ENABLED`: Set to `True` to enable notifications

4. **Save the file**

---

## Step 4: Test Your Configuration

### Quick Test

To test if your Telegram notifications are working, you can run a simple Python test:

```python
# test_telegram.py
from telegram_notifier import notifier

# Test notification
success = notifier.send_notification(
    status="🧪 TEST",
    message="This is a test notification from Zazy Automation",
    details="If you see this, your Telegram integration is working!"
)

if success:
    print("✓ Notification sent successfully!")
else:
    print("✗ Failed to send notification. Check your configuration.")
```

Run it with:
```bash
python test_telegram.py
```

### Full Script Test

Run your automation script and monitor for Telegram messages:

```bash
python zazy_playlist_automation.py
```

You should receive notifications at key points during execution.

---

## Troubleshooting

### ❌ "Telegram notification failed: 400 - Bad Request"

**Possible causes:**
- Invalid bot token
- Invalid chat ID format

**Solution:**
1. Double-check your bot token from @BotFather
2. Ensure your Chat ID is a number (no quotes, letters, or spaces)
3. Make sure you've started a conversation with your bot first

### ❌ "Telegram notification failed: 401 - Unauthorized"

**Cause:** Invalid or expired bot token

**Solution:**
1. Verify your bot token in .env matches the one from @BotFather
2. Create a new bot if necessary

### ❌ "Telegram notification timeout (network issue)"

**Possible causes:**
- Network connectivity issues
- Firewall blocking Telegram API
- Docker network configuration (if running in Docker)

**Solution:**
1. Check your internet connection
2. Verify you can access https://api.telegram.org from your environment
3. If using Docker, ensure the container has network access

### ❌ "Telegram notification failed: 403 - Forbidden"

**Cause:** The bot was blocked by the user or hasn't been started

**Solution:**
1. Open Telegram and find your bot
2. Click "START" or send `/start` to the bot
3. Never block the bot

### ℹ️ Not receiving notifications but no errors

**Possible causes:**
- `TELEGRAM_ENABLED=False` in .env
- Wrong Chat ID (messages going to someone else)
- Bot was deleted or deactivated

**Solution:**
1. Verify `TELEGRAM_ENABLED=True` in your .env file
2. Double-check your Chat ID matches your Telegram account
3. Ensure your bot still exists by searching for it in Telegram

---

## Advanced Configuration

### Disable Notifications for Successful Runs Only

If you only want to be notified about errors, you can modify the script:

1. Open `zazy_playlist_automation.py`
2. Comment out the success notification lines:
   ```python
   # notifier.notify_success(m3u_url, username)
   ```

### Custom Notification Messages

You can customize notification messages by editing `telegram_notifier.py`:

```python
def notify_success(self, m3u_url=None, username=None):
    return self.send_notification(
        status="✅ SUCCESS",
        message="Your custom success message here!",
        details=f"Username: {username}" if username else None
    )
```

### Silent Notifications

Some notifications are sent with `silent=True` (no sound). To change this:

1. Open `telegram_notifier.py`
2. Find the notification method you want to modify
3. Change `silent=True` to `silent=False`

---

## Security Best Practices

1. **Never commit your .env file** to version control
   - Add `.env` to your `.gitignore` file

2. **Keep your bot token secret**
   - Treat it like a password
   - Don't share it in public channels or forums

3. **Regenerate tokens if exposed**
   - If your token is accidentally leaked, use @BotFather to regenerate it:
     ```
     /token
     [Select your bot]
     /revoke
     ```

4. **Restrict bot commands** (optional)
   - By default, your bot only sends messages (it doesn't respond to commands)
   - This is the safest configuration for automation bots

---

## Docker Configuration

If you're running the script in Docker, ensure your docker-compose.yml includes the Telegram environment variables:

```yaml
services:
  zazy-automation:
    environment:
      - TELEGRAM_ENABLED=${TELEGRAM_ENABLED}
      - TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
      - TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
```

The container needs outbound internet access to reach Telegram's API servers.

---

## Notification Reference

Here's what each notification means:

| Icon | Status | When It's Sent |
|------|--------|----------------|
| 🚀 | STARTED | Script begins execution |
| 📥 | M3U EXTRACTED | M3U playlist credentials extracted successfully |
| 💾 | IBO SAVED | Playlist uploaded to IBO Player successfully |
| ⚠️ | IBO SAVE FAILED | Failed to upload playlist to IBO Player |
| ✅ | SUCCESS | Script completed successfully |
| ❌ | ERROR | Script encountered a critical error |

---

## Support

If you encounter issues:

1. Check the console output for detailed error messages
2. Verify all configuration values in your .env file
3. Test your bot token and chat ID manually using the test methods above
4. Ensure the `requests` library is installed: `pip install requests`

---

## Resources

- [Telegram Bot API Documentation](https://core.telegram.org/bots/api)
- [BotFather Commands](https://core.telegram.org/bots#6-botfather)
- [Telegram Bot Tutorial](https://core.telegram.org/bots/tutorial)

---

**Created for Zazy Playlist Automation**
Last Updated: 2026-03-31
