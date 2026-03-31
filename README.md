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

## Project Structure

```
zazytv/
├── zazy_playlist_automation.py  # Main automation script
├── requirements.txt             # Python dependencies
├── .env                         # Configuration (not in git)
├── .env.example                 # Configuration template
├── .gitignore                   # Git ignore rules
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
