# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install Deno (for yt-dlp JS runtime and PO token provider)
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

# Install Python dependencies
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp and PO token provider plugin
RUN pip install --no-cache-dir yt-dlp bgutil-ytdlp-pot-provider

# Set up bgutil POT provider server
RUN git clone --single-branch --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/pot-provider \
    && cd /opt/pot-provider/server \
    && deno install --allow-scripts=npm:canvas --frozen || true

# Copy backend code
COPY backend/app/ ./app/
COPY backend/alembic.ini .

# Copy built frontend
COPY --from=frontend-builder /build/dist/ ./static/

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create default directories
RUN mkdir -p /config /downloads

# Environment defaults
ENV CONFIG_DIR=/config \
    DOWNLOAD_DIR=/downloads \
    LOG_LEVEL=info \
    TZ=America/New_York \
    PUID=1000 \
    PGID=1000 \
    POT_SERVER_ENABLED=true \
    POT_SERVER_PORT=4416 \
    POT_SERVER_URL=http://127.0.0.1:4416

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/v1/system/health || exit 1

VOLUME ["/config", "/downloads"]

ENTRYPOINT ["/entrypoint.sh"]
