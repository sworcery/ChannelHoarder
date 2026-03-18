# Changelog

All notable changes to ChannelHoarder will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.26.0] - 2026-03-15

### Added
- **YouTube OAuth2 authentication** — Connect your YouTube account via Google's TV device flow for auto-refreshing tokens that never expire. Eliminates the hourly cookie expiry problem. One-time setup: click "Connect YouTube Account" in Settings, visit the displayed URL, enter the code, done. OAuth takes priority over cookies when configured.
- **OAuth status in Settings** — New OAuth card at top of Authentication settings shows connection status, device code flow UI with copyable code, and disconnect button.
- **OAuth-aware health checks** — Health checks now distinguish between OAuth and cookie auth failures, broadcasting appropriate notifications for each.

## [0.25.1] - 2026-03-15

### Added
- **Auto-import existing files on scan** — Channel scans now automatically check the download directory for video files that match un-downloaded videos. Matched files are moved, renamed, and given full Plex metadata without needing to re-download.
- **Auto-pause queue on cookie expiry** — When cookies are detected as expired (via download failures or health checks), the download queue is automatically paused to prevent spamming YouTube with failing requests. Resume after uploading fresh cookies.

### Fixed
- **Queue pagination blank page** — Switching queue pages briefly showed an empty state while loading. Now keeps previous page data visible until the new page loads.

## [0.25.0] - 2026-03-15

### Added
- **Import existing videos** — Scan a folder of already-downloaded videos, fuzzy-match them by title against known channel videos, then move/rename into the proper directory structure with full Plex-compatible NFO metadata. Available per-channel via "Import Existing" button. Supports configurable match confidence threshold (default 75%).
- **Bulk queue management** — Multi-select queue items with checkboxes (including shift+click range select and select-all). Bulk remove selected items in one action.
- **Queue pagination** — Queue now returns total count and supports pagination. Frontend shows page controls when queue exceeds 50 items, so all queued videos are accessible.

## [0.24.2] - 2026-03-15

### Added
- **Cookie expiration banner** — Global red banner appears across all pages when YouTube cookies expire. Triggered by download auth failures and periodic health checks. Automatically clears when fresh cookies are uploaded. Also broadcasts via WebSocket for instant notification.

## [0.24.1] - 2026-03-15

### Fixed
- **Dashboard/API hang during downloads** — Root cause: `DownloadService` held a single DB session open for the entire download duration (up to 15 min), starving other SQLite connections. Restructured to use three short-lived sessions (pre-download, download with no DB, post-download). Progress broadcasts now use only WebSocket with no DB writes during the download phase.

## [0.24.0] - 2026-03-15

### Fixed
- **Downloads stuck after restart** — Container restarts left phantom "active" queue entries that blocked new downloads for up to 20 minutes. Added startup cleanup that resets all in-flight entries on boot.
- **Queue processor blocking scheduler** — Download execution now runs as a background task (`asyncio.create_task`) instead of blocking the scheduler's 30-second tick for the full download duration (up to 15 min). The scheduler can now monitor stale entries and process new queue items while downloads are in progress.
- **Progress hook event loop** — Fixed `asyncio.get_event_loop()` deprecation in Python 3.12+ by capturing the running loop before entering the download thread.

## [0.23.9] - 2026-03-15

### Fixed
- **Dashboard loading hang** — Dashboard page would load indefinitely when downloads were active. Root cause: SQLite connection pool was limited to 1 connection (`pool_size=1, max_overflow=0`), so the long-running download session blocked all API endpoints. Increased pool to 5 connections + 5 overflow, allowing dashboard queries to proceed concurrently with active downloads via SQLite WAL mode.

## [0.23.8] - 2026-03-15

### Added
- **Queue pause/resume** — Pause button stops new downloads from starting (in-flight downloads finish). Resume button restarts queue processing within 30 seconds.
- **Clear queue** — Remove all queued (non-active) downloads at once with confirmation dialog
- **Queue count badge** — Queue tab shows item count
- **Paused banner** — Yellow banner on Downloads page when queue is paused

### API
- `GET /api/v1/downloads/paused` — Check if queue is paused
- `POST /api/v1/downloads/pause` — Pause the queue
- `POST /api/v1/downloads/resume` — Resume the queue
- `POST /api/v1/downloads/clear-queue` — Clear all non-active queue items

## [0.23.7] - 2026-03-15

### Fixed
- **Cookie file preservation** — yt-dlp was writing back to the original cookies.txt after each request, causing YouTube to gradually invalidate entries (shrinking from 1100+ to 133 entries). Now uses a temporary copy for each yt-dlp session so the original file is never modified.

## [0.23.6] - 2026-03-15

### Changed
- **New pixel art octopus logo** — replaced squirrel with a pixel art octopus grabbing YouTube play buttons with its tentacles
- **Comprehensive README rewrite** — updated features list, added all API endpoints, expanded configuration docs, added screenshot gallery, added cookies documentation

## [0.23.5] - 2026-03-15

### Changed
- **New squirrel logo** — replaced dragon with a cute squirrel hoarding YouTube play buttons

## [0.23.4] - 2026-03-15

### Fixed
- **Downloads still failing with mweb+web** — using both player clients caused yt-dlp to prefer web formats (higher quality but SABR-restricted with no downloadable URL). Now uses mweb only, which has direct stream URLs.

## [0.23.3] - 2026-03-15

### Fixed
- **Downloads failing with "Sign in to confirm you're not a bot"** — default player client changed from `web` to `mweb,web`. YouTube's web client forces SABR streaming (no direct download URLs), causing actual downloads to fail even when metadata extraction works. The mweb client returns 20 formats with direct URLs.

## [0.23.2] - 2026-03-15

### Fixed
- **Auth status endpoint crash** — removed Pydantic response_model that could cause 500 errors, added try-except fallback
- **Settings page shows error details** — if auth status fails, a red error banner with the actual error message is displayed instead of silently showing "Not configured"
- **Cookies Remove button no longer shows when status unknown** — only appears when cookies are confirmed present
- **Loading spinners** for auth status instead of misleading "Checking..." / "Not configured" while loading

## [0.23.1] - 2026-03-15

### Changed
- Doubled sidebar logo size (64px to 128px)

## [0.23.0] - 2026-03-15

### Fixed
- **API key now saves even if validation fails** — previously, network issues during validation would silently discard the key
- **YouTube n-parameter challenge solver** — enabled remote JS challenge solver (`ejs:github`) required by yt-dlp 2026.x to extract video formats
- **Force PO token fetch for every video** — yt-dlp's default `fetch_pot=auto` skips Player PO tokens for web client; now set to `always`
- **Add Node.js as JS runtime** — yt-dlp 2026.x only includes deno by default; explicitly register node
- **Fix deprecated extractor arg** — migrated from `youtube:getpot_bgutil_baseurl` to `youtubepot-bgutilhttp:base_url`
- **Test endpoint no longer starves PO token server** — changed from calling `/get_pot` to `/ping`

### Added
- Auto-scan toggle on Add Channel dialog — scans for videos immediately after adding (on by default)
- Redesigned sidebar logo — larger logo with app name underneath
- Test-download now tries multiple player client strategies (web, mweb, web_creator, no cookies)

## [0.22.0] - 2026-03-15

### Fixed
- **Install missing canvas native libraries** — PO token server's BotGuard VM requires `libcairo2-dev`, `libpango1.0-dev` and other native libs for the `canvas` npm package. These were missing from the Docker image, causing `/get_pot` to hang
- Added `tini` as init process for proper signal handling and zombie process prevention
- Set Node.js memory limit (256MB) for PO token server to prevent OOM kills (known ~25MB/request memory leak)

### Added
- PO token server log capture (`/config/pot-server.log`) for debugging server issues
- Server startup health check — waits up to 30s for PO token server to respond to `/ping`
- New `/api/v1/system/pot-server-log` endpoint to view server logs and check process status

## [0.21.0] - 2026-03-15

### Fixed
- **PO tokens now generated per-video by plugin** — removed manual pre-generation which YouTube rejects (GVS tokens are bound to video IDs)
- Stop injecting generic PO tokens via extractor args; let bgutil-ytdlp-pot-provider plugin handle per-video token generation automatically via its HTTP provider
- Fixed bgutil script paths in Docker — added symlink from `/opt/pot-provider` to `/root/bgutil-ytdlp-pot-provider` so the plugin's script-node provider can find its files
- Simplified test-download endpoint to test plugin-based token generation

### Removed
- Manual PO token fetching/caching (`_fetch_po_token()`) — plugin handles this now

## [0.20.0] - 2025-03-15

### Fixed
- URL-decode visitor_data from PO token server (was passing URL-encoded base64)
- Enhanced test-download to isolate variables: tests with/without cookies, with/without PO token, bare minimum
- Captures yt-dlp verbose output for debugging why PO tokens aren't recognized

## [0.19.0] - 2025-03-15

### Fixed
- **Direct PO token injection** — bypasses broken bgutil yt-dlp plugin by fetching tokens directly from the PO token server and injecting them via `po_token` extractor arg
- PO tokens are cached for 5 minutes to avoid hammering the server
- Test endpoint now shows active extractor_args to verify PO token is being passed

## [0.18.0] - 2025-03-15

### Fixed
- PO token server running but not generating tokens — test endpoint now probes multiple server API endpoints to find the issue
- Default player client changed from hardcoded "web" to yt-dlp's built-in default (which may handle auth better)

### Added
- Configurable player client strategy (`YTDLP_PLAYER_CLIENT` env var or API)
- Test-download endpoint now tries 6 different player client strategies and reports which ones work
- `PUT /api/v1/auth/player-client` endpoint to switch strategy without rebuilding
- Player client setting persists across container restarts

## [0.17.0] - 2025-03-15

### Fixed
- **ROOT CAUSE: PO token server was not connected to yt-dlp** — the bgutil plugin was installed but never told the server URL, so PO tokens were never generated, causing all downloads to fail with AUTH_EXPIRED / "Sign in to confirm you're not a bot"
- Added `getpot_bgutil_baseurl` to yt-dlp extractor_args so the PO token plugin actually connects to the running server

### Added
- Test-download endpoint now checks PO token generation and plugin installation status

## [0.16.0] - 2025-03-15

### Fixed
- Downloads getting stuck in queue with empty progress bar — added 15-minute timeout per download
- Stale queue entries (>20 min) now automatically unstick and retry
- Added diagnostic logging showing which auth method (cookies/PO token) is active during downloads
- yt-dlp errors no longer suppressed — warnings are now logged for debugging

### Added
- `/api/v1/system/test-download` endpoint for diagnosing download auth issues (checks cookies format, PO token server, stream access)

## [0.15.0] - 2025-03-15

### Fixed
- Unraid template: added optional extra media path mapping that persists across container updates
- Unraid template: improved descriptions for download directory and timezone settings

## [0.14.0] - 2025-03-15

### Fixed
- Storage calculation now includes per-channel custom download directories (e.g. /cartoons)
- Dashboard storage display was only counting default /downloads directory

## [0.13.0] - 2025-03-15

### Fixed
- Download queue processing crash: MissingGreenlet error from lazy-loaded SQLAlchemy relationship
- Downloads now actually process from the queue instead of silently failing

## [0.12.0] - 2025-03-15

### Fixed
- API key validation now shows actual YouTube error message instead of generic "Invalid API key"
- API key persisted to config directory so it survives container restarts

## [0.11.0] - 2025-03-15

### Added
- YouTube RSS feed as upload date source (free, no auth, covers ~15 most recent videos)
- Three-tier date resolution: YouTube Data API > RSS feed > per-video yt-dlp > fallback

### Fixed
- Videos no longer show scan date instead of actual YouTube upload date
- Upload date accuracy greatly improved even without a YouTube API key

## [0.10.0] - 2025-03-15

### Fixed
- Channel scanning no longer hammers YouTube with per-video requests when bot-detected
- Consecutive metadata fetch failures (3+) trigger automatic skip to prevent scan timeouts
- Remaining videos use flat extraction data with fallback dates instead of failing

## [0.9.0] - 2025-03-15

### Added
- Per-channel custom download directory (set when adding or edit later from channel settings)
- SQLite WAL mode with busy timeout for reliable concurrent access

### Fixed
- "Database is locked" error when adding channels while background tasks run

## [0.8.0] - 2025-03-15

### Fixed
- Upload dates now fetched from YouTube per-video instead of defaulting to scan date
- Two-pass scanning: flat extraction for speed, then per-video metadata for accuracy

### Added
- Plex-compatible NFO metadata generation (tvshow.nfo per channel, episode .nfo per video)
- Automatic channel poster download (poster.jpg) for Plex artwork
- Toast notification system for user feedback on all actions
- Anti-detection settings tab fully wired to backend (save/load delays, jitter, UA rotation)
- Naming template save/load from backend settings
- Dragon logo in sidebar
- Dynamic version display in sidebar (fetched from API)

### Changed
- All mutations across pages now show success/error toasts

## [0.7.0] - 2025-03-15

### Fixed
- Channel scanning now discovers videos even when upload date is unavailable from flat extraction
- Copy Diagnostic Report button now works on non-HTTPS connections (e.g. local network IPs)

## [0.6.0] - 2025-03-15

### Fixed
- Timezone display now reflects the user's local timezone instead of UTC
- All API datetime fields include timezone info for correct browser conversion

## [0.5.0] - 2025-03-15

### Fixed
- PO token server startup (migrated from deprecated `deno task` to Node.js build)

### Changed
- Dark mode is now the default theme (no white flash on load)
- Replaced placeholder logo with dragon-hoarding-play-buttons icon

## [0.4.0] - 2025-03-15

### Changed
- Default host port changed from 8000 to 8587 to avoid conflicts with common services

## [0.3.0] - 2025-03-15

### Added
- GitHub Actions CI/CD workflow for automated Docker image builds
- Docker image published to GitHub Container Registry (ghcr.io)

### Changed
- Unraid template and docker-compose updated to pull from ghcr.io registry
- Docker Compose uses pre-built image by default instead of local build

## [0.2.0] - 2025-03-15

### Added
- Comprehensive README with feature list, file organization examples, and setup instructions
- Error handling documentation with all error codes and auto-recovery info
- Full API endpoint reference

## [0.1.0] - 2025-03-15

### Added
- Initial release
- FastAPI backend with async SQLAlchemy and SQLite
- React 18 frontend with TypeScript, Tailwind CSS, and shadcn/ui
- Automatic channel scanning with configurable schedule
- Plex-compatible TV Show naming (Channel/Season Year/S####E### format)
- Per-channel quality settings (best, 1080p, 720p, 480p)
- Zero-cookie authentication via PO tokens (bgutil-ytdlp-pot-provider)
- Optional YouTube Data API v3 integration for reliable channel discovery
- Real-time download progress via WebSocket
- Error diagnostics with classification, explanations, and suggested fixes
- Channel health indicators (green/yellow/red)
- Copy Diagnostic Report button for troubleshooting
- Anti-detection features (configurable delays, jitter, user-agent rotation)
- Single Docker container deployment with multi-stage build
- Unraid template for easy installation
- Dark/light mode toggle with persistent preference
