#!/usr/bin/env python3
"""
Check Uzeen Credential Changes and History

This script monitors when uzeen credentials change to help determine
the optimal cron schedule. Run it periodically to track the pattern.
"""

import os
import json
import sys
from datetime import datetime
from pathlib import Path

STATE_FILE = "uzeen_playlist_state.json"
HISTORY_FILE = "uzeen_credential_history.json"


def load_state():
    """Load current state"""
    if not os.path.exists(STATE_FILE):
        print(f"[!] State file not found: {STATE_FILE}")
        print(f"[!] Run uzeen_playlist_updater.py first")
        return None

    with open(STATE_FILE, 'r') as f:
        return json.load(f)


def load_history():
    """Load credential change history"""
    if not os.path.exists(HISTORY_FILE):
        return {"changes": []}

    with open(HISTORY_FILE, 'r') as f:
        return json.load(f)


def save_history(history):
    """Save credential change history"""
    with open(HISTORY_FILE, 'w') as f:
        json.dump(history, f, indent=2)


def analyze_history(history):
    """Analyze credential change patterns"""
    if not history.get("changes"):
        print("\n[*] No credential changes recorded yet")
        print("[*] Run this script periodically to track changes")
        return

    changes = history["changes"]
    print(f"\n{'='*70}")
    print(f"Credential Change History ({len(changes)} changes recorded)")
    print(f"{'='*70}\n")

    for i, change in enumerate(changes, 1):
        timestamp = datetime.fromisoformat(change['timestamp'])
        print(f"{i}. {timestamp.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"   Host: {change['playlist_url']}")
        print(f"   Username: {change['username']}")
        print(f"   Password: {change['password']}")

        if i > 1:
            prev_timestamp = datetime.fromisoformat(changes[i-2]['timestamp'])
            time_diff = timestamp - prev_timestamp
            days = time_diff.days
            hours = time_diff.seconds // 3600
            print(f"   Time since last change: {days} days, {hours} hours")
        print()

    # Calculate average time between changes
    if len(changes) > 1:
        total_seconds = 0
        for i in range(1, len(changes)):
            curr = datetime.fromisoformat(changes[i]['timestamp'])
            prev = datetime.fromisoformat(changes[i-1]['timestamp'])
            total_seconds += (curr - prev).total_seconds()

        avg_seconds = total_seconds / (len(changes) - 1)
        avg_days = avg_seconds / (24 * 3600)
        avg_hours = (avg_seconds % (24 * 3600)) / 3600

        print(f"{'='*70}")
        print(f"Average time between changes: {int(avg_days)} days, {int(avg_hours)} hours")
        print(f"{'='*70}\n")

        # Recommend cron schedule
        recommend_schedule(avg_seconds)


def recommend_schedule(avg_seconds):
    """Recommend cron schedule based on change frequency"""
    avg_hours = avg_seconds / 3600

    print("\n📅 Recommended Cron Schedule:")
    print("-" * 70)

    if avg_hours < 6:
        print("⚠️  Credentials change VERY frequently (< 6 hours)")
        print("   Recommended: Every 2 hours")
        print("   Cron: 0 */2 * * *")
    elif avg_hours < 24:
        print("⏰ Credentials change frequently (< 1 day)")
        print("   Recommended: Every 4 hours")
        print("   Cron: 0 */4 * * *")
    elif avg_hours < 72:
        print("📆 Credentials change every few days")
        print("   Recommended: Every 6 hours")
        print("   Cron: 0 */6 * * *")
    elif avg_hours < 168:
        print("📅 Credentials change weekly")
        print("   Recommended: Twice daily")
        print("   Cron: 0 2,14 * * *")
    else:
        print("🗓️  Credentials change infrequently (> 1 week)")
        print("   Recommended: Once daily")
        print("   Cron: 0 3 * * *")

    print("-" * 70)


def main():
    print("="*70)
    print("Uzeen Credential Change Monitor")
    print("="*70)

    # Load current state
    state = load_state()
    if not state:
        return 1

    # Load history
    history = load_history()

    # Display current credentials
    print(f"\n📋 Current Credentials (as of {state.get('last_updated', 'unknown')})")
    print(f"-" * 70)
    print(f"Host:     {state['playlist_url']}")
    print(f"Username: {state['username']}")
    print(f"Password: {state['password']}")
    print(f"Playlist: {state['playlist_id']}")

    # Check if this is a new credential set
    is_new = True
    if history.get("changes"):
        last_change = history["changes"][-1]
        if (last_change['username'] == state['username'] and
            last_change['password'] == state['password'] and
            last_change['playlist_url'] == state['playlist_url']):
            is_new = False

    if is_new:
        # Record this credential change
        change_record = {
            "timestamp": state['last_updated'],
            "playlist_url": state['playlist_url'],
            "username": state['username'],
            "password": state['password']
        }
        history["changes"].append(change_record)
        save_history(history)
        print(f"\n✅ New credentials detected! Recorded in history.")
    else:
        print(f"\n✓ No changes since last check")

    # Analyze history
    analyze_history(history)

    # Show when to run this script again
    print("\n💡 Tips:")
    print("-" * 70)
    print("• Run this script periodically to track credential changes")
    print("• After collecting 3-5 change events, you'll have a good pattern")
    print("• Use the recommended cron schedule for uzeen_playlist_updater.py")
    print("• The updater script has change detection, so it's safe to run frequently")
    print("-" * 70)

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[!] Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
