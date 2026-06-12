#!/bin/bash
#
# IPTVV Auto-Run Wrapper
#
# This script is called automatically when WSL starts (via .bashrc).
# It runs in the background and calls the scheduler to decide if automation should run.
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIDFILE="/tmp/iptvv_autorun.pid"
LOG_FILE="/tmp/iptvv_autorun.log"

# Check if already running
if [ -f "$PIDFILE" ]; then
    old_pid=$(cat "$PIDFILE")
    if ps -p "$old_pid" > /dev/null 2>&1; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] IPTVV auto-run already running (PID: $old_pid)" >> "$LOG_FILE"
        exit 0
    else
        # Stale PID file, remove it
        rm -f "$PIDFILE"
    fi
fi

# Save current PID
echo $$ > "$PIDFILE"

# Log startup
echo "==========================================" >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] IPTVV Auto-Run started (PID: $$)" >> "$LOG_FILE"
echo "==========================================" >> "$LOG_FILE"

# Change to script directory
cd "$SCRIPT_DIR" || exit 1

# Run the scheduler
bash iptvv_scheduler.sh >> "$LOG_FILE" 2>&1

# Clean up PID file
rm -f "$PIDFILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] IPTVV Auto-Run finished" >> "$LOG_FILE"
