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
    IBOPLAYER_COOKIE             - IboPlayer authentication cookie
"""

import os
import sys
import json
import time
import requests
from datetime import datetime
from dotenv import load_dotenv
from urllib.parse import urlparse

# Load environment variables
load_dotenv()

# Configuration from environment variables
UZEEN_M3U_URL = os.getenv("UZEEN_M3U_URL")
IBOPLAYER_UZEEN_PLAYLIST_ID = os.getenv("IBOPLAYER_UZEEN_PLAYLIST_ID")
IBOPLAYER_COOKIE = os.getenv("IBOPLAYER_COOKIE")
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
    if not IBOPLAYER_COOKIE:
        missing.append("IBOPLAYER_COOKIE")

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


def fetch_first_channel(m3u_url):
    """
    Fetch the first Xtream Codes channel from an M3U file using streaming to handle large files.

    Skips custom API URLs (like uzeen.net's format) and finds the first proper Xtream Codes URL.

    Args:
        m3u_url: URL to the M3U file

    Returns:
        tuple: (extinf_line, stream_url) or (None, None) if failed
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
        print("[*] Searching for first Xtream Codes channel (skipping custom API URLs)...")

        extinf_line = None
        stream_url = None
        found_extm3u = False
        line_count = 0
        skipped_urls = 0

        # Process line by line
        for raw_line in response.iter_lines():
            line_count += 1

            # Show progress every 1000 lines
            if line_count % 1000 == 0:
                print(f"[*] Processed {line_count} lines, skipped {skipped_urls} non-Xtream URLs, still searching...")

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

            # Get the stream URL (first non-comment line after EXTINF)
            if extinf_line and not line.startswith("#"):
                # Check if this is a Xtream Codes URL
                if is_xtream_codes_url(line):
                    stream_url = line
                    print(f"[*] Found Xtream Codes URL after {line_count} lines (skipped {skipped_urls} non-Xtream URLs)")
                    print(f"[*] Channel: {extinf_line[:80]}...")
                    print(f"[*] Stream URL: {stream_url}")
                    break
                else:
                    # Skip this URL and continue searching
                    skipped_urls += 1
                    if skipped_urls <= 5:  # Show first 5 skipped URLs
                        print(f"[*] Skipping non-Xtream URL: {line[:60]}...")
                    extinf_line = None  # Reset to find next channel
                    continue

        if not found_extm3u:
            print("[!] Invalid M3U file: Missing #EXTM3U header")
            return (None, None)

        if not extinf_line or not stream_url:
            print(f"[!] Could not find Xtream Codes channel in M3U file")
            print(f"[!] Parsed {line_count} lines, skipped {skipped_urls} non-Xtream URLs")
            print(f"[!] Make sure the M3U file contains standard Xtream Codes URLs")
            return (None, None)

        print(f"[✓] Successfully found Xtream Codes channel in M3U file")
        return (extinf_line, stream_url)

    except requests.exceptions.RequestException as e:
        print(f"[!] Failed to fetch M3U file: {e}")
        return (None, None)
    except Exception as e:
        print(f"[!] Unexpected error while parsing M3U: {e}")
        import traceback
        traceback.print_exc()
        return (None, None)


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


def resolve_redirect_credentials(stream_url, original_credentials):
    """
    Probe the stream URL and follow HTTP redirects to discover the real Xtream server.

    The host found in the M3U file is often a load-balancer / front domain that redirects
    real stream requests to the actual Xtream server (which may use a different host and
    different path credentials). This probes the stream URL, follows the redirect, and
    re-parses the full credential from the final URL.

    Args:
        stream_url: The original stream URL from the M3U file
        original_credentials: Credentials extracted from the original (front) URL

    Returns:
        dict: Resolved credentials if a redirect to a different host was found,
              otherwise the original_credentials unchanged.
    """
    print(f"\n[*] Probing stream URL to detect redirect to real Xtream server...")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Connection": "keep-alive",
        "Accept": "*/*"
    }

    response = None
    try:
        # stream=True ensures only headers are read; we never iterate the body.
        response = requests.get(
            stream_url,
            stream=True,
            allow_redirects=True,
            timeout=30,
            headers=headers
        )

        if not response.history:
            print("[*] No redirect detected - keeping original credentials")
            return original_credentials

        print(f"[*] Followed {len(response.history)} redirect(s):")
        for i, resp in enumerate(response.history, 1):
            print(f"    {i}. {resp.status_code} -> {resp.url}")
        print(f"[*] Final URL: {response.url}")

        resolved = extract_credentials_from_url(response.url)
        if not resolved:
            print("[!] Could not parse credentials from redirected URL - keeping original")
            return original_credentials

        if resolved["host"] == original_credentials["host"]:
            print("[*] Redirect resolved to the same host - keeping original credentials")
            return original_credentials

        print(f"[✓] Resolved real Xtream server: {resolved['host']}")
        return resolved

    except requests.exceptions.RequestException as e:
        print(f"[!] Redirect probe failed ({e}) - keeping original credentials")
        return original_credentials
    except Exception as e:
        print(f"[!] Unexpected error during redirect probe ({e}) - keeping original credentials")
        return original_credentials
    finally:
        # Close without downloading the stream body.
        if response is not None:
            response.close()


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

    headers = {
        "Content-Type": "application/json",
        "Cookie": IBOPLAYER_COOKIE,
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }

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

    # Fetch first channel from M3U file
    extinf_line, stream_url = fetch_first_channel(UZEEN_M3U_URL)
    if not stream_url:
        print("[!] Failed to fetch first channel from M3U file")
        return 1

    # Extract credentials from stream URL
    credentials = extract_credentials_from_url(stream_url)
    if not credentials:
        print("[!] Failed to extract credentials from stream URL")
        return 1

    # Follow redirects on the stream URL to resolve the real Xtream server
    credentials = resolve_redirect_credentials(stream_url, credentials)

    # Load last known state
    last_state = load_last_state()

    # Check for changes
    changed, changed_fields = has_changes(last_state, credentials)

    if not changed:
        print("\n[✓] No changes detected - playlist is up to date!")
        return 0

    # Update iboplayer playlist
    if update_iboplayer_playlist(credentials):
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
