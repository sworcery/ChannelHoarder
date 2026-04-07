#!/bin/bash
set -e

echo "=========================================="
echo " ChannelHoarder"
echo "=========================================="

# Handle PUID/PGID for file permissions (Unraid/TrueNAS compatibility)
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting with UID: $PUID, GID: $PGID"

# Create group and user matching the requested PUID/PGID
# Use -o (non-unique) to allow reuse of an existing GID/UID without failing
if ! getent group appuser > /dev/null 2>&1; then
    groupadd -o -g "$PGID" appuser
fi
if ! getent passwd appuser > /dev/null 2>&1; then
    useradd -o -u "$PUID" -g "$PGID" -d /home/appuser -s /bin/sh -M appuser
fi

# Create directories and set ownership
mkdir -p /config /downloads /cookies /home/appuser
chown -R appuser:appuser /config /home/appuser /app /opt/pot-provider
# Downloads and cookies may be NFS/SMB mounts that reject chown — don't fail the container
chown -R appuser:appuser /downloads 2>/dev/null || echo "WARNING: Could not chown /downloads (NFS/SMB mount?) — continuing"
chown -R appuser:appuser /cookies 2>/dev/null || echo "WARNING: Could not chown /cookies (NFS/SMB mount?) — continuing"

# Set up bgutil symlink in user's home so the yt-dlp plugin can find its files
ln -sf /opt/bgutil-ytdlp-pot-provider /home/appuser/bgutil-ytdlp-pot-provider

# Initialize database (create tables)
cd /app
gosu appuser python -c "
import asyncio
from app.database import init_database
asyncio.run(init_database())
print('Database initialized')
" || { echo "Database initialization failed"; exit 1; }

# Start PO token provider server in background (if enabled)
POT_LOG="/config/pot-server.log"
if [ "${POT_SERVER_ENABLED:-true}" = "true" ]; then
    echo "Starting PO token provider server on port ${POT_SERVER_PORT:-4416}..."
    if [ -d "/opt/pot-provider/server/build" ]; then
        cd /opt/pot-provider/server
        HOME=/home/appuser NODE_OPTIONS="--max-old-space-size=256" gosu appuser node build/main.js --port "${POT_SERVER_PORT:-4416}" > "$POT_LOG" 2>&1 &
        POT_PID=$!
        echo "PO token server started (PID: $POT_PID), logging to $POT_LOG"
        cd /app

        # Wait for server to be ready (up to 30 seconds)
        echo "Waiting for PO token server to be ready..."
        for i in $(seq 1 30); do
            if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${POT_SERVER_PORT:-4416}/ping" 2>/dev/null | grep -q "200"; then
                echo "PO token server ready after ${i}s"
                break
            fi
            if ! kill -0 ${POT_PID:-} 2>/dev/null; then
                echo "ERROR: PO token server died. Last log output:"
                tail -20 "$POT_LOG"
                break
            fi
            sleep 1
        done
    else
        echo "WARNING: PO token provider not found at /opt/pot-provider/server"
    fi
fi

# Handle graceful shutdown
trap 'echo "Shutting down..."; kill ${POT_PID:-} 2>/dev/null; exit 0' SIGTERM SIGINT

echo "Starting web server on port 8000..."

# Start the main application as the configured user
exec gosu appuser env HOME=/home/appuser uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --loop uvloop \
    --log-level "${LOG_LEVEL:-info}" \
    --forwarded-allow-ips "*"
