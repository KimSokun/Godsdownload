#!/bin/bash
# ====================================================================
# Godsdownload Automated Storage & Disk Maintenance Script
# ====================================================================

PROJECT_DIR="/www/wwwroot/Godsdownload"
TEMP_DIR="${PROJECT_DIR}/temp_downloads"
LOG_DIR="/var/log/godsdownload"

mkdir -p "$LOG_DIR" "$TEMP_DIR" 2>/dev/null

# 1. Clean temporary downloads older than 30 minutes in temp_downloads
if [ -d "$TEMP_DIR" ]; then
    find "$TEMP_DIR" -type f -mmin +30 -delete 2>/dev/null
    find "$TEMP_DIR" -mindepth 1 -type d -empty -delete 2>/dev/null
fi

# 2. Clean temporary media and residue files in /tmp older than 30 minutes
find /tmp -maxdepth 1 -type f \( \
    -name "*.mp4" -o \
    -name "*.mp3" -o \
    -name "*.m4a" -o \
    -name "*.webm" -o \
    -name "*.mkv" -o \
    -name "*.jpg" -o \
    -name "*.jpeg" -o \
    -name "*.png" -o \
    -name "*.webp" -o \
    -name "*.aria2" -o \
    -name "*.tmp" -o \
    -name "*.part" -o \
    -name "*.ytdl" \
\) -mmin +30 -delete 2>/dev/null

# 3. Vacuum systemd journal to prevent runaway disk usage (limit to 100MB / 3 days)
journalctl --vacuum-size=100M --vacuum-time=3d >/dev/null 2>&1

# 4. Clean APT package archives cache
apt-get clean >/dev/null 2>&1

# 5. Truncate oversized cron logs in /www/server/cron/ if larger than 10MB
find /www/server/cron -name "*.log" -size +10M -exec truncate -s 1M {} \; 2>/dev/null

# 6. Truncate supervisor / application logs in /var/log/godsdownload/ if larger than 50MB
find "$LOG_DIR" -name "*.log" -size +50M -exec truncate -s 10M {} \; 2>/dev/null
