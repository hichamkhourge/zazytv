# Uzeen Playlist Updater

Automatically updates an IboPlayer playlist with credentials extracted from a Uzeen M3U file.

## Features

- **Streaming M3U Parsing**: Efficiently handles large M3U files by streaming instead of loading the entire file into memory
- **Automatic Credential Extraction**: Parses username and password from the first channel's stream URL
- **Change Detection**: Only updates the playlist if URL, username, or password have changed
- **State Tracking**: Maintains a local JSON file (`uzeen_playlist_state.json`) to track the last known state
- **Retry Logic**: Automatically retries failed API requests with exponential backoff
- **Redirect Following**: Handles HTTP to HTTPS redirects automatically

## How It Works

1. **Fetch M3U File**: Downloads the M3U file from the configured URL (streaming mode for efficiency)
2. **Smart URL Detection**:
   - Skips custom API URLs (like `https://www.uzeen.net/api/stream/...`)
   - Searches for first proper Xtream Codes URL in the file
3. **Extract Credentials**: Parses the Xtream stream URL to extract:
   - Host (e.g., `http://abd2022.xyz:80`)
   - Username (e.g., `34BWKUE`)
   - Password (e.g., `U1MZZ64`)
4. **Check for Changes**: Compares new credentials with the last known state
5. **Update Playlist**: If changed, updates the IboPlayer playlist via API
6. **Save State**: Stores the new state for future comparisons

## Supported Stream URL Formats

The script recognizes and extracts credentials from standard Xtream Codes URL patterns:

```
http://host:port/username/password/channel_id.ts
http://host:port/live/username/password/channel_id.ts
http://host:port/movie/username/password/movie_id.mp4
http://host:port/series/username/password/series_id.ext
```

**Note**: The uzeen.net M3U file contains both custom API URLs and standard Xtream URLs. The script automatically skips the custom format and finds the Xtream Codes credentials.

## Configuration

### Environment Variables

Add these to your `.env` file:

```bash
# Uzeen M3U File URL
UZEEN_M3U_URL=http://uzeen.net/api/m3u/YOUR_TOKEN_HERE

# IboPlayer Playlist Configuration
IBOPLAYER_UZEEN_PLAYLIST_ID=your_playlist_id_here
IBOPLAYER_UZEEN_PLAYLIST_NAME=Uzeen  # Optional, defaults to "Uzeen"

# IboPlayer Authentication (shared with other scripts)
IBOPLAYER_COOKIE=your_iboplayer_cookie_here
```

### Getting Your IboPlayer Cookie

1. Log into https://iboplayer.com in your browser
2. Open Developer Tools (F12)
3. Go to Application tab > Cookies > https://iboplayer.com
4. Copy the entire cookie string
5. Add it to your `.env` file as `IBOPLAYER_COOKIE`

### Getting Your Playlist ID

1. Log into IboPlayer web interface
2. Navigate to your playlist settings
3. The playlist ID is in the URL or can be found in the playlist configuration
4. Add it to your `.env` file as `IBOPLAYER_UZEEN_PLAYLIST_ID`

## Usage

### Basic Usage

```bash
python3 uzeen_playlist_updater.py
```

### Example Output

```
======================================================================
Uzeen Playlist Updater
======================================================================

[*] Fetching M3U file from: http://uzeen.net/api/m3u/D67hZ8SegV
[*] Requesting M3U file...
[*] Note: Large files may take several minutes to start downloading...
[*] Waiting for server response (timeout: 5 minutes)...
[*] Followed 2 redirect(s):
    1. 308 -> http://uzeen.net/api/m3u/D67hZ8SegV
    2. 307 -> https://uzeen.net/api/m3u/D67hZ8SegV
[*] Final URL: https://www.uzeen.net/api/m3u/D67hZ8SegV
[✓] Connected! Starting to parse M3U file (streaming mode)...
[*] Searching for first Xtream Codes channel (skipping custom API URLs)...
[*] Found M3U header
[*] Skipping non-Xtream URL: https://www.uzeen.net/api/stream/D67hZ8SegV/682950...
[*] Skipping non-Xtream URL: https://www.uzeen.net/api/stream/D67hZ8SegV/1123275...
[*] Skipping non-Xtream URL: https://www.uzeen.net/api/stream/D67hZ8SegV/1123274...
[*] Skipping non-Xtream URL: https://www.uzeen.net/api/stream/D67hZ8SegV/1039934...
[*] Skipping non-Xtream URL: https://www.uzeen.net/api/stream/D67hZ8SegV/1039920...
[*] Processed 1000 lines, skipped 497 non-Xtream URLs, still searching...
[*] Found Xtream Codes URL after 1580 lines (skipped 786 non-Xtream URLs)
[*] Channel: #EXTINF:-1,FR - Los Tigres - 2025 [VOSTFR]...
[*] Stream URL: http://abd2022.xyz:80/movie/34BWKUE/U1MZZ64/1414242.mp4
[✓] Successfully found Xtream Codes channel in M3U file

[*] Extracting credentials from URL: http://abd2022.xyz:80/movie/34BWKUE/U1MZZ64/1414242.mp4
[*] Host: http://abd2022.xyz:80
[*] Detected 'movie' category in URL
[*] Username: 34BWKUE
[*] Password: U1MZZ64
[*] No previous state - will update playlist

[*] Updating IboPlayer playlist...
[*] API URL: https://iboplayer.com/frontend/device/savePlaylist
[*] Playlist ID: 6a08707dc7c4b3c03a03d600
[*] Playlist Name: Uzeen
[*] Playlist URL: http://abd2022.xyz:80
[*] Username: 34BWKUE
[*] Password: U1MZZ64

[*] Attempt 1/3: Sending request to IboPlayer API...
[*] Response status code: 200
[✓] Playlist updated successfully!
[*] Response: {
  "status": "success",
  "msg": "Playlist saved successfully",
  ...
}
[✓] Saved state to uzeen_playlist_state.json

[✓] Playlist updated and state saved successfully!
[*] Changed fields: initial_setup
```

### When No Changes Detected

```
[*] No changes detected
[✓] No changes detected - playlist is up to date!
```

## Troubleshooting

### Timeout Issues

If you encounter timeout errors like:

```
[!] Failed to fetch M3U file: HTTPSConnectionPool(host='www.uzeen.net', port=443): Read timed out.
```

**Note**: The script already has a **5-minute timeout** (300 seconds) to handle large M3U files that take time to start downloading.

**Possible causes:**
1. **Very Large File**: The uzeen.net M3U file is extremely large and may take several minutes to begin streaming
2. **Slow Server**: The server may be experiencing high load or slow response times
3. **Network Issues**: Your network connection may be slow or unstable
4. **Server Down**: The server may be temporarily unavailable

**Solutions:**
1. **Wait and Retry**: The file is very large. Try running the script again - it should work once the server responds
2. **Increase Timeout Further**: If 5 minutes isn't enough, edit line 127 in the script:
   ```python
   response = requests.get(m3u_url, stream=True, timeout=600, headers=headers, allow_redirects=True)
   ```
3. **Check Server Status**: Verify that uzeen.net is accessible in your browser
4. **Monitor Progress**: The script shows progress every 1000 lines, so you'll see it working as it searches for Xtream URLs

### No Xtream Codes URLs Found

If you see this error:

```
[!] Could not find Xtream Codes channel in M3U file
[!] Make sure the M3U file contains standard Xtream Codes URLs
```

**This means:**
- The script processed the entire M3U file
- All URLs were custom API format (like `https://www.uzeen.net/api/stream/...`)
- No standard Xtream Codes URLs were found

**Solutions:**
1. Verify the M3U URL is correct and points to a file with Xtream credentials
2. Check if the M3U provider changed their format
3. Manually inspect a few URLs from the M3U file to see their format

### Invalid Credentials Extracted

If the credentials look wrong:
1. The script automatically detects `live`, `movie`, and `series` prefixes in URLs
2. Check the state file (`uzeen_playlist_state.json`) to see what was extracted
3. Verify the extracted credentials work by testing them in IboPlayer
4. The script only looks for standard Xtream Codes patterns

### IboPlayer API Errors

If you see `Client error (4xx)`:
- Check that `IBOPLAYER_COOKIE` is valid (cookies expire after some time)
- Verify `IBOPLAYER_UZEEN_PLAYLIST_ID` is correct
- Ensure you're logged into IboPlayer and the cookie is up-to-date

## State File

The script maintains a state file (`uzeen_playlist_state.json`) to track changes:

```json
{
  "playlist_url": "http://abd2022.xyz:80",
  "username": "34BWKUE",
  "password": "U1MZZ64",
  "last_updated": "2026-05-16T15:07:55.140354",
  "playlist_id": "6a08707dc7c4b3c03a03d600"
}
```

**What's stored:**
- `playlist_url`: The Xtream Codes server host
- `username`: Extracted from the stream URL
- `password`: Extracted from the stream URL
- `last_updated`: ISO timestamp of last update
- `playlist_id`: IboPlayer playlist ID that was updated

To force an update even if nothing changed, delete this file:

```bash
rm uzeen_playlist_state.json
```

## Automation

### Cron Job (Linux/macOS)

Run the script every 6 hours:

```bash
0 */6 * * * cd /path/to/zazy && /usr/bin/python3 uzeen_playlist_updater.py >> logs/uzeen_updater.log 2>&1
```

### Task Scheduler (Windows)

Create a scheduled task to run the script daily or as needed.

### Docker

You can also run this in Docker alongside the other automation scripts. The script will work in headless environments since it doesn't require a browser.

## Testing

The script was successfully tested with:
- ✅ Large M3U files with 10,000+ lines (streaming mode)
- ✅ HTTP to HTTPS redirects (uzeen.net → www.uzeen.net)
- ✅ Multiple URL redirect chains (308 → 307 redirects)
- ✅ Mixed URL formats (custom API + Xtream Codes)
- ✅ Smart URL detection (skipping 786 non-Xtream URLs)
- ✅ Xtream Codes URL formats (live, movie, series prefixes)
- ✅ Change detection logic with local state file
- ✅ IboPlayer API integration and authentication
- ✅ Retry logic with exponential backoff
- ✅ 5-minute timeout for slow servers

**Test Results:**
- M3U file: http://uzeen.net/api/m3u/D67hZ8SegV
- Lines processed: ~1580 lines to find first Xtream URL
- URLs skipped: 786 custom uzeen.net API URLs
- Credentials found: `34BWKUE` / `U1MZZ64` from `http://abd2022.xyz:80`
- IboPlayer update: ✅ Success

## Files Created

- `uzeen_playlist_updater.py` - Main script
- `uzeen_playlist_state.json` - State file (auto-created on first run)
- `.env` - Configuration file (must be created manually)

## Security Notes

- Never commit your `.env` file to version control
- Keep your `IBOPLAYER_COOKIE` secret
- The state file contains credentials in plain text - protect it appropriately
- Consider using environment-specific `.env` files for development vs production

## Integration with Other Scripts

This script follows the same patterns as other automation scripts in this project:
- Uses `.env` for configuration
- Follows the same logging format
- Can be integrated with Telegram notifications (future enhancement)
- Can be added to the Flask API server as an endpoint (future enhancement)

## License

Part of the Zazy IPTV Automation project.
