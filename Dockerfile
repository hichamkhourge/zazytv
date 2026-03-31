FROM python:3.12.2-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    cron \
    curl \
    unzip \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

# Install Google Chrome
RUN wget -q -O /tmp/google-chrome-key.pub https://dl-ssl.google.com/linux/linux_signing_key.pub \
    && gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg /tmp/google-chrome-key.pub \
    && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/* /tmp/google-chrome-key.pub

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY zazy_playlist_automation.py .
COPY .env.example .

# Create necessary directories
RUN mkdir -p /app/playlists /var/log

# Copy crontab file
COPY crontab /etc/cron.d/zazy-cron

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/zazy-cron

# Apply cron job
RUN crontab /etc/cron.d/zazy-cron

# Create the log file to be able to run tail
RUN touch /var/log/cron.log

# Copy entrypoint script
COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Volume for playlists
VOLUME ["/app/playlists"]

# Run the entrypoint script
ENTRYPOINT ["docker-entrypoint.sh"]

# Default command
CMD ["cron", "-f"]
