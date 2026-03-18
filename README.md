<p align="center">
  <img src="frontend/public/logo.svg" alt="ChannelHoarder" width="128" height="128">
</p>

<h1 align="center">ChannelHoarder</h1>

<p align="center">
  Self-hosted YouTube channel archiver with a modern web UI, designed for Plex-compatible output.<br>
  Runs as a single Docker container and automatically downloads new videos from your subscribed channels.
</p>

---

![Dashboard](screenshots/dashboard.png)

## Features

### Channel Management
- **Subscribe to Channels** — Add channels by URL or @handle from YouTube, Rumble, Twitch, Dailymotion, Vimeo, and Odysee
- **Automatic Scanning** — Checks subscribed channels daily for new uploads and queues them for download
- **Auto-Scan on Add** — Optionally trigger an immediate scan when adding a new channel
- **Channel Health Indicators** — Green/yellow/red status showing each channel's download success rate
- **Per-Channel Quality** — Set download quality independently for each channel (best, 1080p, 720p, 480p)
- **Per-Channel Download Directories** — Route channels to different storage locations
- **Pause/Resume** — Pause a channel to discover new videos without downloading them

### Plex Integration
- **TV Show Naming** — Organizes videos in Plex TV Show format (seasons by year, episodes numbered in upload order)
- **Customizable Naming Templates** — Configure the output filename format with variables like `{channel_name}`, `{season}`, `{episode}`, `{title}`, `{upload_date}`, `{video_id}` — with live preview
- **Per-Channel Naming Overrides** — Each channel can use its own naming template
- **NFO Metadata** — Generates tvshow.nfo and episode.nfo files for Plex/Jellyfin/Emby
- **Poster Art** — Downloads channel thumbnails as poster images

### Downloads
- **Queue-Based Pipeline** — Downloads are queued and processed sequentially with configurable delays
- **Pause/Resume Queue** — Pause the entire download queue and resume when ready
- **Real-Time Progress** — WebSocket-powered live download speed, ETA, and progress bars
- **Retry Failed Downloads** — Retry individual failures or all failed downloads at once
- **Bulk Video Management** — Select multiple videos to queue, skip, or unskip at once
- **Import Existing Files** — Scan a folder of previously downloaded videos and match them to channel entries by title. Matched files are moved into the proper Plex-compatible directory structure. Video filenames must contain the original video title for matching to work.
- **Livestream / Long Video Filter** — Auto-skip videos over a configurable duration (e.g., livestreams), with optional notification for manual review

### Authentication
- **PO Token Authentication** — Built-in PO token server (bgutil-ytdlp-pot-provider) for YouTube authentication with no manual setup
- **Automatic Cookie Sync** — Windows cookie exporter reads Firefox cookies and pushes to ChannelHoarder every 30 minutes
- **Browser Cookie Sync** — Tampermonkey userscript exports cookies on each YouTube page load
- **Manual Cookie Upload** — Upload a cookies.txt file directly
- **YouTube Data API** — Optional API key for faster, more reliable channel discovery
- **Multiple Player Clients** — Supports mweb, web, and web_creator strategies

### Anti-Detection
- **Configurable Download Delays** — Set minimum and maximum delay between downloads
- **Random Jitter** — Adds 0–10 seconds of random delay to avoid predictable patterns
- **User-Agent Rotation** — Rotates browser user-agent strings between downloads

### Monitoring & Diagnostics
- **Error Classification** — Categorizes failures (rate limited, geo-blocked, auth expired, etc.) with suggested fixes
- **Test Download Tool** — Multi-strategy diagnostic that tests metadata extraction across player clients (web, mweb, web_creator, without cookies)
- **Diagnostic Report** — One-click copy of full system report including app version, yt-dlp version, PO token status, cookie status, API key configuration, disk space, and download statistics
- **System Logs** — Searchable and filterable log viewer in the web UI

### Notifications
- **Telegram & Pushover** — Push notifications with configurable events:
  - Download complete
  - Download failed
  - Cookies expired / refreshed
  - Channel health alerts
  - Critical system alerts
  - Long video review required

### Infrastructure
- **Single Container** — Web server, download engine, PO token server, and scheduler all in one Docker container
- **Dark Mode UI** — Modern React-based interface with responsive layout
- **Auto-Updating yt-dlp** — Checks for yt-dlp updates daily and can be triggered manually
- **Config Import/Export** — Backup and restore all settings and channel subscriptions as JSON

## Quick Start

### Docker Compose

```yaml
services:
  channelhoarder:
    image: ghcr.io/sworcery/channelhoarder:latest
    container_name: channelhoarder
    ports:
      - "8587:8587"
    volumes:
      - ./config:/config
      - /path/to/your/media/youtube:/downloads
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    restart: unless-stopped
```

### Access the Web UI

Open `http://your-server-ip:8587` in your browser.

## How It Works

### Adding Channels

1. Go to the **Channels** page and click **Add Channel**
2. Paste a channel URL or @handle (e.g., `https://www.youtube.com/@ChannelName`)
3. ChannelHoarder fetches the channel metadata (name, description, thumbnail) and adds it to your subscription list
4. Set per-channel options: download quality, custom download directory, enabled/disabled
5. Optionally auto-scan immediately after adding

### Automatic Scanning

- The scheduler runs a channel scan daily at 3:00 AM (configurable)
- Each scan checks for new videos that haven't been downloaded yet
- New videos are added to the download queue with `pending` status
- If a YouTube Data API key is configured, it's used for faster and more reliable discovery; otherwise, yt-dlp handles discovery directly
- Manual scans can be triggered per-channel or for all channels at once

### Download Pipeline

1. **Queue Processing** — Every 30 seconds, the next queued video is picked up
2. **Rate Limiting** — A configurable delay (default 10–30 seconds) with optional random jitter is applied between downloads
3. **Download** — yt-dlp downloads the video using the configured player client with PO tokens, plus thumbnail and metadata
4. **Naming** — Files are renamed to the Plex-compatible format with season/episode numbering
5. **Verification** — Output files are verified to exist and the database is updated
6. **Progress** — Real-time progress (speed, ETA, percentage) is broadcast via WebSocket to connected browsers

### Importing Existing Videos

If you already have downloaded videos from a channel, you can import them instead of re-downloading:

1. Open a channel's detail page and click **Import Existing**
2. Enter the folder path (on the server) containing your video files
3. ChannelHoarder scans the folder and fuzzy-matches filenames against undownloaded video entries
4. Review the matches with confidence scores, select which to import
5. Imported files are moved into the correct Plex-compatible directory structure

**Important:** Video filenames must contain the original video title for matching to work. Files named with only dates or generic names won't match.

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

### Channel Health

Each channel shows a health indicator:
- **Green** — All recent downloads succeeded
- **Yellow** — Some downloads failed recently
- **Red** — Most or all recent downloads are failing, with the specific error reason shown

## File Organization

Videos are saved in Plex TV Show format. Each channel becomes a "show", each year becomes a "season", and videos are numbered as episodes in upload order:

```
/downloads/
  Channel Name/
    tvshow.nfo
    poster.jpg
    Season 2024/
      S2024E001 - Video Title - 20240115 - [videoId].mp4
      S2024E001 - Video Title - 20240115 - [videoId].nfo
      S2024E002 - Another Video - 20240203 - [videoId].mp4
      ...
    Season 2025/
      S2025E001 - New Video - 20250112 - [videoId].mp4
      ...
```

The default naming template is:

```
{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]
```

Available template variables: `{channel_name}`, `{season}`, `{episode}`, `{title}`, `{upload_date}`, `{video_id}`

Point your Plex library at the `/downloads` directory as a "TV Shows" library and it will automatically pick up channels as shows.

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
| `POT_SERVER_URL` | `http://127.0.0.1:4416` | PO token server address (internal to container) |
| `CONFIG_DIR` | `/config` | Configuration and database storage path |
| `DOWNLOAD_DIR` | `/downloads` | Video output directory |
| `COOKIE_WATCH_DIR` | `/cookies` | Directory watched for cookie file updates |
| `MAX_CONCURRENT_DOWNLOADS` | `1` | Maximum simultaneous downloads |
| `MAX_RETRIES` | `3` | Retry attempts for failed downloads |
| `DOWNLOAD_DELAY_MIN` | `10` | Minimum delay between downloads (seconds) |
| `DOWNLOAD_DELAY_MAX` | `30` | Maximum delay between downloads (seconds) |
| `JITTER_ENABLED` | `true` | Add random 0–10s jitter between downloads |
| `USER_AGENT_ROTATION` | `true` | Rotate browser user-agent strings |

### YouTube Data API Key (Optional)

A YouTube Data API key improves channel scanning reliability and speed. Without it, yt-dlp handles discovery directly, which works but can be slower.

To get a free API key:
1. Go to the [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable the **YouTube Data API v3**
3. Create an API key under **Credentials**
4. Add the key in **Settings > Authentication** in the web UI, or set the `YOUTUBE_API_KEY` environment variable

The free tier provides 10,000 quota units per day, which is enough for most personal use.

### Cookie Authentication (Optional)

Cookies are optional but can help if you have YouTube Premium or need to access region-specific content. There are three ways to provide cookies:

**Option 1: Automatic Cookie Sync (Recommended)**

The Windows cookie exporter reads cookies directly from Firefox's database, including HttpOnly cookies that browser scripts cannot access.

1. Install Python 3.10+ and [pycryptodome](https://pypi.org/project/pycryptodome/) on a Windows machine or VM
2. Install Firefox and log into YouTube
3. Copy the `tools/` folder from this repository to the machine
4. Edit `cookie_exporter.ini`:
   - Set `server_url` to your ChannelHoarder address (e.g., `http://your-server:8587`)
   - Optionally set `profile` if using a non-default Firefox profile
   - Optionally set `domains` to customize which cookie domains to export (default: `.youtube.com, .google.com`)
   - Optionally set `refresh_url` to change the page loaded before export (default: `https://www.youtube.com`)
   - Optionally set `refresh_wait` to change seconds to wait for page load (default: `20`)
5. Run `python cookie_exporter.py --dry-run` to test
6. Run `install_task.bat` as administrator to set up automatic export every 30 minutes
7. Every run: opens Firefox to YouTube (refreshes cookies) → closes Firefox → reads the cookie DB → pushes to ChannelHoarder via API

**Option 2: Browser Cookie Sync (Tampermonkey)**

1. Install the [Tampermonkey](https://www.tampermonkey.net/) browser extension
2. Go to **Settings > Authentication** in ChannelHoarder and click **Install Tampermonkey Script**
3. The script comes pre-configured with your server address and exports cookies each time you load a YouTube page (with a 5-minute cooldown between exports)
4. Note: Cannot access HttpOnly cookies (SID, HSID, SSID) — Option 1 is more complete

**Option 3: Manual Upload**

1. Use a cookie export extension (e.g., "Get cookies.txt LOCALLY")
2. Navigate to youtube.com while logged in and export cookies
3. Upload the file via **Settings > Authentication** in the web UI

Cookies expire periodically. Options 1 and 2 handle re-export automatically.

### Settings (via Web UI)

All settings are configurable through the **Settings** page:

- **General** — Manual scan trigger, system overview, config import/export
- **Authentication** — PO token status, cookie upload/status, browser cookie sync (Tampermonkey), YouTube API key, player client strategy
- **Naming** — Output filename template with live preview and variable reference (`{channel_name}`, `{season}`, `{episode}`, `{title}`, `{upload_date}`, `{video_id}`)
- **yt-dlp** — Version info, manual update trigger
- **Anti-Detection** — Download delay range, jitter toggle, user-agent rotation, max video duration filter (for skipping livestreams and long videos)
- **Notifications** — Telegram and Pushover configuration with test buttons, per-event toggles

## Supported Platforms

| Platform | Channel Discovery | Video Download | API Support |
|---|---|---|---|
| YouTube | RSS + API + yt-dlp | yt-dlp | YouTube Data API v3 |
| Rumble | yt-dlp | yt-dlp | No |
| Twitch | yt-dlp | yt-dlp | No |
| Dailymotion | yt-dlp | yt-dlp | No |
| Vimeo | yt-dlp | yt-dlp | No |
| Odysee | yt-dlp | yt-dlp | No |

## Tech Stack

- **Backend**: Python 3.12, FastAPI, SQLAlchemy (async), APScheduler, yt-dlp
- **Frontend**: React 18, TypeScript, Tailwind CSS, TanStack Query, React Router
- **Database**: SQLite (WAL mode)
- **Auth**: bgutil-ytdlp-pot-provider (PO tokens), optional cookies.txt
- **Docker**: Multi-stage build with Python, Node.js, ffmpeg, and PO token provider

## Volumes

| Path | Purpose |
|---|---|
| `/config` | Database, settings, API key, optional cookies.txt |
| `/downloads` | Downloaded videos, thumbnails, and metadata (point Plex here) |
| `/cookies` | Optional: watched directory for cookie file updates |

## API

All endpoints are under `/api/v1/`:

### Channels
- `GET /channels` — List all channels (supports search)
- `POST /channels` — Add a new channel
- `GET /channels/{id}` — Get channel details
- `PUT /channels/{id}` — Update channel settings (quality, download dir, enabled)
- `DELETE /channels/{id}` — Delete a channel (optionally delete files)
- `POST /channels/{id}/scan` — Trigger a manual scan
- `GET /channels/{id}/videos` — List channel videos (filterable by status)
- `POST /channels/{id}/download-all` — Queue all pending videos
- `POST /channels/{id}/videos/bulk-queue` — Queue selected videos
- `POST /channels/{id}/videos/bulk-skip` — Skip selected videos
- `POST /channels/{id}/videos/bulk-unskip` — Unskip selected videos
- `POST /channels/{id}/import/scan` — Scan folder for existing video files
- `POST /channels/{id}/import/confirm` — Import matched files

### Downloads
- `GET /downloads/queue` — Current download queue with progress
- `POST /downloads/queue` — Add video to queue
- `DELETE /downloads/queue/{id}` — Remove from queue
- `POST /downloads/queue/bulk-remove` — Remove multiple items from queue
- `POST /downloads/clear-queue` — Clear all queued (non-active) downloads
- `GET /downloads/active` — Currently downloading
- `GET /downloads/paused` — Check if queue is paused
- `POST /downloads/pause` — Pause the download queue
- `POST /downloads/resume` — Resume the download queue
- `GET /downloads/history` — Filterable download history
- `POST /downloads/retry/{id}` — Retry a failed download
- `POST /downloads/retry-all-failed` — Retry all failed downloads

### Dashboard
- `GET /dashboard/stats` — Aggregate statistics
- `GET /dashboard/recent` — Recent downloads
- `GET /dashboard/storage` — Storage breakdown by channel

### Authentication
- `POST /auth/cookies/upload` — Upload cookies.txt file
- `POST /auth/cookies/push` — Push cookies as JSON (for browser extensions and scripts)
- `GET /auth/cookies/status` — Cookie file status
- `POST /auth/cookies/validate` — Force a cookie health check
- `DELETE /auth/cookies` — Remove cookies
- `PUT /auth/api-key` — Set YouTube API key
- `PUT /auth/player-client` — Set yt-dlp player client strategy
- `GET /auth/status` — Overall auth status

### Settings
- `GET /settings` — Get all settings
- `PUT /settings` — Update settings
- `GET /settings/userscript.user.js` — Download pre-configured Tampermonkey script
- `GET /settings/export` — Export config backup (JSON)
- `POST /settings/import` — Import config backup
- `POST /settings/webhook/test` — Test notification delivery

### System
- `GET /system/health` — Health check with version
- `GET /system/diagnostics` — Full diagnostic report
- `GET /system/diagnostics/{video_id}` — Per-video diagnostic report
- `GET /system/logs` — System logs with filtering
- `GET /system/pot-server-log` — PO token server logs and process status
- `POST /system/test-download` — Test download capability (multi-strategy)
- `POST /system/scan-all` — Scan all enabled channels
- `GET /system/ytdlp/version` — Current yt-dlp version
- `POST /system/ytdlp/update` — Update yt-dlp

### WebSocket
- `WS /ws/progress` — Real-time download progress updates

## License

This project is for personal use.
