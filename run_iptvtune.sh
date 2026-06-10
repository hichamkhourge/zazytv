#!/bin/bash
#
# Simple script to run IPTVtune free-trial automation
#
# The automation always runs on the host's public IP (direct connection, no proxy).
# After ordering it keeps the browser open and polls the client-area email history until
# the account-ready email arrives (IPTVTUNE_EMAIL_MAX_WAIT_SECONDS, default up to 1 hour,
# polling every IPTVTUNE_EMAIL_POLL_SECONDS, default 60s).
#
# Usage:
#   ./run_iptvtune.sh                 # Run with default settings (headless)
#   ./run_iptvtune.sh gui             # Run with GUI mode (browser visible)
#   ./run_iptvtune.sh headless        # Run in headless mode
#
#   # Any extra args are passed through to the Python script, e.g.:
#   ./run_iptvtune.sh --user-id 123 --callback-url https://app.com/api/webhooks/iptvtune-automation
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
echo "IPTVtune Automation Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "======================================"
echo ""

# Run the script
HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT venv/bin/python iptvtune_automation.py "${PY_ARGS[@]}"
