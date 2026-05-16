# Uzeen Playlist Expiration & Scheduling Guide

## 🔍 Investigation Results

### Server Status Check (2026-05-16)

**Current Credentials:**
- Host: `http://abd2022.xyz:80`
- Username: `34BWKUE`
- Password: `U1MZZ64`
- Last Updated: 2026-05-16 15:15:09

**API Accessibility:**
- ❌ Xtream API endpoints are **not accessible**
- ❌ Server is actively resetting connections (`Connection reset by peer`)
- ✅ M3U file is still serving the same credentials

### Why Can't We Check Expiration Directly?

The Xtream Codes server at `abd2022.xyz:80` is:
1. **Blocking API requests** - Standard Xtream API endpoints return connection reset errors
2. **Stream-only mode** - Configured to serve streams but not API queries
3. **Security measure** - Likely blocking automated API access to prevent abuse

This is common with reseller IPTV services that don't want users querying account info.

---

## 📊 Credential Change Monitoring

Since we can't query expiration directly, I've created a **monitoring system** to track when credentials change:

### New Tool: `check_uzeen_expiration.py`

**What it does:**
- Tracks credential changes over time
- Records timestamps when credentials rotate
- Calculates average time between changes
- Recommends optimal cron schedule based on patterns

**How to use:**
```bash
python3 check_uzeen_expiration.py
```

**Output example:**
```
Credential Change History (5 changes recorded)
1. 2026-05-16 15:15:09 - Username: 34BWKUE
2. 2026-05-23 14:30:22 - Username: 5CXWQRT
   Time since last change: 6 days, 23 hours
3. 2026-05-30 14:00:15 - Username: 7DHMNBV
   Time since last change: 7 days, 0 hours

Average time between changes: 7 days, 0 hours

📅 Recommended Cron Schedule:
   Credentials change weekly
   Recommended: Twice daily
   Cron: 0 2,14 * * *
```

---

## 🎯 Current Recommendation (Without Historical Data)

Since we just started monitoring and have no historical data yet, here's my **conservative recommendation**:

### **Start with: Every 6 Hours**

```bash
0 */6 * * * cd /home/hicha/projects/my_projects/zazy && python3 uzeen_playlist_updater.py >> logs/uzeen.log 2>&1
```

**Rationale:**
- ✅ Catches changes within reasonable time (6 hours max delay)
- ✅ Not too aggressive (4 checks per day)
- ✅ Script has change detection, so no-ops are cheap
- ✅ Good balance without knowing the rotation pattern

---

## 📈 Long-Term Strategy

### Phase 1: Data Collection (First 1-2 Weeks)

1. **Run the updater every 6 hours**
   ```bash
   crontab -e
   # Add:
   0 */6 * * * cd /home/hicha/projects/my_projects/zazy && python3 uzeen_playlist_updater.py >> logs/uzeen.log 2>&1
   ```

2. **Monitor credential changes**
   - Check logs weekly: `tail -100 logs/uzeen.log`
   - Look for "Changed fields" messages indicating credential rotation

3. **Run the expiration checker**
   ```bash
   # Run weekly to see if pattern emerges
   python3 check_uzeen_expiration.py
   ```

### Phase 2: Optimization (After Pattern Emerges)

Once you have 3-5 credential changes recorded, the monitoring script will show the pattern:

| Pattern | Recommended Schedule | Cron |
|---------|---------------------|------|
| Changes every few days | Every 6 hours | `0 */6 * * *` |
| Changes weekly | Twice daily | `0 2,14 * * *` |
| Changes monthly | Once daily | `0 3 * * *` |
| Changes very frequently | Every 3 hours | `0 */3 * * *` |

---

## 🔔 Setting Up Change Monitoring

To automatically track credential changes, add this to your cron:

```bash
# Update playlist every 6 hours
0 */6 * * * cd /home/hicha/projects/my_projects/zazy && python3 uzeen_playlist_updater.py >> logs/uzeen.log 2>&1

# Check and record changes daily
0 4 * * * cd /home/hicha/projects/my_projects/zazy && python3 check_uzeen_expiration.py >> logs/uzeen_monitor.log 2>&1
```

This will:
- Update the playlist every 6 hours
- Record any credential changes
- Build a history you can analyze

---

## 📝 Typical IPTV Credential Patterns

Based on common IPTV service practices:

### **Reseller Services (like uzeen.net)**
- Usually rotate credentials **weekly to monthly**
- Often rotate on a specific day/time (e.g., every Monday at 3 AM)
- May rotate when renewing subscriptions

### **Trial Accounts**
- Often expire after **24-48 hours**
- May rotate credentials daily

### **Premium Services**
- Rotate credentials **monthly or less frequently**
- Usually tied to billing cycles

**Uzeen.net appears to be a reseller service**, so I'd expect:
- **Weekly or monthly rotations**
- **Predictable schedule** (same day/time each week/month)

---

## 🎬 Action Plan

### Immediate (Today):

```bash
# 1. Set up the updater to run every 6 hours
crontab -e
# Add this line:
0 */6 * * * cd /home/hicha/projects/my_projects/zazy && python3 uzeen_playlist_updater.py >> logs/uzeen.log 2>&1

# 2. Create logs directory if needed
mkdir -p /home/hicha/projects/my_projects/zazy/logs

# 3. Run the first update manually
cd /home/hicha/projects/my_projects/zazy
python3 uzeen_playlist_updater.py
```

### Weekly (Next 2-4 Weeks):

```bash
# Check for credential changes
python3 check_uzeen_expiration.py

# Review logs
tail -50 logs/uzeen.log
```

### After Pattern Emerges:

```bash
# Adjust cron schedule based on recommendations from check_uzeen_expiration.py
# The script will tell you the optimal schedule
```

---

## 📊 Files Created

1. **`check_uzeen_expiration.py`** - Credential change monitoring tool
2. **`uzeen_credential_history.json`** - Auto-created history file
3. **`UZEEN_EXPIRATION_INFO.md`** - This guide

---

## ⚠️ Important Notes

1. **Can't predict exact expiration time**
   - The server blocks API queries
   - We can only detect changes after they happen

2. **Script is designed for this**
   - Change detection ensures no duplicate updates
   - Safe to run frequently
   - No-op if credentials unchanged

3. **Start conservative, optimize later**
   - Begin with 6-hour schedule
   - Collect data for 1-2 weeks
   - Adjust based on actual pattern

4. **Monitor logs regularly**
   - Check for "Changed fields" messages
   - Look for patterns in timing
   - Adjust schedule accordingly

---

## 🆘 If Credentials Expire Unexpectedly

If you notice the playlist stopped working:

1. **Run the updater immediately:**
   ```bash
   python3 uzeen_playlist_updater.py
   ```

2. **Check if credentials changed:**
   ```bash
   python3 check_uzeen_expiration.py
   ```

3. **Verify in IboPlayer:**
   - Log into https://iboplayer.com
   - Check playlist ID `6a08707dc7c4b3c03a03d600`
   - Test playback

4. **If still not working:**
   - Check if uzeen.net M3U URL is still valid
   - Verify your uzeen.net subscription is active

---

## 📌 Summary

**We can't check exact expiration time** because the Xtream server blocks API queries.

**Solution:** Monitor credential changes over time to learn the pattern.

**Recommendation:** Start with **every 6 hours**, collect data for 1-2 weeks, then optimize.

**Next Steps:**
1. ✅ Set up cron for every 6 hours
2. ✅ Run `check_uzeen_expiration.py` weekly
3. ✅ Adjust schedule after pattern emerges

The monitoring system will tell you the optimal schedule once it has enough data! 🎯
