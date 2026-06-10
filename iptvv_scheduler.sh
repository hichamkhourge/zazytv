#!/bin/bash
#
# IPTVV Scheduler - Manages timing logic for auto-runs
#
# This script decides when to run IPTVV automation based on:
# - 20-hour interval after successful run
# - 1-hour retry interval after failed run
#
# State files (stored in /tmp for simplicity):
# - /tmp/iptvv_last_success - Unix timestamp of last successful run
# - /tmp/iptvv_last_attempt - Unix timestamp of last attempt (success or failure)
#

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LAST_SUCCESS_FILE="/tmp/iptvv_last_success"
LAST_ATTEMPT_FILE="/tmp/iptvv_last_attempt"
LOG_FILE="/tmp/iptvv_scheduler.log"

SUCCESS_INTERVAL=$((20 * 3600))  # 20 hours in seconds
RETRY_INTERVAL=$((1 * 3600))     # 1 hour in seconds

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

get_timestamp() {
    local file="$1"
    if [ -f "$file" ]; then
        cat "$file"
    else
        echo "0"
    fi
}

save_timestamp() {
    local file="$1"
    local timestamp="$2"
    echo "$timestamp" > "$file"
}

should_run() {
    local now=$(date +%s)
    local last_success=$(get_timestamp "$LAST_SUCCESS_FILE")
    local last_attempt=$(get_timestamp "$LAST_ATTEMPT_FILE")

    # Calculate time since last success and last attempt
    local time_since_success=$((now - last_success))
    local time_since_attempt=$((now - last_attempt))

    log "Checking if should run..."
    log "  - Time since last success: ${time_since_success}s ($(($time_since_success / 3600))h)"
    log "  - Time since last attempt: ${time_since_attempt}s ($(($time_since_attempt / 3600))h)"
    log "  - Success interval threshold: ${SUCCESS_INTERVAL}s (20h)"
    log "  - Retry interval threshold: ${RETRY_INTERVAL}s (1h)"

    # If never run before (both timestamps are 0), run now
    if [ "$last_success" -eq 0 ] && [ "$last_attempt" -eq 0 ]; then
        log "  -> Decision: RUN (first time ever)"
        return 0
    fi

    # If last run was successful and 20 hours have passed
    if [ "$time_since_success" -ge "$SUCCESS_INTERVAL" ]; then
        log "  -> Decision: RUN (20+ hours since last success)"
        return 0
    fi

    # If last run failed and 1 hour has passed since last attempt
    if [ "$time_since_attempt" -ge "$RETRY_INTERVAL" ] && [ "$time_since_attempt" -gt "$time_since_success" ]; then
        log "  -> Decision: RUN (1+ hour since last failed attempt)"
        return 0
    fi

    # Calculate time remaining until next run
    if [ "$time_since_success" -lt "$SUCCESS_INTERVAL" ]; then
        local remaining=$((SUCCESS_INTERVAL - time_since_success))
        log "  -> Decision: SKIP (next run in ${remaining}s / $((remaining / 3600))h)"
    else
        local remaining=$((RETRY_INTERVAL - time_since_attempt))
        log "  -> Decision: SKIP (next retry in ${remaining}s / $((remaining / 3600))h)"
    fi

    return 1
}

run_automation() {
    local now=$(date +%s)

    log "=========================================="
    log "Starting IPTVV automation..."
    log "=========================================="

    # Save attempt timestamp
    save_timestamp "$LAST_ATTEMPT_FILE" "$now"

    # Run the automation script
    cd "$SCRIPT_DIR" || exit 1

    # Runs on the host's public IP (direct connection, no proxy).
    # Headless mode, auto-exit
    HEADLESS=True AUTO_EXIT=True \
        IPTVV_DEBUG_DIR=/tmp \
        venv/bin/python iptvvcanada_automation.py 2>&1 | tee -a "$LOG_FILE"

    local exit_code=${PIPESTATUS[0]}

    if [ "$exit_code" -eq 0 ]; then
        log "[SUCCESS] IPTVV automation completed successfully"
        log "Updating last success timestamp: $now"
        save_timestamp "$LAST_SUCCESS_FILE" "$now"
        log "Next run scheduled in 20 hours"
    else
        log "[FAILURE] IPTVV automation failed with exit code $exit_code"
        log "Will retry in 1 hour"
    fi

    log "=========================================="
    return "$exit_code"
}

# Main execution
main() {
    log "IPTVV Scheduler started"

    if should_run; then
        run_automation
    else
        log "Skipping run - conditions not met"
    fi

    log "IPTVV Scheduler finished"
}

main "$@"
