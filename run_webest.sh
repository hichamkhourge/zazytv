#!/bin/bash
#
# Simple script to run WEBESTIPTV account registration automation.
#
# Rotates the source IP each run through free public proxies (WEBEST_USE_PROXY=True,
# the default): a proxy list is fetched from a public API, validated, and the browser
# egresses through a working proxy. Uses the disposable temp-mail backend
# (WEBEST_EMAIL_BACKEND=procmail, with mail.tm fallback) to capture the OTP email.
#
# Usage:
#   ./run_webest.sh                   # Headless, via free proxy (default)
#   ./run_webest.sh gui               # GUI mode (browser visible), via free proxy
#   ./run_webest.sh headless          # Headless mode, via free proxy
#   ./run_webest.sh noproxy           # Direct connection (host IP, no proxy)
#
# Combine, e.g.:
#   ./run_webest.sh gui noproxy
#

# Change to script directory
cd "$(dirname "$0")"

# Default settings
HEADLESS=True
AUTO_EXIT=True
WEBEST_USE_PROXY=True

# Parse arguments
for arg in "$@"; do
    case $arg in
        gui)
            HEADLESS=False
            AUTO_EXIT=False
            echo "[*] Running in GUI mode (browser visible)"
            ;;
        headless)
            HEADLESS=True
            AUTO_EXIT=True
            echo "[*] Running in headless mode"
            ;;
        noproxy)
            WEBEST_USE_PROXY=False
            echo "[*] Proxy disabled (direct connection on host IP)"
            ;;
    esac
done

# Pick the Python interpreter (project venv if present, else python3).
if [ -x "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
else
    PYTHON="python3"
fi

# Display configuration
echo "======================================"
echo "WEBESTIPTV Registration Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "WEBEST_USE_PROXY:     $WEBEST_USE_PROXY"
echo "PROXY_PROTOCOL:       ${WEBEST_PROXY_PROTOCOL:-http}"
echo "EMAIL_BACKEND:        ${WEBEST_EMAIL_BACKEND:-procmail}"
echo "PYTHON:               $PYTHON"
echo "======================================"
echo ""

# Run the script
HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT WEBEST_USE_PROXY=$WEBEST_USE_PROXY \
    "$PYTHON" webestiptv_automation.py
