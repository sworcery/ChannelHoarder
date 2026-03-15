# ChannelHoarder

Self-hosted YouTube channel archiver with a modern web UI, designed for Plex-compatible output. Runs as a single Docker container on Unraid (or any Docker host) and automatically downloads new videos from your subscribed channels.

## Features

- **Automatic Channel Scanning** - Checks subscribed channels daily for new uploads and queues them for download
- **Plex-Compatible Naming** - Organizes videos in TV Show format so Plex recognizes them as shows with seasons and episodes
- **Zero-Cookie Authentication** - Uses PO tokens (bgutil-ytdlp-pot-provider) for YouTube authentication with no manual cookie management
- **Per-Channel Quality Settings** - Set download quality independently for each channel (best, 1080p, 720p, 480p)
- **Error Diagnostics** - Classifies download failures with human-readable explanations, suggested fixes, and a one-click "Copy Diagnostic Report" button
- **Real-Time Progress** - WebSocket-powered live download progress bars and status updates in the dashboard
- **Modern Web UI** - React-based interface with dark mode, responsive layout, and channel health indicators
- **Anti-Detection** - Configurable delays, jitter, and user-agent rotation to avoid rate limiting
- **Single Container** - Everything runs in one Docker container: web server, download engine, PO token server, and scheduler

## File Organization

Videos are saved in Plex TV Show format. Each channel becomes a "show", each year becomes a "season", and videos are numbered as episodes in upload order:

```
/downloads/
  Technology Connections/
    Season 2024/
      S2024E001 - How Washing Machines Work - 20240115 - [dQw4w9WgXcQ].mp4
      S2024E001 - How Washing Machines Work - 20240115 - [dQw4w9WgXcQ].jpg
      S2024E001 - How Washing Machines Work - 20240115 - [dQw4w9WgXcQ].info.json
      S2024E002 - The Dishwasher Debate - 20240203 - [xvFZjo5PgG0].mp4
      ...
    Season 2025/
      S2025E001 - Why VHS Won - 20250112 - [abc123defgh].mp4
      ...
  LGR/
    Season 2024/
      ...
```

Point your Plex library at the `/downloads` directory as a "TV Shows" library and it will automatically pick up channels as shows.

## Quick Start

### Docker Compose

```yaml
version: "3.8"

services:
  channelhoarder:
    build:
      context: .
      dockerfile: docker/Dockerfile
    container_name: channelhoarder
    ports:
      - "8000:8000"
    volumes:
      - ./config:/config
      - /path/to/your/media/youtube:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    restart: unless-stopped
```

### Unraid

Use the included Unraid template at `docker/unraid-template.xml`, or install manually:

1. Add a new container in the Docker tab
2. Set the repository to the built image
3. Map `/config` to your appdata directory
4. Map `/downloads` to your Plex YouTube library folder
5. Set the web UI port to `8000`

### Access the Web UI

Open `http://your-server-ip:8000` in your browser.

## How It Works

### Adding Channels

1. Go to the **Channels** page and click **Add Channel**
2. Paste a YouTube channel URL (e.g., `https://www.youtube.com/@TechnologyConnections`)
3. ChannelHoarder fetches the channel metadata and adds it to your subscription list
4. Set per-channel options: download quality, enabled/disabled, custom schedule

### Automatic Scanning

- The scheduler runs a channel scan daily at 3:00 AM (configurable)
- Each scan checks for new videos that haven't been downloaded yet
- New videos are added to the download queue with `pending` status
- If a YouTube Data API key is configured, it's used for faster and more reliable discovery; otherwise, yt-dlp handles discovery directly

### Download Pipeline

1. **Queue Processing** - Every 30 seconds, the next queued video is picked up
2. **Rate Limiting** - A configurable delay (default 10-30 seconds) with random jitter is applied between downloads
3. **Download** - yt-dlp downloads the video with the configured quality, plus thumbnail and metadata
4. **Naming** - Files are renamed to the Plex-compatible format with season/episode numbering
5. **Verification** - Output files are verified to exist and the database is updated
6. **Progress** - Real-time progress is broadcast via WebSocket to any connected browsers

### Error Handling

When a download fails, ChannelHoarder classifies the error and provides actionable information:

| Error Code | Meaning | Auto-Recovery |
|---|---|---|
| `RATE_LIMITED` | YouTube is throttling requests | Increases delay automatically |
| `GEO_BLOCKED` | Video not available in your region | No |
| `VIDEO_UNAVAILABLE` | Video was deleted or made private | No |
| `PO_TOKEN_FAILURE` | PO token server is not responding | Retries after health check |
| `YTDLP_OUTDATED` | yt-dlp needs an update | Auto-updates daily |
| `FFMPEG_ERROR` | Post-processing failed | Retries up to 3 times |
| `DISK_FULL` | Not enough storage space | No |
| `NETWORK_ERROR` | Connection issue | Retries with backoff |
| `AUTH_EXPIRED` | Authentication needs refresh | Retries with new token |

Each failed download stores the full error log, diagnosis, and suggested fix. The **Diagnostics** page lets you:
- View all errors filtered by type, channel, or date
- Expand any error to see the full yt-dlp output
- Click **Copy Diagnostic Report** to get a formatted text block with all system info for troubleshooting

### Channel Health

Each channel shows a health indicator on the dashboard:
- **Green** - All recent downloads succeeded
- **Yellow** - Some downloads failed recently
- **Red** - Most or all recent downloads are failing, with the specific error reason shown

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PUID` | `1000` | User ID for file permissions |
| `PGID` | `1000` | Group ID for file permissions |
| `TZ` | `America/New_York` | Timezone for scheduling |
| `LOG_LEVEL` | `info` | Logging level (`debug`, `info`, `warning`, `error`) |
| `YOUTUBE_API_KEY` | *(empty)* | Optional YouTube Data API v3 key for reliable channel discovery |
| `POT_SERVER_ENABLED` | `true` | Enable the built-in PO token server |
| `CONFIG_DIR` | `/config` | Configuration and database storage path |
| `DOWNLOAD_DIR` | `/downloads` | Video output directory |

### YouTube Data API Key (Optional)

A YouTube Data API key improves channel scanning reliability and speed. Without it, yt-dlp handles discovery directly, which works but can be slower.

To get a free API key:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **YouTube Data API v3**
3. Create an API key under **Credentials**
4. Add the key in **Settings > Authentication** in the web UI, or set the `YOUTUBE_API_KEY` environment variable

The free tier provides 10,000 quota units per day, which is enough for most personal use.

### Settings (via Web UI)

All settings are configurable through the **Settings** page:

- **General** - Scan schedule (cron), concurrent downloads, retry limits
- **Authentication** - PO token status, API key configuration, optional cookie upload
- **Naming** - Output template with live preview
- **yt-dlp** - Version info, manual update trigger
- **Anti-Detection** - Download delay range, jitter toggle, user-agent rotation

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), APScheduler, yt-dlp
- **Frontend**: React 18, TypeScript, Tailwind CSS, TanStack Query, React Router
- **Database**: SQLite
- **Docker**: Multi-stage build with Python, Node.js, ffmpeg, Deno, and PO token provider

## Volumes

| Path | Purpose |
|---|---|
| `/config` | Database, settings, optional cookies.txt |
| `/downloads` | Downloaded videos, thumbnails, and metadata (point Plex here) |

## API

All endpoints are under `/api/v1/`:

- `GET/POST /channels` - List and add channels
- `GET/PUT/DELETE /channels/{id}` - Channel management
- `POST /channels/{id}/scan` - Trigger a manual scan
- `GET /downloads/queue` - Current download queue with progress
- `GET /downloads/history` - Filterable download history
- `POST /downloads/retry/{video_id}` - Retry a failed download
- `GET /dashboard/stats` - Aggregate statistics
- `GET /system/health` - Health check
- `GET /system/diagnostics` - Full system diagnostic report
- `WS /ws/progress` - Real-time WebSocket progress updates
