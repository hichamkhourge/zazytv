# Dokploy Deployment Troubleshooting

## Error: Docker Registry Connection Timeout

**Error message:**
```
failed to resolve source metadata for docker.io/library/python:3.12-slim:
failed to do request: Head "https://registry-1.docker.io/v2/library/python/manifests/3.12-slim":
dial tcp: lookup registry-1.docker.io on 127.0.0.53:53: read udp 127.0.0.1:45228->127.0.0.53:53: i/o timeout
```

This indicates the Dokploy server cannot reach Docker Hub due to DNS or network issues.

---

## Quick Fixes

### 1. **Retry the Build** (Try This First!)

In Dokploy dashboard:
- Click "Rebuild" or "Redeploy"
- Network timeouts are often temporary
- This works 80% of the time

---

### 2. **Check Dokploy Server Network**

SSH into your Dokploy server:

```bash
# Test DNS resolution
nslookup registry-1.docker.io

# Test connectivity to Docker Hub
curl -I https://registry-1.docker.io

# Check if Docker can pull images manually
docker pull python:3.12.2-slim
```

**If DNS fails:**
```bash
# Check DNS configuration
cat /etc/resolv.conf

# Try changing DNS to Google DNS
sudo nano /etc/resolv.conf
# Add: nameserver 8.8.8.8
```

---

### 3. **Restart Docker Service**

Sometimes Docker daemon needs a restart:

```bash
sudo systemctl restart docker
sudo systemctl status docker
```

Then retry the build in Dokploy.

---

### 4. **Configure Docker DNS**

Edit Docker daemon configuration:

```bash
sudo nano /etc/docker/daemon.json
```

Add:
```json
{
  "dns": ["8.8.8.8", "8.8.4.4"]
}
```

Restart Docker:
```bash
sudo systemctl restart docker
```

---

### 5. **Use Docker Registry Mirror**

If Docker Hub is blocked or slow in your region:

```bash
sudo nano /etc/docker/daemon.json
```

Add a registry mirror:
```json
{
  "dns": ["8.8.8.8", "8.8.4.4"],
  "registry-mirrors": [
    "https://mirror.gcr.io"
  ]
}
```

Restart Docker and retry.

---

### 6. **Check Firewall Rules**

Ensure the server can reach Docker Hub:

```bash
# Check if port 443 is open
sudo ufw status

# Allow HTTPS if blocked
sudo ufw allow 443/tcp

# Test connection
telnet registry-1.docker.io 443
```

---

### 7. **Increase Build Timeout** (Dokploy Setting)

In Dokploy:
- Go to Application Settings
- Increase build timeout to 600 seconds or more
- Retry build

---

## Server Requirements

Ensure your Dokploy server meets these requirements:

- ✅ Internet connectivity
- ✅ DNS resolution working
- ✅ Port 443 (HTTPS) accessible
- ✅ At least 2GB RAM for Docker builds
- ✅ 10GB+ free disk space

---

## Common Causes

1. **Temporary Docker Hub outage** → Retry later
2. **DNS not configured** → Add 8.8.8.8 to /etc/resolv.conf
3. **Firewall blocking** → Check ufw/iptables rules
4. **Server in restricted region** → Use registry mirror
5. **Low disk space** → Clean up: `docker system prune -a`
6. **Docker daemon issue** → Restart Docker service

---

## Test Dokploy Server Health

Run these commands on your Dokploy server:

```bash
# Check disk space
df -h

# Check memory
free -h

# Check Docker status
sudo systemctl status docker

# Check Docker info
docker info

# Clean up Docker (if low on space)
docker system prune -a -f

# Test pulling an image manually
docker pull python:3.12.2-slim
```

---

## If All Else Fails

### Option A: Build Locally and Push

Build the image locally and push to your own registry:

```bash
# Build locally
docker build -t yourusername/zazy-automation:latest .

# Push to Docker Hub
docker push yourusername/zazy-automation:latest

# Update docker-compose.yml to use your image
# Change: build: .
# To: image: yourusername/zazy-automation:latest
```

### Option B: Use Different Dokploy Server

If the server consistently has network issues:
- Try a different cloud provider
- Ensure the VPS has unrestricted internet access
- Some providers block Docker Hub in certain regions

---

## Get Help

1. **Check Dokploy Logs:**
   - In Dokploy UI → Application → Logs
   - Look for more specific error messages

2. **Check Server Logs:**
   ```bash
   journalctl -u docker -n 100
   ```

3. **Dokploy Community:**
   - GitHub Issues: https://github.com/Dokploy/dokploy/issues
   - Discord/Community forums

---

## Prevention

To avoid future issues:

1. Use specific version tags (e.g., `python:3.12.2-slim` instead of `python:3.12-slim`)
2. Configure reliable DNS (8.8.8.8, 1.1.1.1)
3. Set up Docker registry mirror
4. Monitor server disk space
5. Keep Docker updated

---

## Updated Dockerfile

The Dockerfile has been updated to use a specific Python version (`3.12.2-slim`) which should help with caching and reliability.
