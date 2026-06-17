#!/bin/bash
#
# Run IPTVV Canada free-trial automation (procmail email backend).
#
# The automation runs on the host's public IP (direct connection, no proxy). It
# generates a disposable address via api.procmail.xyz, submits the IPTVV.ca
# checkout, polls the inbox until the credentials email arrives, then optionally
# pushes the Xtream credentials into an IBO Player playlist.
#
# Usage:
#   ./run_iptvv.sh                    # Run with default settings (headless)
#   ./run_iptvv.sh gui                # Run with GUI mode (browser visible)
#   ./run_iptvv.sh headless           # Run in headless mode
#
#   # Any extra args are passed through to the Python script, e.g.:
#   ./run_iptvv.sh --preflight-only
#   ./run_iptvv.sh --user-id 123 --callback-url https://app.com/api/webhooks/iptvv-automation
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

# Pick a Python interpreter (venv if present, else system python3)
PY=python3
[ -x venv/bin/python ] && PY=venv/bin/python

# Auto-detect a ChromeDriver so runs don't depend on webdriver_manager's network
# lookup (which can fail offline). Honor an existing CHROMEDRIVER_PATH; otherwise
# prefer the Docker location, else the newest cached ~/.wdm driver. If none is
# found we leave it unset and let the Python fallback handle it.
if [ -z "$CHROMEDRIVER_PATH" ]; then
    if [ -x /usr/local/bin/chromedriver ]; then
        CHROMEDRIVER_PATH=/usr/local/bin/chromedriver
    else
        CHROMEDRIVER_PATH=$(ls -1t "$HOME"/.wdm/drivers/chromedriver/linux64/*/chromedriver-linux64/chromedriver 2>/dev/null | head -n1)
    fi
fi
if [ -n "$CHROMEDRIVER_PATH" ]; then
    export CHROMEDRIVER_PATH
    echo "[*] CHROMEDRIVER_PATH:    $CHROMEDRIVER_PATH"
fi

# Display configuration
echo "======================================"
echo "IPTVV Canada Automation Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "======================================"
echo ""

# Run the script
HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT "$PY" iptvvcanada_automation.py "${PY_ARGS[@]}"
