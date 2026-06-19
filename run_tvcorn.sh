#!/bin/bash
#
# Simple script to run TVCORN free-trial automation.
#
# The automation runs on the host's public IP (direct connection, no proxy). It
# fills the tvcorn.com/trial multi-step form using a temporary inbox (mail.tm or
# procmail.xyz, selected via TVCORN_EMAIL_BACKEND), reads the 6-digit OTP emailed
# by the site, verifies it, then scrapes the generated Xtream credentials and
# optionally pushes them into an IBO Player playlist (TVCORN_IBOPLAYER_ENABLED).
#
# Usage:
#   ./run_tvcorn.sh                  # Run with default settings (headless)
#   ./run_tvcorn.sh gui              # Run with GUI mode (browser visible)
#   ./run_tvcorn.sh headless         # Run in headless mode
#
#   # Any extra args are passed through to the Python script, e.g.:
#   ./run_tvcorn.sh --user-id 123 --callback-url https://app.com/api/webhooks/tvcorn-automation
#

cd "$(dirname "$0")"

HEADLESS=True
AUTO_EXIT=True

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

echo "======================================"
echo "TVCORN Automation Runner"
echo "======================================"
echo "HEADLESS:             $HEADLESS"
echo "AUTO_EXIT:            $AUTO_EXIT"
echo "EMAIL_BACKEND:        ${TVCORN_EMAIL_BACKEND:-procmail}"
echo "======================================"
echo ""

HEADLESS=$HEADLESS AUTO_EXIT=$AUTO_EXIT venv/bin/python tvcorn_automation.py "${PY_ARGS[@]}"
