#!/bin/bash
set -e

# ==============================================================================
# ⚡ GODSDOWNLOAD - ALL-IN-ONE SUPERVISOR AUTOMATION SCRIPT FOR UBUNTU ⚡
# ==============================================================================
# Usage:
#   ./deploy.sh          - Full setup, install dependencies & start via supervisor
#   ./deploy.sh update   - Pull latest git code, update yt-dlp & restart bot
#   ./deploy.sh restart  - Restart the bot via supervisor
#   ./deploy.sh status   - Check bot running status
#   ./deploy.sh logs     - View real-time bot output logs
#   ./deploy.sh stop     - Stop the bot service
# ==============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

APP_NAME="godsdownload"
if [ -d "/etc/supervisor/conf.d" ]; then
    SUPERVISOR_CONF_DIR="/etc/supervisor/conf.d"
elif [ -d "/etc/supervisord.d" ]; then
    SUPERVISOR_CONF_DIR="/etc/supervisord.d"
else
    SUPERVISOR_CONF_DIR="/etc/supervisor/conf.d"
    sudo mkdir -p "$SUPERVISOR_CONF_DIR" 2>/dev/null || true
fi
SUPERVISOR_CONF="${SUPERVISOR_CONF_DIR}/${APP_NAME}.conf"
LOG_DIR="/var/log/godsdownload"
CURRENT_USER=$(whoami)

ACTION="${1:-deploy}"

case "$ACTION" in
    status)
        echo "📊 Checking $APP_NAME status..."
        sudo supervisorctl status "$APP_NAME" || true
        exit 0
        ;;
    restart)
        echo "🔄 Preparing and restarting $APP_NAME..."
        sudo mkdir -p "$LOG_DIR" temp_downloads data
        sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$SCRIPT_DIR" 2>/dev/null || true
        sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$LOG_DIR" 2>/dev/null || true
        sudo rm -f godsdownload.lock test.lock /etc/supervisor/conf.d/${APP_NAME}.ini 2>/dev/null || true
        sudo ufw allow 8080/tcp 2>/dev/null || true
        sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true
        
        # Ensure virtual environment exists
        if [ ! -f "${SCRIPT_DIR}/.venv/bin/python3" ]; then
            echo "📦 Setting up Python virtual environment (.venv)..."
            python3 -m venv .venv
            .venv/bin/pip install --upgrade pip -q
            .venv/bin/pip install -r requirements.txt -q
        fi

        # Make sure supervisor configuration is valid
        sudo supervisorctl reread || true
        sudo supervisorctl update || true
        
        echo "🔄 Starting $APP_NAME via supervisor..."
        sudo supervisorctl restart "$APP_NAME" 2>/dev/null || sudo supervisorctl start "$APP_NAME" || true
        sleep 1
        sudo supervisorctl status "$APP_NAME"
        exit 0
        ;;
    stop)
        echo "🛑 Stopping $APP_NAME via supervisor..."
        sudo supervisorctl stop "$APP_NAME"
        echo "✅ Bot stopped."
        exit 0
        ;;
    logs)
        echo "📜 Streaming live logs for $APP_NAME (Ctrl+C to exit)..."
        if [ -f "${LOG_DIR}/stdout.log" ]; then
            tail -f "${LOG_DIR}/stdout.log" "${LOG_DIR}/stderr.log"
        else
            sudo supervisorctl tail -f "$APP_NAME"
        fi
        exit 0
        ;;
    cron)
        echo "⏰ Checking /etc/cron.d/godsdownload-autoupgrade..."
        if [ -f "/etc/cron.d/godsdownload-autoupgrade" ]; then
            cat /etc/cron.d/godsdownload-autoupgrade
        else
            echo "⚠️ Cron not found. Run './deploy.sh' to automatically install it."
        fi
        exit 0
        ;;
    fix-owner|set-owner|permission|permissions)
        TARGET_USER="${2:-$CURRENT_USER}"
        echo "👤 Fixing directory ownership to '$TARGET_USER'..."
        sudo chown -R "$TARGET_USER:$TARGET_USER" "$SCRIPT_DIR" 2>/dev/null || true
        sudo chown -R "$TARGET_USER:$TARGET_USER" "$LOG_DIR" 2>/dev/null || true
        echo "✅ Ownership fixed for $SCRIPT_DIR and $LOG_DIR."
        exit 0
        ;;
    update)
        echo "=========================================================="
        echo "🔄 Automatically upgrading $APP_NAME & packages..."
        echo "=========================================================="
        TARGET_USER="${2:-$CURRENT_USER}"
        sudo mkdir -p "$LOG_DIR" temp_downloads data
        sudo chown -R "$TARGET_USER:$TARGET_USER" "$SCRIPT_DIR" 2>/dev/null || true
        sudo chown -R "$TARGET_USER:$TARGET_USER" "$LOG_DIR" 2>/dev/null || true
        sudo rm -f godsdownload.lock test.lock /etc/supervisor/conf.d/${APP_NAME}.ini 2>/dev/null || true
        sudo ufw allow 8080/tcp 2>/dev/null || true
        sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true

        # Ensure FFmpeg is installed
        if ! command -v ffmpeg &> /dev/null; then
            echo "📦 FFmpeg not found. Installing ffmpeg..."
            sudo apt-get update -y -q && sudo apt-get install -y -q ffmpeg || true
        fi

        if [ -d ".git" ]; then
            echo "📥 Pulling latest git updates..."
            git pull || true
        fi

        # Ensure Python virtual environment exists
        if [ ! -f ".venv/bin/python3" ] || [ ! -f ".venv/bin/pip" ]; then
            echo "🐍 Python virtual environment (.venv) not found. Setting it up now..."
            python3 -m venv .venv 2>/dev/null || (sudo apt-get update -y -q && sudo apt-get install -y -q python3-venv python3-pip && python3 -m venv .venv)
            .venv/bin/pip install --upgrade pip -q
            .venv/bin/pip install -r requirements.txt -q
            .venv/bin/pip install --upgrade yt-dlp -q
        else
            echo "📦 Upgrading pip, yt-dlp & all python packages from requirements.txt..."
            .venv/bin/pip install --upgrade pip -q
            .venv/bin/pip install --upgrade yt-dlp -q
            .venv/bin/pip install --upgrade -r requirements.txt -q
        fi

        # Ensure supervisor configuration is in place
        if [ ! -f "/etc/supervisor/conf.d/${APP_NAME}.conf" ] && [ ! -f "/etc/supervisord.d/${APP_NAME}.conf" ]; then
            sudo mkdir -p /etc/supervisor/conf.d
            echo "[program:${APP_NAME}]
command=${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/main.py
directory=${SCRIPT_DIR}
user=${TARGET_USER}
autostart=true
autorestart=true
startretries=10
redirect_stderr=false
stdout_logfile=${LOG_DIR}/stdout.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
stderr_logfile=${LOG_DIR}/stderr.log
stderr_logfile_maxbytes=50MB
stderr_logfile_backups=10
environment=PYTHONUNBUFFERED=\"1\"" | sudo tee /etc/supervisor/conf.d/${APP_NAME}.conf > /dev/null
        fi

        echo "🔄 Reloading supervisor and restarting service..."
        sudo supervisorctl reread 2>/dev/null || true
        sudo supervisorctl update 2>/dev/null || true
        sudo supervisorctl restart "$APP_NAME" 2>/dev/null || sudo supervisorctl start "$APP_NAME" || true
        echo "✅ Update complete! Running version info:"
        .venv/bin/yt-dlp --version 2>/dev/null || true
        sudo supervisorctl status "$APP_NAME"
        exit 0
        ;;
    diag|check)
        echo "=========================================================="
        echo "🩺 Godsdownload System Diagnostics"
        echo "=========================================================="
        echo "1. Supervisor service:"
        sudo supervisorctl status "$APP_NAME" || true
        echo "2. FFmpeg status:"
        AAPANEL_FFMPEG=$(ls /www/server/ffmpeg*/bin/ffmpeg /www/server/ffmpeg*/ffmpeg 2>/dev/null | head -n 1)
        if command -v ffmpeg &> /dev/null; then
            ffmpeg -version | head -n 1
        elif [ -n "$AAPANEL_FFMPEG" ] && [ -x "$AAPANEL_FFMPEG" ]; then
            $AAPANEL_FFMPEG -version | head -n 1
        elif [ -f "/usr/local/bin/ffmpeg" ]; then
            /usr/local/bin/ffmpeg -version | head -n 1
        else
            echo "❌ FFmpeg NOT installed. Install 'ffmpeg manager' in aaPanel App Store or run: sudo apt-get install -y ffmpeg"
        fi
        echo "3. yt-dlp version in .venv:"
        if [ -f ".venv/bin/yt-dlp" ]; then
            .venv/bin/yt-dlp --version
        else
            echo "⚠️ .venv/bin/yt-dlp not found"
        fi
        echo "4. Disk space:"
        df -h "$SCRIPT_DIR"
        echo "5. Recent errors (last 10 lines of stderr.log):"
        if [ -f "${LOG_DIR}/stderr.log" ]; then
            tail -n 10 "${LOG_DIR}/stderr.log"
        else
            echo "No stderr.log found yet."
        fi
        exit 0
        ;;
    clean)
        echo "🧹 Running Godsdownload automated disk cleanup..."
        /bin/bash "${SCRIPT_DIR}/clean_disk.sh"
        echo "✅ Cleanup finished. Current disk space:"
        df -h "$SCRIPT_DIR"
        exit 0
        ;;
    deploy|install)
        # Continue with full deployment below
        ;;
    *)
        echo "Unknown command: $ACTION"
        echo "Available options: ./deploy.sh [deploy|update|restart|status|logs|cron|stop|fix-owner|diag|clean]"
        exit 1
        ;;
esac

echo "=========================================================="
echo "⚡ Starting Godsdownload Automated Supervisor Deployment"
echo "=========================================================="
echo "📂 Project Directory: $SCRIPT_DIR"
echo "👤 Current User:      $CURRENT_USER"
echo "=========================================================="

echo "🧹 [1/7] Cleaning old conflicting processes & locks..."
sudo systemctl stop godsdownload 2>/dev/null || true
sudo systemctl disable godsdownload 2>/dev/null || true
sudo supervisorctl stop "$APP_NAME" 2>/dev/null || true
pkill -9 -f "[m]ain.py" 2>/dev/null || true
rm -f godsdownload.lock test.lock 2>/dev/null || true

# Release any interrupted apt/dpkg locks automatically
sudo killall -9 apt apt-get dpkg 2>/dev/null || true
sudo rm -f /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null || true
sudo DEBIAN_FRONTEND=noninteractive dpkg --configure -a 2>/dev/null || true

echo "📦 [2/7] Installing system packages (Python3, FFmpeg, Supervisor, Git, Curl)..."
sudo DEBIAN_FRONTEND=noninteractive apt-get update -y -q
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold" python3 python3-pip python3-venv ffmpeg curl git supervisor

echo "📁 [3/7] Setting up required directories & permissions..."
mkdir -p temp_downloads data "$LOG_DIR"
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$SCRIPT_DIR" 2>/dev/null || true
sudo chown -R "$CURRENT_USER:$CURRENT_USER" "$LOG_DIR" 2>/dev/null || true
sudo ufw allow 8080/tcp 2>/dev/null || true
sudo iptables -I INPUT -p tcp --dport 8080 -j ACCEPT 2>/dev/null || true

echo "🐍 [4/7] Setting up Python virtual environment (.venv)..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate

echo "📦 [5/7] Installing Python requirements..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
# Ensure yt-dlp is latest release for max platform compatibility
pip install --upgrade yt-dlp -q

echo "⚙️ [6/7] Verifying environment configuration (.env)..."
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo "⚠️ Created default .env from .env.example. Please make sure to fill BOT_TOKEN!"
    else
        echo "⚠️ Warning: .env file not found. Please create one with your BOT_TOKEN!"
    fi
fi

echo "🚀 [7/7] Configuring and launching Supervisor process..."
CONF_CONTENT="[program:${APP_NAME}]
command=${SCRIPT_DIR}/.venv/bin/python3 ${SCRIPT_DIR}/main.py
directory=${SCRIPT_DIR}
user=${CURRENT_USER}
autostart=true
autorestart=true
startretries=10
redirect_stderr=false
stdout_logfile=${LOG_DIR}/stdout.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
stderr_logfile=${LOG_DIR}/stderr.log
stderr_logfile_maxbytes=50MB
stderr_logfile_backups=10
environment=PYTHONUNBUFFERED=\"1\""

# Write .conf to standard Ubuntu supervisor path (and clean up duplicate .ini in conf.d)
sudo mkdir -p /etc/supervisor/conf.d /etc/supervisord.d 2>/dev/null || true
sudo rm -f /etc/supervisor/conf.d/${APP_NAME}.ini 2>/dev/null || true
echo "$CONF_CONTENT" | sudo tee /etc/supervisor/conf.d/${APP_NAME}.conf > /dev/null

if [ -d "/etc/supervisord.d" ]; then
    echo "$CONF_CONTENT" | sudo tee /etc/supervisord.d/${APP_NAME}.conf > /dev/null
fi

# Support aaPanel Supervisor Manager plugin path if installed
if [ -d "/www/server/panel/plugin/supervisor/profile" ]; then
    echo "$CONF_CONTENT" | sudo tee /www/server/panel/plugin/supervisor/profile/${APP_NAME}.ini > /dev/null
fi

# Make sure supervisor system service is active and running
sudo systemctl enable supervisor 2>/dev/null || true
sudo systemctl restart supervisor 2>/dev/null || sudo service supervisor restart 2>/dev/null || true
sleep 1

# Reload supervisorctl and start bot
sudo supervisorctl reread || true
sudo supervisorctl update || true
sudo supervisorctl start "$APP_NAME" || sudo supervisorctl restart "$APP_NAME" || true

echo "⏰ [8/8] Installing automatic cron jobs (auto-update & disk cleanup)..."
CRON_FILE="/etc/cron.d/godsdownload-autoupgrade"
sudo bash -c "cat <<EOF > ${CRON_FILE}
# Automatically upgrade Godsdownload packages (pip, yt-dlp, requirements.txt) daily at 04:00 AM
0 4 * * * root /bin/bash ${SCRIPT_DIR}/deploy.sh update > ${LOG_DIR}/cron_upgrade.log 2>&1
EOF"
sudo chmod 644 "${CRON_FILE}"

DISK_CRON="/etc/cron.d/godsdownload-disk-clean"
sudo bash -c "cat <<EOF > ${DISK_CRON}
# Automatically clean temporary downloads, /tmp residues, and system journals every 15 minutes
*/15 * * * * root /bin/bash ${SCRIPT_DIR}/clean_disk.sh > /dev/null 2>&1
EOF"
sudo chmod 644 "${DISK_CRON}"
echo "✅ Automatic daily upgrade cron installed at ${CRON_FILE}."
echo "✅ Automatic disk cleanup cron installed at ${DISK_CRON}."

echo ""
echo "=========================================================="
echo "✅ DEPLOYMENT COMPLETE! Godsdownload Bot is running under Supervisor!"
echo "=========================================================="
sudo supervisorctl status "$APP_NAME"
echo ""
echo "📌 Useful Management Commands:"
echo " • Check status:   ./deploy.sh status   (or sudo supervisorctl status)"
echo " • View logs:       ./deploy.sh logs     (or tail -f ${LOG_DIR}/stderr.log)"
echo " • Restart bot:     ./deploy.sh restart  (or sudo supervisorctl restart godsdownload)"
echo " • Auto-update:     ./deploy.sh update   (upgrades pip, yt-dlp, requirements & restarts)"
echo " • View cron:       ./deploy.sh cron     (inspect automatic daily cron job)"
echo " • Stop bot:        ./deploy.sh stop"
echo "=========================================================="
