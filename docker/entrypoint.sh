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
if [ "${POT_SERVER_ENABLED:-true}" = "true" ]; then
    echo "Starting PO token provider server on port ${POT_SERVER_PORT:-4416}..."
    if [ -d "/opt/pot-provider/server/build" ]; then
        cd /opt/pot-provider/server
        node build/main.js --port "${POT_SERVER_PORT:-4416}" &
        POT_PID=$!
        echo "PO token server started (PID: $POT_PID)"
        cd /app
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
