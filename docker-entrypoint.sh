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

# Start Xvfb virtual display for headless Chrome automation
echo "[*] Starting Xvfb virtual display..."
Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &
XVFB_PID=$!
sleep 2

# Verify Xvfb is running
if ps -p $XVFB_PID > /dev/null 2>&1; then
    echo "[✓] Xvfb started successfully (PID: $XVFB_PID, DISPLAY=:99)"
else
    echo "[!] WARNING: Xvfb failed to start, headless Chrome may have issues"
fi
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

# Start the Automation API (FastAPI/Uvicorn) in the background
echo "[*] Starting Automation API on port 5005..."
uvicorn automation_api.main:app --host 0.0.0.0 --port 5005 \
    --log-level info \
    >> /var/log/api.log 2>&1 &
API_PID=$!
sleep 2

if ps -p $API_PID > /dev/null 2>&1; then
    echo "[✓] Automation API started (PID: $API_PID)"
else
    echo "[!] WARNING: Automation API failed to start. Check /var/log/api.log"
fi
echo ""

# Start the Flask API (for Laravel integration) in the background
echo "[*] Starting Flask API on port 8899..."
gunicorn --bind 0.0.0.0:8899 \
    --workers 2 \
    --threads 4 \
    --timeout 900 \
    --access-logfile /var/log/flask-access.log \
    --error-logfile /var/log/flask-error.log \
    api_server:app &
FLASK_PID=$!
sleep 2

if ps -p $FLASK_PID > /dev/null 2>&1; then
    echo "[✓] Flask API started (PID: $FLASK_PID)"
else
    echo "[!] WARNING: Flask API failed to start. Check /var/log/flask-error.log"
fi
echo ""

# Execute the command passed to the entrypoint (usually "cron -f")
exec "$@"

