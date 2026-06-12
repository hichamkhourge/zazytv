#!/bin/bash
#
# Simple script to run IPTVV Canada automation
#
# Runs on the host's public IP by default; set USE_IPTVV_PROXY=True and
# IPTVV_PROXY_URL in .env to route the checkout browser through a proxy.
#
# Usage:
#   ./run_iptvv.sh                    # Run with default settings (headless)
#   ./run_iptvv.sh gui                # Run with GUI mode (browser visible)
#   ./run_iptvv.sh headless           # Run in headless mode
#

# Change to script directory
cd "$(dirname "$0")"

# Default settings
HEADLESS=True
AUTO_EXIT=True

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
    esac
done

# Display configuration
echo "======================================"
echo "IPTVV Canada Automation Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "======================================"
echo ""

# Run the script
HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT venv/bin/python iptvvcanada_automation.py
