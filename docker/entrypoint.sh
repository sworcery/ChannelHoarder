#!/bin/bash
set -e

echo "=========================================="
echo " ChannelHoarder"
echo "=========================================="

# Handle PUID/PGID for file permissions (Unraid compatibility)
PUID=${PUID:-1000}
PGID=${PGID:-1000}

echo "Starting with UID: $PUID, GID: $PGID"

# Create directories if they don't exist
mkdir -p /config /downloads

# Initialize database (create tables)
cd /app
python -c "
import asyncio
from app.database import init_database
asyncio.run(init_database())
print('Database initialized')
"

# Start PO token provider server in background (if enabled)
POT_LOG="/config/pot-server.log"
if [ "${POT_SERVER_ENABLED:-true}" = "true" ]; then
    echo "Starting PO token provider server on port ${POT_SERVER_PORT:-4416}..."
    if [ -d "/opt/pot-provider/server/build" ]; then
        cd /opt/pot-provider/server
        NODE_OPTIONS="--max-old-space-size=256" node build/main.js --port "${POT_SERVER_PORT:-4416}" > "$POT_LOG" 2>&1 &
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
            if ! kill -0 $POT_PID 2>/dev/null; then
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
trap 'echo "Shutting down..."; kill $POT_PID 2>/dev/null; exit 0' SIGTERM SIGINT

echo "Starting web server on port 8000..."

# Start the main application
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --workers 1 \
    --log-level "${LOG_LEVEL:-info}" \
    --forwarded-allow-ips "*"
