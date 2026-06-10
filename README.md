# Zazy Automation Suite

Automated playlist creation and service activation for Zazy TV and UGEEN.LIVE. This suite includes two automation tools:
- **Zazy Playlist Automation**: Automates Zazy TV account creation, credential extraction, and IBO Player integration
- **UGEEN API Scraper**: Automates UGEEN.LIVE service activation with anti-detection and session management

## Features

### Zazy Playlist Automation
- **Automated Account Creation**: Creates Zazy TV account with auto-generated strong passwords
- **reCAPTCHA Solving**: Automatically solves reCAPTCHA v2 using 2captcha service
- **Credential Extraction**: Automatically extracts M3U URL, username, and password from service page
- **IBO Player Integration**: Saves playlist to IBO Player via API
- **M3U Download**: Downloads playlist file to local `playlists/` directory with timestamp
- **Browser Automation**: Uses Selenium with Chrome for reliable browser-based automation

### IPTVtune Free Trial Automation
- **WHMCS Checkout Automation**: Orders the free "1 Day Free Trial" product at `https://iptvtune.com/pay/` (`cart.php?a=confproduct&i=0`) — same engine as LayerSeven
- **reCAPTCHA Solving**: Solves reCAPTCHA v2 via 2captcha when present on checkout
- **Bouquet Selection**: Selects all bouquets by default (`IPTVTUNE_BOUQUET_MODE=all`); `--bouquets 1,3,60,63` selects specific ones
- **Credential Extraction**: After ordering, keeps the browser open and polls the client-area email history (`clientarea.php?action=emails`) until the account-ready email arrives, then extracts Xtream username, password, and portal URL and builds the M3U URL
- **IBO Player Update** (optional): pushes the extracted credentials into an existing IBO Player playlist via `savePlaylist`. Enable with `IPTVTUNE_IBOPLAYER_ENABLED=True` and set `IPTVTUNE_IBOPLAYER_COOKIE` + `IPTVTUNE_IBOPLAYER_PLAYLIST_URL_ID` (+ optional `IPTVTUNE_IBOPLAYER_PLAYLIST_NAME`)
- **Outputs**: Webhook callback (Laravel mode) or Telegram notification
- **Config**: `IPTVTUNE_*` keys in `.env` (see `.env.example`). In practice the trial email lands in ~3 min; the default waits up to 1 hour (`IPTVTUNE_EMAIL_MAX_WAIT_SECONDS=3600`) polling once a minute (`IPTVTUNE_EMAIL_POLL_SECONDS=60`), since IPTVtune advertises "within 1-3 hours". `IPTVTUNE_READY_EMAIL_SUBJECT` defaults to the verified subject `IPTV Access Information`.
- **API**: `POST /api/generate/iptvtune` and `/api/generate/iptvtune/sync` (both accept an optional `bouquets` array)
- **Schedule**: `iptvtune_scheduler.sh` (20 h success / 1 h retry) and a daily 7:00 AM `crontab` entry

### UGEEN API Scraper
- **Stealth Browser Automation**: Uses undetected-chromedriver for anti-detection
- **Session Management**: Saves and reuses JWT tokens for efficiency
- **Human Behavior Simulation**: Random delays, mouse movements, and scrolling
- **2captcha Integration**: Automatic reCAPTCHA solving when needed
- **Data Persistence**: Saves activation codes and session data
- **Telegram Notifications**: Real-time status updates (optional)
- **Configurable Scheduling**: Runs automatically via cron

## Prerequisites

- Python 3.8 or higher
- Chrome browser
- 2captcha account with API key ([Sign up here](https://2captcha.com))
- IBO Player account and device configuration

## Installation

1. **Clone the repository**
   ```bash
   git clone git@github.com:hichamkhourge/zazytv.git
   cd zazytv
   ```

2. **Create and activate virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your actual credentials
   ```

## Configuration

Edit the `.env` file with your credentials:

### Required Configuration

#### For Zazy Playlist Automation:
- **TWOCAPTCHA_API_KEY**: Your 2captcha API key
  - Get from: https://2captcha.com/enterpage

- **IBOPLAYER_COOKIE**: Your IBO Player session cookie
  - How to get:
    1. Login to https://iboplayer.com
    2. Open browser DevTools (F12)
    3. Go to Application > Cookies
    4. Copy the entire cookie string

- **IBOPLAYER_PLAYLIST_URL_ID**: Your IBO Player device playlist ID
  - Find in your IBO Player device settings

#### For UGEEN API Scraper:
- **UGEEN_EMAIL**: Your UGEEN.LIVE login email
- **UGEEN_PASSWORD**: Your UGEEN.LIVE password
- **UGEEN_PACKAGE_ID**: Package ID to activate (default: 384)
- **TWOCAPTCHA_API_KEY**: Same 2captcha API key used for Zazy

### Optional Configuration

#### Zazy-specific:
- **PROMO_CODE**: Promotional code (if available)
- **LOGIN_EMAIL/LOGIN_PASSWORD**: For existing account login (set SKIP_LOGIN=False)

#### UGEEN-specific:
- **UGEEN_URL**: UGEEN base URL (default: http://ugeen.live)
- **UGEEN_HEADLESS**: Run in headless mode (default: True)
- **UGEEN_SESSION_DIR**: Session storage directory (default: ./ugeen_sessions)
- **UGEEN_DATA_DIR**: Data output directory (default: ./ugeen_data)

#### Shared Settings:
- **TELEGRAM_ENABLED**: Enable Telegram notifications (default: False)
- **TELEGRAM_BOT_TOKEN**: Your Telegram bot token
- **TELEGRAM_CHAT_ID**: Your Telegram chat ID

## Usage

### Running Zazy Playlist Automation

1. **Activate virtual environment**
   ```bash
   source venv/bin/activate
   ```

2. **Run the automation script**
   ```bash
   python zazy_playlist_automation.py
   ```

3. **What happens:**
   - Navigates to Zazy TV website
   - Clicks "Free Trial" and proceeds to checkout
   - Fills registration form with auto-generated data
   - Solves reCAPTCHA automatically
   - Completes order
   - Navigates to service details and extracts credentials
   - Saves playlist to IBO Player
   - Downloads M3U file to `playlists/` directory

4. **Check your files**
   - M3U playlist: `playlists/zazy_playlist_YYYY-MM-DD_HHMMSS.m3u`
   - Credentials: Displayed in terminal output

### Running UGEEN API Scraper

1. **Activate virtual environment**
   ```bash
   source venv/bin/activate
   ```

2. **Run the scraper**
   ```bash
   python ugeen_api_scraper.py
   ```

3. **What happens:**
   - Checks for existing valid session (reuses if available)
   - Logs in with stealth browser using your credentials
   - Navigates to renewal page and requests activation code
   - Decodes JWT token to extract activation code
   - Submits subscription form with the code
   - Saves activation data to `ugeen_data/` directory
   - Sends Telegram notification (if enabled)

4. **Check your files**
   - Activation data: `ugeen_data/activation_YYYY-MM-DD_HHMMSS.json`
   - Session cache: `ugeen_sessions/ugeen_session.json`

## Docker Deployment

### Option 1: Docker Compose (Recommended)

1. **Configure environment**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

2. **Build and start container**
   ```bash
   docker-compose up -d
   ```

3. **View logs**
   ```bash
   # Live logs
   docker logs -f zazy-automation

   # Cron logs
   docker exec zazy-automation tail -f /var/log/cron.log
   ```

4. **Access downloaded playlists**
   ```bash
   # Copy playlists to current directory
   docker cp zazy-automation:/app/playlists ./playlists

   # Or list files
   docker exec zazy-automation ls -lah /app/playlists
   ```

5. **Manual run (trigger immediately)**
   ```bash
   # Run Zazy automation
   docker exec zazy-automation python /app/zazy_playlist_automation.py

   # Run UGEEN scraper
   docker exec zazy-automation python /app/ugeen_api_scraper.py
   ```

6. **Access UGEEN data**
   ```bash
   # View activation history
   docker exec zazy-automation ls -lh /app/ugeen_data

   # Copy activation data
   docker cp zazy-automation:/app/ugeen_data ./ugeen_data

   # View specific activation
   docker exec zazy-automation cat /app/ugeen_data/activation_2026-04-06_060000.json
   ```

### Option 2: Dokploy Deployment

1. **In Dokploy dashboard:**
   - Create new application
   - Connect to your GitHub repository: `git@github.com:hichamkhourge/zazytv.git`
   - Set build type: Docker Compose
   - Configure environment variables in Dokploy UI

2. **Environment Variables to set:**
   ```
   # 2captcha (required for both)
   TWOCAPTCHA_API_KEY=your_key_here

   # Zazy-specific
   IBOPLAYER_COOKIE=your_cookie_here
   IBOPLAYER_PLAYLIST_URL_ID=your_id_here
   IBOPLAYER_PLAYLIST_NAME=Zazy

   # UGEEN-specific
   UGEEN_EMAIL=your_email@example.com
   UGEEN_PASSWORD=your_password
   UGEEN_PACKAGE_ID=384

   # Telegram (optional)
   TELEGRAM_ENABLED=True
   TELEGRAM_BOT_TOKEN=your_bot_token
   TELEGRAM_CHAT_ID=your_chat_id

   # General
   TZ=America/New_York  # Your timezone
   ```

3. **Deploy:**
   - Click "Deploy" button
   - Monitor logs for first run
   - Zazy automation will run daily at 03:00 AM
   - UGEEN scraper will run daily at 06:00 AM
   - (Times based on TZ setting)

### Scheduled Execution

The Docker container runs both automations on a daily schedule (based on the `TZ` environment variable):
- **Zazy Playlist Automation**: Daily at 03:00 AM
- **UGEEN API Scraper**: Daily at 06:00 AM

**Change the schedule:**
Edit the `crontab` file before building:
```bash
# Zazy: 0 3 * * * (03:00 AM daily)
# UGEEN: 0 6 * * * (06:00 AM daily)

# Example alternatives:
# 0 */6 * * * (every 6 hours)
# 0 0 * * 1 (every Monday at midnight)
# 0 12,18 * * * (twice daily at noon and 6 PM)
```

**Timezone Configuration:**
Set the `TZ` environment variable in docker-compose.yml:
```yaml
environment:
  - TZ=America/New_York  # Eastern Time
  # - TZ=Europe/London    # GMT/BST
  # - TZ=Asia/Dubai       # Gulf Standard Time
```

### Docker Environment Variables

Additional Docker-specific variables:

- **HEADLESS**: `True` (default) for headless mode, `False` for GUI mode
- **AUTO_EXIT**: `True` (default) to exit after completion, `False` to keep running
- **TZ**: Timezone for cron schedule (default: `UTC`)

### Accessing Playlists from Volume

**List all playlists:**
```bash
docker exec zazy-automation ls -lh /app/playlists
```

**Copy specific playlist:**
```bash
docker cp zazy-automation:/app/playlists/zazy_playlist_2026-03-31_030000.m3u ./
```

**Copy all playlists:**
```bash
docker cp zazy-automation:/app/playlists ./
```

**View playlist content:**
```bash
docker exec zazy-automation cat /app/playlists/zazy_playlist_2026-03-31_030000.m3u
```

### Container Management

**Stop container:**
```bash
docker-compose down
```

**Restart container:**
```bash
docker-compose restart
```

**Rebuild after code changes:**
```bash
docker-compose up -d --build
```

**Remove container and volumes:**
```bash
docker-compose down -v
```

## Project Structure

```
zazytv/
├── zazy_playlist_automation.py  # Zazy automation script
├── ugeen_api_scraper.py         # UGEEN scraper script
├── telegram_notifier.py         # Telegram notification module
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Docker image configuration
├── docker-compose.yml           # Docker Compose orchestration
├── docker-entrypoint.sh         # Container startup script
├── crontab                      # Cron schedule (both scripts)
├── .env                         # Configuration (not in git)
├── .env.example                 # Configuration template
├── .gitignore                   # Git ignore rules
├── .dockerignore                # Docker build ignore rules
├── playlists/                   # Zazy M3U files (not in git)
├── ugeen_sessions/              # UGEEN session cache (not in git)
├── ugeen_data/                  # UGEEN activation data (not in git)
└── README.md                    # This file
```

## Troubleshooting

### Common Issues

#### reCAPTCHA Fails
- Check your 2captcha balance at https://2captcha.com
- Ensure TWOCAPTCHA_API_KEY is correct in .env
- Both scripts share the same 2captcha account

#### Zazy-Specific Issues

**IBO Player Save Fails:**
- Verify IBOPLAYER_COOKIE is current (cookies expire)
- Check IBOPLAYER_PLAYLIST_URL_ID matches your device

**M3U Download Fails:**
- Verify credentials were extracted correctly
- Check IBOPLAYER_PLAYLIST_URL is accessible

#### UGEEN-Specific Issues

**Login Fails Repeatedly:**
- Verify UGEEN_EMAIL and UGEEN_PASSWORD are correct
- Check if account is locked or requires verification
- Try running in non-headless mode locally: `UGEEN_HEADLESS=False python ugeen_api_scraper.py`

**Session Not Reused:**
- Check if `ugeen_sessions/` directory exists and is writable
- Session expires after 24 hours (this is normal)
- Old sessions are automatically cleaned up

**Activation Fails:**
- Verify UGEEN_PACKAGE_ID is correct (default: 384)
- Check logs for specific error messages
- Ensure account has available activations

#### Chrome Driver Issues
```bash
# The scripts auto-manage ChromeDriver, but if issues occur:
pip install --upgrade webdriver-manager undetected-chromedriver
```

#### Docker Issues

**Container won't start:**
```bash
# Check logs
docker logs zazy-automation

# Rebuild container
docker-compose up -d --build
```

**Cron not running:**
```bash
# Verify cron is running
docker exec zazy-automation ps aux | grep cron

# Check cron logs
docker exec zazy-automation cat /var/log/cron.log
```

## Security Notes

- **Never commit `.env` file** - Contains sensitive credentials
- **Never share your 2captcha API key** - It's linked to your billing
- **IBO Player cookies expire** - Update regularly in .env
- **Downloaded M3U files** contain credentials - Keep them secure
- **UGEEN credentials** - Stored in environment variables, never hardcoded
- **Session files** (`ugeen_sessions/`) contain JWT tokens - Keep them secure
- **Activation data** (`ugeen_data/`) may contain sensitive information - Protect accordingly

## Dependencies

- `selenium` - Browser automation (Zazy)
- `undetected-chromedriver` - Stealth browser automation (UGEEN)
- `webdriver-manager` - Chrome driver management
- `2captcha-python` - CAPTCHA solving service
- `python-dotenv` - Environment variable management
- `requests` - HTTP requests for API calls
- `beautifulsoup4` - HTML parsing
- `fake-useragent` - User agent spoofing

See `requirements.txt` for complete list with versions.

## License

This project is for educational purposes only. Use responsibly and in accordance with the terms of service of Zazy TV and UGEEN.LIVE.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues and questions:
- Open an issue on GitHub
- Check existing issues for solutions

## Changelog

### v2.0.0 (2026-04-06)
- **NEW**: Added UGEEN API Scraper automation
- **NEW**: Telegram notification support for both scripts
- **NEW**: Session management and caching for UGEEN
- **NEW**: Combined Docker container for both automations
- **IMPROVED**: Better error handling and logging
- **IMPROVED**: Configurable schedules (Zazy: 3 AM, UGEEN: 6 AM)
- **IMPROVED**: Environment-based configuration for all credentials

### v1.0.0
- Initial release
- Automated Zazy TV account creation
- IBO Player integration
- M3U playlist download
- Automatic reCAPTCHA solving
