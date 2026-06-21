<p align="center">
  <img src="frontend/public/logo.svg" alt="ChannelHoarder" width="128" height="128">
</p>

<h1 align="center">ChannelHoarder</h1>

<p align="center">
  Self-hosted YouTube channel archiver with a modern web UI, designed for Plex-compatible output.<br>
  Runs as a single Docker container and automatically downloads new videos from your subscribed channels.
</p>

---

## Features

- **Multi-platform** - Subscribe to channels (or YouTube playlists) from YouTube, Rumble, Twitch, Dailymotion, Vimeo, and Odysee by URL or @handle
- **Automatic archiving** - Scheduled scans detect new videos and queue them, with per-channel quality (up to 4K), quality cutoff, title (keyword/regex) and duration filters, and per-channel storage paths
- **Plex / Jellyfin / Emby ready** - TV Show-style naming (seasons by year, chronological episodes) with customizable templates, NFO metadata, and poster/season art
- **Sonarr-style episode management** - Monitor/unmonitor, status icons, collapsible seasons, bulk actions, and per-episode retry, re-download, rename, and delete
- **Robust downloads** - Sequential queue with rate limiting and live progress (speed/ETA) over WebSocket, standalone single-video downloads, import of existing files, subtitle and chapter support, and automatic Shorts / livestream filtering
- **Smart authentication** - Cookies (auto-sync via Windows exporter or Tampermonkey, or manual upload) with PO token fallback, optional YouTube Data API key, and a selectable yt-dlp player client
- **Anti-detection** - Configurable download delays, random jitter, and user-agent rotation
- **Monitoring** - Error classification with suggested fixes, diagnostic reports, searchable logs, and Telegram / Pushover / Discord notifications
- **Self-contained** - Single Docker container (web UI, download engine, PO token server, scheduler) with dark-mode UI, auto-updating yt-dlp, and config import/export

## Prerequisites

- **Docker & Docker Compose**  - [Install Docker](https://docs.docker.com/get-docker/)
- **Disk space**  - Plan 500 MB–1 GB per video at best quality; minimum 50 GB recommended
- **Platform**  - Linux, macOS, Windows (via Docker Desktop), Unraid, TrueNAS

## Quick Start

### Docker Compose

```yaml
services:
  channelhoarder:
    image: ghcr.io/sworcery/channelhoarder:latest
    container_name: channelhoarder
    ports:
      - "8587:8000"
    volumes:
      - ./config:/config
      - /path/to/your/media/youtube:/downloads
      - ./cookies:/cookies
    environment:
      - PUID=1000
      - PGID=1000
      - TZ=America/New_York
    restart: unless-stopped
```

> **Note:** Set `PUID`/`PGID` to match your media server user so downloaded files have the correct ownership. On Unraid, this is typically `PUID=99` (nobody).

### Access the Web UI

Open `http://your-server-ip:8587` in your browser.

### First Steps

1. Go to **Settings → Authentication** and verify the PO Token Server shows **Running**
2. *(Optional)* Add a YouTube Data API key for faster channel discovery
3. *(Optional)* Configure Telegram or Pushover notifications
4. Go to **Channels** → **Add Channel** and paste a channel URL
5. Videos will be scanned immediately and queued for download

> **Security note:** ChannelHoarder has no built-in authentication  - it is designed for trusted local networks. If exposing to the internet, place it behind a reverse proxy with authentication (e.g. Authelia, Nginx Proxy Manager).

### Updating

```bash
docker compose pull && docker compose up -d
```

## How It Works

### Adding Channels

1. Go to the **Channels** page and click **Add Channel**
2. Paste a channel URL or @handle (e.g., `https://www.youtube.com/@ChannelName`)
3. ChannelHoarder fetches the channel metadata (name, description, thumbnail) and adds it to your subscription list
4. Set per-channel options: download quality, custom download directory, enabled/disabled
5. Optionally auto-scan immediately after adding

### Automatic Scanning

- The scheduler runs a channel scan on a configurable schedule (default: daily at 3:00 AM, changeable in Settings)
- Each scan checks for new videos that haven't been downloaded yet
- New videos are added to the download queue with `pending` status
- If a YouTube Data API key is configured, it's used for faster and more reliable discovery; otherwise, yt-dlp handles discovery directly
- Manual scans can be triggered per-channel or for all channels at once

### Download Pipeline

1. **Queue Processing**  - Every 30 seconds, the next queued video is picked up
2. **Rate Limiting**  - A configurable delay (default 10–30 seconds) with optional random jitter is applied between downloads
3. **Download**  - yt-dlp downloads the video with PO tokens, plus thumbnail and metadata. By default yt-dlp auto-selects a working player client; a specific client can be forced in Settings → Authentication
4. **Naming**  - Files are renamed to the Plex-compatible format with season/episode numbering
5. **Verification**  - Output files are verified to exist and the database is updated
6. **Progress**  - Real-time progress (speed, ETA, percentage) is broadcast via WebSocket to connected browsers

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
| `FORMAT_UNAVAILABLE` | No downloadable format (player client hit a DRM/SABR restriction) | Retries; set player client to Default (automatic) if persistent |
| `PO_TOKEN_FAILURE` | PO token server is not responding | Retries after health check |
| `YTDLP_OUTDATED` | yt-dlp needs an update | Auto-updates daily |
| `FFMPEG_ERROR` | Post-processing failed | Retries up to 3 times |
| `DISK_FULL` | Not enough storage space | No |
| `NETWORK_ERROR` | Connection issue | Retries with backoff |
| `AUTH_EXPIRED` | Authentication needs refresh | Retries with new token |

### Channel Health

Each channel shows a health indicator:
- **Green**  - All recent downloads succeeded
- **Yellow**  - Some downloads failed recently
- **Red**  - Most or all recent downloads are failing, with the specific error reason shown

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

### Plex Library Setup

Create a **separate Plex library** for your YouTube channels - do not mix them with your regular TV shows library.

1. In Plex, click **+** to add a new library
2. Select **TV Shows** as the type
3. Point it at your ChannelHoarder downloads folder
4. Under **Advanced**, set the agent to **Personal Media Shows**
5. Check **Use local assets**
6. Save and scan

**Why a separate library?** The Personal Media Shows agent reads episode titles, descriptions, and artwork from the NFO files that ChannelHoarder generates. The default Plex TV agent ignores NFO files and only looks up online databases (TVDB/TMDB), which won't find YouTube channels. Using Personal Media Shows in your regular TV library would break metadata for shows from Sonarr/etc.

If you already added channels to an existing TV library, move them to the new library and unmatch each show for the NFO data to take effect.

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
| `EXTRA_DOWNLOAD_DIRS` | *(empty)* | Comma-separated additional allowed download paths (e.g. `/media,/cartoons`) |
| `COOKIE_WATCH_DIR` | `/cookies` | Directory watched for cookie file updates |
| `MAX_CONCURRENT_DOWNLOADS` | `1` | Maximum simultaneous downloads |
| `MAX_RETRIES` | `3` | Retry attempts for failed downloads |
| `DOWNLOAD_DELAY_MIN` | `10` | Minimum delay between downloads (seconds) |
| `DOWNLOAD_DELAY_MAX` | `30` | Maximum delay between downloads (seconds) |
| `JITTER_ENABLED` | `true` | Add random 0–10s jitter between downloads |
| `USER_AGENT_ROTATION` | `true` | Rotate browser user-agent strings |
| `YTDLP_AUTO_UPDATE` | `true` | Daily check of PyPI for a newer yt-dlp and auto-upgrade when behind |
| `YTDLP_AUTO_RESTART` | `true` | After an auto-update, restart the container when idle to apply it (requires a restart policy, e.g. `restart: unless-stopped`) |

### YouTube Data API Key (Optional)

A YouTube Data API key improves channel scanning reliability and speed. Without it, yt-dlp handles discovery directly, which works but can be slower.

To get a free API key:

1. Go to [console.cloud.google.com](https://console.cloud.google.com/) and sign in with your Google account
2. Click **Select a project** at the top, then **New Project**. Name it anything (e.g. "ChannelHoarder") and click **Create**
3. Make sure your new project is selected, then go to **APIs & Services > Library**
4. Search for **YouTube Data API v3** and click on it
5. Click **Enable**
6. Go to **APIs & Services > Credentials**
7. Click **+ Create Credentials > API key**
8. Copy the API key that appears
9. In ChannelHoarder, go to **Settings > Authentication** and paste the key, or set the `YOUTUBE_API_KEY` environment variable in your container config

The free tier provides 10,000 quota units per day, which is enough for most personal use. No billing account is required.

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
4. Note: Cannot access HttpOnly cookies (SID, HSID, SSID)  - Option 1 is more complete

**Option 3: Manual Upload**

1. Use a cookie export extension (e.g., "Get cookies.txt LOCALLY")
2. Navigate to youtube.com while logged in and export cookies
3. Upload the file via **Settings > Authentication** in the web UI

Cookies expire periodically. Options 1 and 2 handle re-export automatically.

### Settings (via Web UI)

All settings are configurable through the **Settings** page:

- **General**  - Scan schedule (presets + custom cron), default quality, max concurrent downloads, max retries, log level, manual scan trigger, system overview, config import/export
- **Authentication**  - PO token status, cookie upload/status, browser cookie sync (Tampermonkey), YouTube API key, player client strategy
- **Naming**  - Output filename template with live preview and variable reference (`{channel_name}`, `{season}`, `{episode}`, `{title}`, `{upload_date}`, `{video_id}`)
- **yt-dlp**  - Version info, manual update trigger
- **Anti-Detection**  - Download delay range, jitter toggle, user-agent rotation, max video duration filter (for skipping livestreams and long videos)
- **Media Management**  - Subtitle downloads, chapter embedding toggle
- **Notifications**  - Telegram, Pushover, and Discord configuration with test buttons, per-event toggles

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

ChannelHoarder exposes a REST API under `/api/v1/` plus a WebSocket for live progress. See [docs/API.md](docs/API.md) for the full endpoint list, or browse the interactive Swagger UI at `/docs` on a running instance.

## Known Limitations

- **YouTube Shorts**  - Videos ≤30 seconds are skipped by default (configurable per channel)
- **Livestreams / long videos**  - Videos over the max duration setting are skipped by default
- **Geo-blocked content**  - Cannot download region-restricted videos
- **Private / deleted videos**  - Automatically skipped if no longer available
- **No built-in auth**  - Designed for trusted local networks; use a reverse proxy if internet-facing
- **Rate limiting**  - YouTube may throttle requests; the PO token server and configurable delays help mitigate this

## Troubleshooting

**Queue not downloading / stuck**
- Check Settings → Authentication: PO Token Server must show **Running**
- Check Settings → System Logs for error details
- If cookies expired, upload fresh ones via Settings → Authentication

**"Sign in to confirm you're not a bot"**
- Your session has been flagged  - try uploading fresh cookies
- Adding a YouTube Data API key improves reliability
- Wait 1–2 hours if recently rate-limited

**Downloads failing with "Requested format is not available"**
- YouTube restricts certain player clients (DRM-protected or URL-less SABR formats), leaving nothing downloadable
- Set the player client to **Default (automatic)** in Settings → Authentication so yt-dlp picks a working client and adapts to YouTube's changes
- If a specific video still fails, try `ios` or `web_safari` from the same dropdown
- Make sure yt-dlp is up to date (Settings → yt-dlp)

**Plex not showing downloaded videos**
- Ensure your `/downloads` path is added as a **TV Shows** library in Plex
- Trigger a library scan: Plex → Libraries → Scan Library Files
- Check that `PUID`/`PGID` match your Plex user so files are readable

**Port conflict on 8587**
- Change the host-side port: `"8590:8000"`  - then access via `:8590`

**Container writes files as root**
- Ensure `PUID` and `PGID` are set correctly in your compose/template
- The internal port is always `8000`; the host port is configurable

## Support

For bugs or feature requests, open a GitHub issue and include the diagnostic report from **Settings → System → Copy Diagnostic Report**.

## License

MIT License  - free to use, modify, and distribute with attribution. See [LICENSE](LICENSE) for details.
