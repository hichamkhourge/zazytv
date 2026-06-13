#!/bin/bash
#
# Simple script to run ViewTVY free-trial automation
#
# The automation always runs on the host's public IP (direct connection, no proxy).
# After ordering it polls the client-area email history until the credentials email
# arrives (VIEWTVY_EMAIL_MAX_WAIT_SECONDS, default up to 1 hour, polling every
# VIEWTVY_EMAIL_POLL_SECONDS, default 60s), then optionally pushes the extracted
# Xtream credentials into an IBO Player playlist (VIEWTVY_IBOPLAYER_ENABLED).
#
# Usage:
#   ./run_viewtvy.sh                  # Run with default settings (headless)
#   ./run_viewtvy.sh gui              # Run with GUI mode (browser visible)
#   ./run_viewtvy.sh headless         # Run in headless mode
#
#   # Any extra args are passed through to the Python script, e.g.:
#   ./run_viewtvy.sh --iboplayer-account 2
#   ./run_viewtvy.sh --user-id 123 --callback-url https://app.com/api/webhooks/viewtvy-automation
#

# Change to script directory
cd "$(dirname "$0")"

# Default settings
HEADLESS=True
AUTO_EXIT=True

# Parse arguments: handle gui/headless modes, pass everything else to Python
PY_ARGS=()
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
        *)
            PY_ARGS+=("$arg")
            ;;
    esac
done

# Display configuration
echo "======================================"
echo "ViewTVY Automation Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "======================================"
echo ""

# Run the script
HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT venv/bin/python viewtvy_automation.py "${PY_ARGS[@]}"
