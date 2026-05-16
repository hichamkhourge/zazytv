# Telegram Bot for Uzeen Control

## 🤖 Overview

Interactive Telegram bot that lets you control the Uzeen playlist updater from your phone. The bot runs 24/7 in your Docker container alongside the existing Flask and FastAPI servers.

---

## ✨ Features

### Commands Available

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and command list |
| `/help` | Show all available commands |
| `/uzeen` | **Run uzeen playlist updater now** |
| `/uzeen_status` | Show current credentials and last update time |
| `/uzeen_history` | View credential change history |
| `/ping` | Health check (verify bot is alive) |

### Key Capabilities

- ✅ Trigger uzeen script from your phone anytime
- ✅ Get real-time status and credential information
- ✅ View credential change history and patterns
- ✅ Instant responses (~1 second)
- ✅ Secure (only authorized chat IDs)
- ✅ Works alongside automated cron jobs
- ✅ No server configuration needed (polling-based)

---

## 🚀 Quick Start

### Prerequisites

Your bot is already configured! The `.env` file has:
```bash
TELEGRAM_ENABLED=True
TELEGRAM_BOT_TOKEN=8772319617:AAEQG-OLBXmE_5tSkbe0negrT6qkN-6IoZg
TELEGRAM_CHAT_ID=6591796414
```

### Deployment

**1. Build and deploy to Dokploy:**
```bash
git add .
git commit -m "Add Telegram bot for uzeen control"
git push
```

**2. Dokploy will automatically:**
- Install `python-telegram-bot` from requirements.txt
- Start the bot via `docker-entrypoint.sh`
- Run bot as background service
- Enable commands from your phone

**3. Verify bot is running:**
- Check Dokploy logs for: `[✓] Telegram Bot started`
- Or check container logs: `docker logs <container_id> | grep Telegram`

### Test the Bot

Open Telegram on your phone and send:

1. **Health check:**
   ```
   /ping
   ```
   Expected response: `✅ Bot is alive and running!`

2. **Check status:**
   ```
   /uzeen_status
   ```
   Shows current credentials and last update time

3. **Run updater:**
   ```
   /uzeen
   ```
   Triggers the script and returns results in ~30-60 seconds

---

## 📋 How It Works

### Architecture

```
Docker Container (zazy-automation)
├── Xvfb (virtual display)
├── FastAPI (port 5005)
├── Flask API (port 8899)
├── Telegram Bot ← NEW (polling mode)
└── Cron (foreground process)
```

### Polling vs Webhooks

**This bot uses POLLING** (not webhooks):
- Bot checks Telegram servers every 1 second
- No public URL needed
- Works perfectly with Dokploy
- Instant message delivery via long polling

### Integration with Cron

The bot works **alongside** the cron job:

| Method | Frequency | Purpose |
|--------|-----------|---------|
| **Cron** | Every 6 hours | Automatic updates |
| **Bot** | On-demand | Manual trigger from phone |

**Both use the same:**
- Script: `uzeen_playlist_updater.py`
- State file: `uzeen_playlist_state.json`
- Change detection (prevents conflicts)

**Example timeline:**
```
00:00 - Cron runs (automatic) ✅
02:30 - You send /uzeen (manual) ✅
06:00 - Cron runs (automatic) ✅
08:45 - You check /uzeen_status
12:00 - Cron runs (automatic) ✅
...
```

---

## 💬 Usage Examples

### Scenario 1: Check Current Status

```
You: /uzeen_status
Bot: 📋 Uzeen Playlist Status

     Host: http://abd2022.xyz:80
     Username: 34BWKUE
     Password: U1MZZ64
     Playlist ID: 6a08707dc7c4b3c03a03d600

     Last Updated: 2026-05-16 15:15:09
     (2h 30m ago)

     ✅ Playlist is configured
```

### Scenario 2: Trigger Update

```
You: /uzeen
Bot: 🔄 Running Uzeen Playlist Updater...

     Please wait, this may take 30-60 seconds.

[30 seconds later...]

Bot: ✅ Uzeen Update Complete

     ℹ️ No changes detected
     Playlist is already up to date!

     Current Credentials:
     Host: http://abd2022.xyz:80
     Username: 34BWKUE
     Password: U1MZZ64
```

### Scenario 3: Credentials Changed

```
You: /uzeen
Bot: 🔄 Running Uzeen Playlist Updater...

[45 seconds later...]

Bot: ✅ Uzeen Update Successful!

     New Credentials:
     Host: http://newserver.xyz:80
     Username: 5CXWQRT
     Password: 9PLMNBV

     Changed: username, password, playlist_url

     IboPlayer playlist updated!
```

### Scenario 4: View History

```
You: /uzeen_history
Bot: 📊 Credential Change History

     3. 2026-05-23 14:30
        Host: http://server3.xyz:80
        User: 7DHMNBV
        Pass: 2QWEASD

     2. 2026-05-16 15:15
        Host: http://abd2022.xyz:80
        User: 34BWKUE
        Pass: U1MZZ64

     1. 2026-05-10 10:00
        Host: http://oldserver.xyz:80
        User: OLDUSER
        Pass: OLDPASS

     Showing last 5 of 3 total changes
```

---

## 🔧 Configuration

### Environment Variables

**Required** (already configured):
```bash
TELEGRAM_ENABLED=True               # Enable bot
TELEGRAM_BOT_TOKEN=your_token_here  # From @BotFather
TELEGRAM_CHAT_ID=your_chat_id       # Your Telegram chat ID
```

**Optional** (advanced):
```bash
TELEGRAM_BOT_POLL_INTERVAL=1.0  # Seconds between polls (default: 1.0)
TELEGRAM_BOT_TIMEOUT=30         # Long polling timeout (default: 30)
```

### Authorization

Only users with chat IDs in `TELEGRAM_CHAT_ID` can use the bot.

**To add multiple authorized users:**
```bash
TELEGRAM_CHAT_ID=123456789,987654321,555555555
```

**To find your chat ID:**
1. Send a message to your bot
2. Visit: `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Look for `"chat":{"id":123456789}`

---

## 🛡️ Security

### Access Control
- Only authorized chat IDs can execute commands
- Unauthorized users get "⛔ Unauthorized access" message
- All command attempts are logged

### What's Logged
```
2026-05-16 15:30:22 - INFO - Starting uzeen updater for chat_id: 6591796414
2026-05-16 15:30:55 - INFO - Uzeen updater completed for chat_id: 6591796414, success: True
2026-05-16 15:31:10 - WARNING - Unauthorized /uzeen attempt from chat_id: 999999999
```

### Logs Location
- Bot logs: `/var/log/telegram-bot.log` (in Docker volume)
- View via: Dokploy UI or `docker logs <container>`

---

## 📊 Monitoring

### Check if Bot is Running

**Method 1: Dokploy UI**
- Go to your deployment
- Check logs for: `[✓] Telegram Bot started (PID: ...)`

**Method 2: Docker command**
```bash
docker logs <container_id> | grep "Telegram Bot"
```

**Method 3: From phone**
```
Send: /ping
Expected: ✅ Bot is alive and running!
```

### Bot Logs

**View bot activity:**
```bash
# SSH into server
docker exec -it <container_id> tail -f /var/log/telegram-bot.log
```

**What you'll see:**
```
2026-05-16 15:15:00 - INFO - Zazy Telegram Bot Starting...
2026-05-16 15:15:00 - INFO - Authorized chat IDs: [6591796414]
2026-05-16 15:15:00 - INFO - Commands: /start, /help, /uzeen, /uzeen_status, /uzeen_history, /ping
2026-05-16 15:15:00 - INFO - Bot started! Polling for commands...
```

---

## 🔄 Auto-Restart

The bot automatically restarts if it crashes:

**Restart Policy** (from docker-compose.yml):
```yaml
restart: unless-stopped
```

**What this means:**
- Bot crashes → Docker automatically restarts container
- All services (Flask, FastAPI, Bot, Cron) restart together
- No manual intervention needed

---

## 🐛 Troubleshooting

### Bot Not Responding

**1. Check if bot is running:**
```bash
docker logs <container_id> | grep "Telegram Bot"
```

**2. Look for startup errors:**
```bash
docker exec -it <container_id> cat /var/log/telegram-bot.log
```

**3. Common issues:**

| Problem | Solution |
|---------|----------|
| `TELEGRAM_BOT_TOKEN not found` | Set in `.env` file |
| `TELEGRAM_CHAT_ID not found` | Set in `.env` file |
| Bot started but not responding | Check if `TELEGRAM_ENABLED=True` |
| `Unauthorized access` | Your chat_id not in `TELEGRAM_CHAT_ID` |

### Bot Crashes

**Check logs:**
```bash
docker exec -it <container_id> tail -100 /var/log/telegram-bot.log
```

**Common causes:**
- Invalid bot token
- Network issues
- Python dependency missing

**Solution:** Container will auto-restart. Check logs for root cause.

### Commands Time Out

If `/uzeen` times out (>5 minutes):
- Large M3U file taking very long to download
- Server (uzeen.net) is slow or down
- Check uzeen logs: `/var/log/cron.log`

---

## 📱 Best Practices

### When to Use Bot vs. Cron

**Use Bot (/uzeen):**
- ✅ Playlist suddenly stopped working
- ✅ You know credentials just changed
- ✅ Want to force an immediate check
- ✅ Testing after configuration changes

**Let Cron Handle:**
- ✅ Normal daily operation
- ✅ Overnight updates
- ✅ Automatic maintenance
- ✅ When you're away/sleeping

### Checking Status

**Daily check:**
```
Morning: /uzeen_status (verify credentials are current)
```

**After changes:**
```
After running /uzeen: /uzeen_history (see if it changed)
```

**Troubleshooting:**
```
/uzeen_status → See current state
/uzeen → Force update
/uzeen_history → See change pattern
```

---

## 🔮 Future Enhancements

Possible additions (not yet implemented):

- [ ] `/uzeen_cron` - Show next scheduled cron run
- [ ] `/logs` - Get recent log entries
- [ ] `/stats` - Show update statistics
- [ ] `/test` - Test credentials without updating
- [ ] Inline buttons for common actions
- [ ] Multi-playlist support
- [ ] Custom notifications when credentials change

---

## 📝 Files

**Created:**
- `telegram_bot.py` - Bot implementation (~400 lines)
- `TELEGRAM_BOT_README.md` - This documentation

**Modified:**
- `requirements.txt` - Added `python-telegram-bot==20.7`
- `docker-entrypoint.sh` - Added bot startup
- `.env.example` - Added bot configuration

**Logs:**
- `/var/log/telegram-bot.log` - Bot activity log
- `/var/log/cron.log` - Cron job execution (includes uzeen)

---

## 🎯 Summary

**What You Get:**
- ✅ Trigger uzeen script from phone with `/uzeen`
- ✅ Check status anytime with `/uzeen_status`
- ✅ View history with `/uzeen_history`
- ✅ ~1 second response time
- ✅ Works 24/7 in Docker
- ✅ Secure authorization
- ✅ Auto-restart on failure
- ✅ Compatible with existing cron jobs

**Next Steps:**
1. Deploy to Dokploy (git push)
2. Test with `/ping` from your phone
3. Try `/uzeen_status` to see current credentials
4. Use `/uzeen` when you need immediate update

Enjoy controlling your uzeen playlist from your phone! 📱✨
