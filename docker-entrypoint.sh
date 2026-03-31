#!/bin/bash
set -e

echo "================================================"
echo "  Zazy TV Automation - Docker Container"
echo "================================================"
echo ""

# Note: Environment variables are loaded by docker-compose.yml from .env file
# No manual parsing needed - docker-compose handles this automatically

# Display configuration (without sensitive data)
echo "Configuration:"
echo "  - Base URL: ${BASE_URL:-Not set}"
echo "  - Home URL: ${HOME_URL:-Not set}"
echo "  - Timezone: ${TZ:-UTC}"
echo "  - IBO Player Playlist Name: ${IBOPLAYER_PLAYLIST_NAME:-Not set}"
echo ""

# Check if required environment variables are set
echo "[*] Checking required environment variables..."

if [ -z "$TWOCAPTCHA_API_KEY" ]; then
    echo "[!] ERROR: TWOCAPTCHA_API_KEY is not set!"
    exit 1
fi

if [ -z "$IBOPLAYER_COOKIE" ]; then
    echo "[!] WARNING: IBOPLAYER_COOKIE is not set!"
fi

if [ -z "$IBOPLAYER_PLAYLIST_URL_ID" ]; then
    echo "[!] WARNING: IBOPLAYER_PLAYLIST_URL_ID is not set!"
fi

echo "[✓] Environment check completed"
echo ""

# Ensure playlists directory exists
mkdir -p /app/playlists
echo "[*] Playlists directory ready: /app/playlists"

# Display cron schedule
echo ""
echo "Cron Schedule:"
crontab -l
echo ""

# Start cron in foreground
echo "[*] Starting cron daemon..."
echo "[*] Logs will be written to /var/log/cron.log"
echo ""
echo "================================================"
echo "  Container is running. Automation will run at 03:00 AM daily."
echo "  To view logs: docker logs -f zazy-automation"
echo "  To access playlists: docker cp zazy-automation:/app/playlists ."
echo "================================================"
echo ""

# Execute the command passed to the entrypoint (usually "cron -f")
exec "$@"
