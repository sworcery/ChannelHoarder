# Stage 1: Build frontend
FROM node:20-alpine AS frontend-builder
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime
FROM python:3.12-slim-bookworm

# Install system dependencies, Node.js, and canvas native libs (for bgutil PO token server)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    unzip \
    ca-certificates \
    git \
    gnupg \
    gosu \
    build-essential \
    libcairo2-dev \
    libpango1.0-dev \
    libjpeg-dev \
    libgif-dev \
    librsvg2-dev \
    pkg-config \
    python3 \
    tini \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install Deno - the supported JS runtime yt-dlp's EJS solver needs to solve
# YouTube's n-signature challenge. Node 20 is no longer a supported runtime for
# the challenge solver, so without Deno, downloads for logged-in (cookie) sessions
# fail with "No video formats found". Deno also enables the bgutil script-deno PO
# token provider.
RUN curl -fsSL https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip -o /tmp/deno.zip \
    && unzip /tmp/deno.zip -d /usr/local/bin \
    && rm /tmp/deno.zip \
    && chmod +x /usr/local/bin/deno

# Install Python dependencies
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install PO token provider plugin (yt-dlp already in requirements.txt)
RUN pip install --no-cache-dir bgutil-ytdlp-pot-provider

# Set up bgutil POT provider server
RUN git clone --single-branch --depth 1 https://github.com/Brainicism/bgutil-ytdlp-pot-provider.git /opt/pot-provider \
    && cd /opt/pot-provider/server \
    && npm ci --no-audit --no-fund \
    && npx tsc \
    && ln -s /opt/pot-provider /opt/bgutil-ytdlp-pot-provider

# Copy backend code
COPY backend/app/ ./app/
COPY backend/alembic.ini .

# Copy userscript template for in-app Tampermonkey install
COPY tools/tampermonkey_cookie_exporter.user.js ./tools/

# Copy built frontend
COPY --from=frontend-builder /build/dist/ ./static/

# Copy entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Create default directories
RUN mkdir -p /config /downloads /cookies

# Environment defaults
ENV CONFIG_DIR=/config \
    DOWNLOAD_DIR=/downloads \
    COOKIE_WATCH_DIR=/cookies \
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

VOLUME ["/config", "/downloads", "/cookies"]

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
