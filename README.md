# Zazy TV Automation

Automated playlist creation and IBO Player integration for Zazy TV. This tool automates the entire process of signing up for a Zazy TV free trial, extracting credentials, saving the playlist to IBO Player, and downloading the M3U file.

## Features

- **Automated Account Creation**: Creates Zazy TV account with auto-generated strong passwords
- **reCAPTCHA Solving**: Automatically solves reCAPTCHA v2 using 2captcha service
- **Credential Extraction**: Automatically extracts M3U URL, username, and password from service page
- **IBO Player Integration**: Saves playlist to IBO Player via API
- **M3U Download**: Downloads playlist file to local `playlists/` directory with timestamp
- **Browser Automation**: Uses Selenium with Chrome for reliable browser-based automation

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

### Optional Configuration

- **PROMO_CODE**: Promotional code (if available)
- **LOGIN_EMAIL/LOGIN_PASSWORD**: For existing account login (set SKIP_LOGIN=False)

## Usage

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
   - Browser remains open for verification

4. **Check your files**
   - M3U playlist: `playlists/zazy_playlist_YYYY-MM-DD_HHMMSS.m3u`
   - Credentials: Displayed in terminal output

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
   docker exec zazy-automation python /app/zazy_playlist_automation.py
   ```

### Option 2: Dokploy Deployment

1. **In Dokploy dashboard:**
   - Create new application
   - Connect to your GitHub repository: `git@github.com:hichamkhourge/zazytv.git`
   - Set build type: Docker Compose
   - Configure environment variables in Dokploy UI

2. **Environment Variables to set:**
   ```
   TWOCAPTCHA_API_KEY=your_key_here
   IBOPLAYER_COOKIE=your_cookie_here
   IBOPLAYER_PLAYLIST_URL_ID=your_id_here
   IBOPLAYER_PLAYLIST_NAME=Zazy
   TZ=America/New_York  # Your timezone
   ```

3. **Deploy:**
   - Click "Deploy" button
   - Monitor logs for first run
   - Automation will run daily at 03:00 AM (based on TZ setting)

### Scheduled Execution

The Docker container runs the automation **daily at 03:00 AM** (based on the `TZ` environment variable).

**Change the schedule:**
Edit the `crontab` file before building:
```bash
# Current: 0 3 * * * (03:00 AM daily)
# Example: 0 */6 * * * (every 6 hours)
# Example: 0 0 * * 1 (every Monday at midnight)
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
├── zazy_playlist_automation.py  # Main automation script
├── requirements.txt             # Python dependencies
├── Dockerfile                   # Docker image configuration
├── docker-compose.yml           # Docker Compose orchestration
├── docker-entrypoint.sh         # Container startup script
├── crontab                      # Cron schedule configuration
├── .env                         # Configuration (not in git)
├── .env.example                 # Configuration template
├── .gitignore                   # Git ignore rules
├── .dockerignore                # Docker build ignore rules
├── playlists/                   # Downloaded M3U files (not in git)
└── README.md                    # This file
```

## Troubleshooting

### reCAPTCHA Fails
- Check your 2captcha balance at https://2captcha.com
- Ensure TWOCAPTCHA_API_KEY is correct in .env

### IBO Player Save Fails
- Verify IBOPLAYER_COOKIE is current (cookies expire)
- Check IBOPLAYER_PLAYLIST_URL_ID matches your device

### M3U Download Fails
- Verify credentials were extracted correctly
- Check IBOPLAYER_PLAYLIST_URL is accessible

### Chrome Driver Issues
```bash
# The script auto-downloads ChromeDriver, but if issues occur:
pip install --upgrade webdriver-manager
```

## Security Notes

- **Never commit `.env` file** - Contains sensitive API keys and cookies
- **Never share your 2captcha API key** - It's linked to your billing
- **IBO Player cookies expire** - Update regularly in .env
- **Downloaded M3U files** contain credentials - Keep them secure

## Dependencies

- `selenium` - Browser automation
- `webdriver-manager` - Chrome driver management
- `2captcha-python` - CAPTCHA solving service
- `python-dotenv` - Environment variable management
- `requests` - HTTP requests for API calls

## License

This project is for educational purposes only. Use responsibly and in accordance with Zazy TV's terms of service.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Support

For issues and questions:
- Open an issue on GitHub
- Check existing issues for solutions

## Changelog

### v1.0.0
- Initial release
- Automated Zazy TV account creation
- IBO Player integration
- M3U playlist download
- Automatic reCAPTCHA solving
