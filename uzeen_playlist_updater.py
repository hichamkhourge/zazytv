#!/usr/bin/env python3
"""
Uzeen Playlist Updater

This script reads an M3U file from Uzeen, extracts credentials from the first channel,
checks for changes, and updates the iboplayer playlist if necessary.

Usage:
    python uzeen_playlist_updater.py

Environment Variables:
    UZEEN_M3U_URL                - URL to the Uzeen M3U file
    IBOPLAYER_UZEEN_PLAYLIST_ID  - IboPlayer playlist ID to update
    IBOPLAYER_MAC_ADDRESS        - IboPlayer device MAC address (for device login)
    IBOPLAYER_DEVICE_KEY         - IboPlayer device key (for device login)
    TWOCAPTCHA_API_KEY           - 2captcha key used to solve the login captcha
    IBOPLAYER_COOKIE             - (optional/legacy) sent alongside the bearer token
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlparse, urljoin

try:
    from telegram_notifier import send_notification
except Exception:
    # Telegram notifier is optional; degrade gracefully if unavailable.
    def send_notification(status, message, details=None):
        return False

import iboplayer_auth

# Load environment variables
load_dotenv()

# Configuration from environment variables
UZEEN_M3U_URL = os.getenv("UZEEN_M3U_URL")
IBOPLAYER_UZEEN_PLAYLIST_ID = os.getenv("IBOPLAYER_UZEEN_PLAYLIST_ID")

# Device-login credentials (replace the old static IBOPLAYER_COOKIE auth).
IBOPLAYER_MAC_ADDRESS = os.getenv("IBOPLAYER_MAC_ADDRESS")
IBOPLAYER_DEVICE_KEY = os.getenv("IBOPLAYER_DEVICE_KEY")
TWOCAPTCHA_API_KEY = os.getenv("TWOCAPTCHA_API_KEY")

IBOPLAYER_PLAYLIST_NAME = os.getenv("IBOPLAYER_UZEEN_PLAYLIST_NAME", "Uzeen")

# State file for tracking changes
STATE_FILE = "uzeen_playlist_state.json"

# IboPlayer API configuration
IBOPLAYER_API_URL = "https://iboplayer.com/frontend/device/savePlaylist"


def validate_config():
    """Validate required environment variables are set."""
    missing = []

    if not UZEEN_M3U_URL:
        missing.append("UZEEN_M3U_URL")
    if not IBOPLAYER_UZEEN_PLAYLIST_ID:
        missing.append("IBOPLAYER_UZEEN_PLAYLIST_ID")
    if not IBOPLAYER_MAC_ADDRESS:
        missing.append("IBOPLAYER_MAC_ADDRESS")
    if not IBOPLAYER_DEVICE_KEY:
        missing.append("IBOPLAYER_DEVICE_KEY")
    if not TWOCAPTCHA_API_KEY:
        missing.append("TWOCAPTCHA_API_KEY")

    if missing:
        print("[!] Missing required environment variables:")
        for var in missing:
            print(f"    - {var}")
        print("\n[!] Please configure these variables in your .env file")
        return False

    return True


def is_xtream_codes_url(url):
    """
    Check if a URL follows the Xtream Codes format.

    Xtream Codes URLs typically have username/password in the path:
    - http://host:port/username/password/channel.ext
    - http://host:port/live/username/password/channel.ext
    - http://host:port/series/username/password/series_id.ext

    Args:
        url: The stream URL to check

    Returns:
        bool: True if it looks like a Xtream Codes URL
    """
    # Skip uzeen.net custom API URLs
    if 'uzeen.net/api/' in url:
        return False

    # Parse the URL path
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split('/') if p]

    # Xtream URLs should have at least 3 path parts after the host
    # e.g., /username/password/file or /live/username/password/file
    if len(path_parts) >= 3:
        # Check if it has typical Xtream patterns
        if path_parts[0] in ['live', 'movie', 'series']:
            return len(path_parts) >= 4  # /live/user/pass/file
        else:
            return True  # /user/pass/file

    return False


def iter_channel_urls(m3u_url):
    """
    Stream an M3U file and yield channel stream URLs one at a time.

    Yields the stream URL following each #EXTINF entry, regardless of format. For the Uzeen
    M3U these are proxy URLs (e.g. https://www.uzeen.net/api/stream/<token>/<id>) that
    redirect to the real Xtream server; redirect resolution happens in
    resolve_xtream_credentials(). Yielding lazily lets the caller stop as soon as a channel
    resolves to credentials, without downloading the whole (potentially huge) M3U or capping
    the number of channels tried.

    Args:
        m3u_url: URL to the M3U file

    Yields:
        str: Each channel stream URL, in M3U order.
    """
    print(f"\n[*] Fetching M3U file from: {m3u_url}")

    try:
        # Stream the response to handle large files efficiently
        # Add headers to help with compatibility and connection optimization
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Connection": "keep-alive",
            "Accept": "*/*"
        }

        print(f"[*] Requesting M3U file...")
        print(f"[*] Note: Large files may take several minutes to start downloading...")
        print(f"[*] Waiting for server response (timeout: 5 minutes)...")

        # Increased timeout to 300 seconds (5 minutes) for very large M3U files
        response = requests.get(m3u_url, stream=True, timeout=300, headers=headers, allow_redirects=True)

        # Check if there were redirects
        if response.history:
            print(f"[*] Followed {len(response.history)} redirect(s):")
            for i, resp in enumerate(response.history, 1):
                print(f"    {i}. {resp.status_code} -> {resp.url}")
            print(f"[*] Final URL: {response.url}")

        response.raise_for_status()

        print("[✓] Connected! Starting to parse M3U file (streaming mode)...")
        print("[*] Yielding channel stream URLs until one resolves...")

        extinf_line = None
        found_extm3u = False
        line_count = 0
        channel_count = 0

        # Process line by line
        for raw_line in response.iter_lines():
            line_count += 1

            # Show progress every 1000 lines
            if line_count % 1000 == 0:
                print(f"[*] Processed {line_count} lines, still searching...")

            if not raw_line:
                continue

            # Decode bytes to string if needed
            if isinstance(raw_line, bytes):
                line = raw_line.decode('utf-8', errors='ignore').strip()
            else:
                line = raw_line.strip()

            # Skip the #EXTM3U header
            if line.startswith("#EXTM3U"):
                found_extm3u = True
                print("[*] Found M3U header")
                continue

            # Look for channel entry
            if line.startswith("#EXTINF"):
                extinf_line = line
                continue

            # Yield the stream URL (first non-comment line after EXTINF), any format.
            # Redirect resolution to the real Xtream server happens in
            # resolve_xtream_credentials().
            if extinf_line and not line.startswith("#"):
                channel_count += 1
                print(f"[*] Channel {channel_count}: {line}")
                extinf_line = None  # Reset to pair the next #EXTINF
                yield line

        if not found_extm3u:
            print("[!] Invalid M3U file: Missing #EXTM3U header")
        elif channel_count == 0:
            print(f"[!] Could not find any channel stream URL in M3U file")
            print(f"[!] Parsed {line_count} lines")

    except requests.exceptions.RequestException as e:
        print(f"[!] Failed to fetch M3U file: {e}")
    except Exception as e:
        print(f"[!] Unexpected error while parsing M3U: {e}")
        import traceback
        traceback.print_exc()


def extract_credentials_from_url(stream_url):
    """
    Extract host, username, and password from a stream URL.

    Expected formats:
        - http://host:port/username/password/channel_id.ts
        - http://host:port/live/username/password/channel_id.ts
        - http://host/username/password/channel_id

    Args:
        stream_url: The stream URL from the M3U file

    Returns:
        dict: {host, username, password, playlist_url} or None if failed
    """
    print(f"\n[*] Extracting credentials from URL: {stream_url}")

    try:
        parsed = urlparse(stream_url)

        if not parsed.scheme or not parsed.netloc:
            print("[!] Invalid URL format")
            return None

        # Construct base host URL
        host_url = f"{parsed.scheme}://{parsed.netloc}"
        print(f"[*] Host: {host_url}")

        # Parse the path to extract username and password
        path_parts = [p for p in parsed.path.split('/') if p]

        if len(path_parts) < 2:
            print(f"[!] URL path too short: {parsed.path}")
            print("[!] Expected format: /username/password/... or /live/username/password/...")
            return None

        # Handle different URL patterns
        username = None
        password = None

        # Pattern 1: /live|movie|series/username/password/... (has category prefix)
        if path_parts[0].lower() in ['live', 'movie', 'series'] and len(path_parts) >= 3:
            username = path_parts[1]
            password = path_parts[2]
            print(f"[*] Detected '{path_parts[0]}' category in URL")

        # Pattern 2: /username/password/... (no prefix)
        elif len(path_parts) >= 2:
            username = path_parts[0]
            password = path_parts[1]

        if not username or not password:
            print("[!] Could not extract username/password from URL path")
            return None

        print(f"[*] Username: {username}")
        print(f"[*] Password: {password}")

        # Construct the playlist URL (base host for Xtream Codes)
        playlist_url = host_url

        return {
            "host": host_url,
            "username": username,
            "password": password,
            "playlist_url": playlist_url
        }

    except Exception as e:
        print(f"[!] Failed to parse credentials from URL: {e}")
        return None


def resolve_xtream_credentials(channel_url, max_hops=5):
    """
    Resolve the real Xtream credentials by following a channel URL's redirect chain.

    The Uzeen M3U contains proxy URLs (e.g. https://www.uzeen.net/api/stream/<token>/<id>)
    that respond with a 302 redirect to the real Xtream server, which carries the
    credentials in its path (e.g. http://abd2022.xyz/USERNAME/PASSWORD/<id>). This follows
    the redirect(s) and parses the Xtream credentials from the target.

    If the channel URL is already an Xtream Codes URL, it is parsed directly with no
    network request (backward compatible with M3Us that carry direct Xtream URLs).

    Args:
        channel_url: The first channel stream URL from the M3U file
        max_hops: Maximum number of redirects to follow

    Returns:
        dict: {host, username, password, playlist_url} or None if it could not be resolved.
    """
    print(f"\n[*] Resolving Xtream credentials from channel URL: {channel_url}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Connection": "keep-alive",
        "Accept": "*/*"
    }

    current = channel_url
    try:
        for hop in range(max_hops):
            # Stop as soon as we reach a URL that carries Xtream credentials in its path.
            if is_xtream_codes_url(current):
                print(f"[✓] Reached Xtream Codes URL: {current}")
                return extract_credentials_from_url(current)

            # allow_redirects=False so we read only the Location header and never stream
            # from the real server. stream=True + close() avoids downloading any body.
            response = requests.get(
                current,
                stream=True,
                allow_redirects=False,
                timeout=30,
                headers=headers
            )
            status = response.status_code
            location = response.headers.get("Location")
            response.close()

            if status in (301, 302, 303, 307, 308) and location:
                next_url = urljoin(current, location)
                print(f"[*] Hop {hop + 1}: {status} -> {next_url}")
                current = next_url
                continue

            print(f"[!] No redirect to follow (status {status}) - could not reach an Xtream URL")
            return None

        print(f"[!] Exceeded max redirects ({max_hops}) without reaching an Xtream URL")
        return None

    except requests.exceptions.RequestException as e:
        print(f"[!] Failed to resolve Xtream credentials ({e})")
        return None
    except Exception as e:
        print(f"[!] Unexpected error while resolving Xtream credentials ({e})")
        return None


def load_last_state():
    """
    Load the last known playlist state from JSON file.

    Returns:
        dict: Last known state or None if file doesn't exist
    """
    if not os.path.exists(STATE_FILE):
        print(f"[*] No previous state file found ({STATE_FILE})")
        return None

    try:
        with open(STATE_FILE, 'r') as f:
            state = json.load(f)
        print(f"[*] Loaded previous state from {STATE_FILE}")
        return state
    except Exception as e:
        print(f"[!] Failed to load state file: {e}")
        return None


def save_state(credentials):
    """
    Save the current playlist state to JSON file.

    Args:
        credentials: Dictionary with host, username, password, playlist_url
    """
    try:
        state = {
            "playlist_url": credentials["playlist_url"],
            "username": credentials["username"],
            "password": credentials["password"],
            "last_updated": datetime.now().isoformat(),
            "playlist_id": IBOPLAYER_UZEEN_PLAYLIST_ID
        }

        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2)

        print(f"[✓] Saved state to {STATE_FILE}")
        return True
    except Exception as e:
        print(f"[!] Failed to save state file: {e}")
        return False


def has_changes(last_state, new_credentials):
    """
    Check if playlist data has changed.

    Args:
        last_state: Previous state dictionary
        new_credentials: New credentials dictionary

    Returns:
        tuple: (has_changes: bool, changed_fields: list)
    """
    if not last_state:
        print("[*] No previous state - will update playlist")
        return (True, ["initial_setup"])

    changed = []

    if last_state.get("playlist_url") != new_credentials["playlist_url"]:
        changed.append("playlist_url")

    if last_state.get("username") != new_credentials["username"]:
        changed.append("username")

    if last_state.get("password") != new_credentials["password"]:
        changed.append("password")

    if changed:
        print(f"[*] Changes detected in: {', '.join(changed)}")
        return (True, changed)
    else:
        print("[*] No changes detected")
        return (False, [])


def update_iboplayer_playlist(credentials, max_retries=3):
    """
    Update the iboplayer playlist with new credentials.

    Args:
        credentials: Dictionary with username, password, playlist_url
        max_retries: Maximum number of retry attempts

    Returns:
        bool: True if successful, False otherwise
    """
    print("\n[*] Updating IboPlayer playlist...")

    # Authenticate with a device-login bearer token (obtained/cached by
    # iboplayer_auth). A 401/403 below triggers a token refresh + one retry.
    try:
        headers = iboplayer_auth.authed_headers()
    except Exception as e:
        print(f"[!] Could not obtain IboPlayer bearer token: {e}")
        return False

    payload = {
        "current_playlist_url_id": IBOPLAYER_UZEEN_PLAYLIST_ID,
        "password": credentials["password"],
        "pin": "",
        "playlist_name": IBOPLAYER_PLAYLIST_NAME,
        "playlist_type": "xc",
        "playlist_url": credentials["playlist_url"],
        "protect": "false",
        "username": credentials["username"],
        "xml_url": ""
    }

    print(f"[*] API URL: {IBOPLAYER_API_URL}")
    print(f"[*] Playlist ID: {IBOPLAYER_UZEEN_PLAYLIST_ID}")
    print(f"[*] Playlist Name: {IBOPLAYER_PLAYLIST_NAME}")
    print(f"[*] Playlist URL: {credentials['playlist_url']}")
    print(f"[*] Username: {credentials['username']}")
    print(f"[*] Password: {credentials['password']}")

    # Retry loop with exponential backoff
    relogin_attempted = False
    for attempt in range(1, max_retries + 1):
        try:
            print(f"\n[*] Attempt {attempt}/{max_retries}: Sending request to IboPlayer API...")

            response = requests.post(
                IBOPLAYER_API_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            print(f"[*] Response status code: {response.status_code}")

            if response.status_code == 200:
                print("[✓] Playlist updated successfully!")
                try:
                    response_data = response.json()
                    print(f"[*] Response: {json.dumps(response_data, indent=2)}")
                except:
                    print(f"[*] Response text: {response.text}")
                return True

            elif response.status_code in (401, 403):
                # Expired/invalid bearer token - refresh it once and retry.
                print(f"[!] Auth rejected ({response.status_code}): {response.text}")
                if not relogin_attempted:
                    relogin_attempted = True
                    print("[*] Refreshing bearer token and retrying...")
                    iboplayer_auth.clear_cached_token()
                    try:
                        headers = iboplayer_auth.authed_headers(force_refresh=True)
                    except Exception as e:
                        print(f"[!] Re-login failed: {e}")
                        return False
                    continue
                print("[!] Re-login already attempted - giving up.")
                return False

            elif response.status_code >= 400 and response.status_code < 500:
                # Client error - don't retry
                print(f"[!] Client error ({response.status_code}): {response.text}")
                print("[!] This is likely a configuration issue. Please check your IboPlayer settings.")
                return False

            else:
                # Server error - retry
                print(f"[!] Server error ({response.status_code}): {response.text}")

                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    print(f"[*] Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print("[!] Max retries reached. Could not update playlist.")
                    return False

        except requests.exceptions.Timeout:
            print(f"[!] Request timed out (attempt {attempt}/{max_retries})")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("[!] Max retries reached. Request timed out.")
                return False

        except requests.exceptions.RequestException as e:
            print(f"[!] Request failed (attempt {attempt}/{max_retries}): {e}")
            if attempt < max_retries:
                wait_time = 2 ** attempt
                print(f"[*] Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                print("[!] Max retries reached. Request failed.")
                return False

        except Exception as e:
            print(f"[!] Unexpected error: {e}")
            import traceback
            traceback.print_exc()
            return False

    return False


def main():
    """Main execution flow."""
    print("=" * 70)
    print("Uzeen Playlist Updater")
    print("=" * 70)

    # Validate configuration
    if not validate_config():
        return 1

    # Stream channel URLs from the M3U and follow each one's redirect until one resolves
    # to Xtream credentials. This keeps trying (no limit) and falls back to the next
    # channel whenever an entry does not redirect (e.g. a direct VOD file with no
    # credentials), stopping as soon as a good URL is found.
    credentials = None
    for i, channel_url in enumerate(iter_channel_urls(UZEEN_M3U_URL), 1):
        print(f"\n[*] Trying channel {i}...")
        credentials = resolve_xtream_credentials(channel_url)
        if credentials:
            break
        print(f"[!] Channel {i} did not resolve to Xtream credentials - trying next...")

    if not credentials:
        print("[!] Failed to resolve Xtream credentials from any channel in the M3U")
        return 1

    # Load last known state
    last_state = load_last_state()

    # Check for changes
    changed, changed_fields = has_changes(last_state, credentials)

    if not changed:
        print("\n[✓] No changes detected - playlist is up to date!")
        return 0

    # Update iboplayer playlist
    if update_iboplayer_playlist(credentials):
        # Notify Telegram that the playlist was updated successfully
        details = (
            f"Host: {credentials['host']}\n"
            f"Username: {credentials['username']}\n"
            f"Password: {credentials['password']}\n"
            f"Changed: {', '.join(changed_fields)}"
        )
        send_notification(
            "✅ SUCCESS",
            "Uzeen playlist updated on IboPlayer with new Xtream credentials.",
            details,
        )

        # Save new state
        if save_state(credentials):
            print("\n[✓] Playlist updated and state saved successfully!")
            print(f"[*] Changed fields: {', '.join(changed_fields)}")
            return 0
        else:
            print("\n[!] Playlist updated but failed to save state")
            return 1
    else:
        print("\n[!] Failed to update playlist")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n[!] Interrupted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n[!] Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
