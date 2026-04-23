# Changelog

All notable changes to ChannelHoarder will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.7.30] - 2026-04-22

### Changed
- **Async file I/O throughout** - All blocking file operations (delete, move, rename, renumber, chmod/chown) now run via `asyncio.to_thread` to avoid stalling the event loop, especially on NFS/SMB mounts.
- **Centralized sidecar file handling** - Single `ASSOCIATED_EXTENSIONS` constant and `move_video_files()`/`delete_video_files()` helpers replace duplicated file+sidecar logic across 13+ call sites. All sidecar types (.nfo, -thumb.jpg, .jpg, .info.json, .en.vtt, .en.srt, .en.ass) are now consistently handled everywhere.
- **Typed API client** - Replaced all 58 `any` types in the frontend API client with proper TypeScript generics and interfaces.
- **Settings export/import completeness** - Export now includes all channel fields (platform, quality_cutoff, min_video_duration, include_shorts, include_livestreams, auto_download) so round-trip import no longer loses data.

### Fixed
- **WebSocket origin validation** - WebSocket endpoint now checks the `Origin` header against `CORS_ORIGINS`, rejecting connections from unauthorized origins.
- **Move tasks corrupting file paths** - Move Files and Move All were updating the database path even when the source file didn't exist, silently pointing records at non-existent locations.
- **Subtitle download base path** - `os.path.splitext()` replaces `rsplit(".", 1)` throughout, fixing incorrect base paths when directory names contain dots.
- **Rename sidecar coverage** - `_rename_existing_files` now moves all 7 sidecar types instead of only 3, and no longer crashes on `-thumb.jpg` suffix.
- **Subtitle notification spam** - Progress notification now fires once after all videos are processed instead of after every individual video.
- **chmod/chown sidecar coverage** - Permission setting now applies to all sidecar types including subtitle files (.en.vtt, .en.srt, .en.ass).
- **Sidecar move error handling** - Failed sidecar moves are logged and skipped instead of aborting the entire operation.
- **Stale download timeout** - Bumped stuck download cutoff from 20 to 25 minutes to reduce false positives on large files.

### Removed
- **`check_schedule` field** - Dead column removed from the Channel model, schema, and TypeScript types.

## [1.7.29] - 2026-04-17

### Added
- **Per-channel randomized scan scheduling** - Each channel gets its own random scan time instead of all channels scanning at once. Configurable scan window (e.g. 10 PM - 6 AM) with minimum 8-hour width. Channels display their next scheduled scan time.
- **Scan rate limits** - Minimum hours between auto-scans (default 12) and manual "Scan Now" cooldown (default 5 min) prevent excessive scanning.
- **Auto-reclassify shorts and livestreams** - Every scan re-checks existing videos against YouTube's /shorts and /streams tabs. Newly identified shorts/livestreams are automatically cleaned up (files deleted, episodes renumbered) when those categories are disabled.
- **Scan jitter** - Randomized delays between channel scans and randomized scan order to avoid predictable traffic patterns.
- **Download subtitles for existing videos** - "Download Missing Subtitles" button fetches subtitles for previously downloaded videos without re-downloading them.
- **Standalone downloads organized by uploader** - Standalone video downloads now create proper per-uploader channels in the main download directory instead of a flat folder.

### Changed
- **Settings reorganized (Sonarr-style)** - Restructured from 6 tabs to 6 purpose-driven tabs: General (system info, yt-dlp, backup/restore), Media Management (naming, shorts, livestreams, subtitles, permissions), Authentication, Anti-Detection, Connect, Diagnostics.
- **Diagnostics moved into Settings** - No longer a separate sidebar page; now a tab within Settings with sub-tabs for Overview, Error Logs, and System Logs.
- **Quality target is the cutoff** - Removed separate "Quality Cutoff" field per channel. The channel's Quality setting now serves as both the download target and upgrade cutoff.
- **Advanced options collapsible** - Shorts, livestreams, and subtitles management on channel detail page hidden behind an "Advanced Options" toggle.

### Fixed
- **Null dereference crash** in refresh_channel_metadata when yt-dlp returns None.
- **Reclassification race condition** - No longer deletes files for videos that are actively downloading.
- **Settings query performance** - Batch-read all settings once at scan start instead of 2 DB queries per video (eliminates 400+ round-trips for large channel scans).
- **Raw sqlite3 connection** removed from subtitle check; setting now read asynchronously and passed as parameter.
- **Path validation enforced** - `validate_download_path` now actually checks `allowed_roots` containment (was accepting the parameter but never using it).
- **Move file endpoints validated** - Move Files and Move All now validate the target directory against allowed roots.
- **Per-channel DB sessions** in scheduled scans prevent rollback contamination and connection starvation during long scan runs with jitter.
- **Shorts false positive** - No longer marks videos as shorts when duration is unknown and file is small.

## [1.7.28] - 2026-04-14

### Added
- **Minimum auto-scan interval** - New setting guarantees a minimum number of hours between automatic scans of the same channel (default 12). Randomization can no longer roll a near-future scan time. Configurable in Anti-Detection settings.
- **Scan Now cooldown** - Manual "Scan Now" button is rate limited by a configurable cooldown (default 5 minutes). Clicking during cooldown returns a 429 response with the remaining wait time. Set to 0 to disable.

### Changed
- **Scan scheduling always resets clock** - Manual scans via "Scan Now" re-roll the channel's `next_scan_at` just like automatic scans, so a manual trigger doesn't leave a scan pending right behind it.

## [1.7.27] - 2026-04-14

### Changed
- **Per-channel randomized scan scheduling** - Replaced the fixed daily cron with a per-channel scheduling model. Each channel now has its own `next_scan_at` timestamp, and a background tick every 10 minutes scans any channel whose time has arrived. Scans spread across the 24 hour cycle instead of firing in a burst at 3 AM local time.
- **Configurable scan window** - New setting in Anti-Detection to constrain scans to a specific daily window (e.g. 10 PM to 6 AM). Minimum window width of 8 hours is enforced to avoid clustering. Channels scan once per day, with a random slot picked inside the window.
- **Next scan display** - Channel detail page now shows the next scheduled scan time alongside the last scan.

### Migrated
- Existing channels without a `next_scan_at` value are staggered across the next 24 hours on first startup (deterministic per-channel offset based on channel id).
- `global_schedule_cron` setting is retained for backwards compatibility but is no longer read by the scheduler.

## [1.7.26] - 2026-04-13

### Added
- **Auto-reclassify existing videos on every scan** - The scan now re-checks each existing video against the current `/shorts` and `/streams` tab data. Videos that were misclassified (scanned before tab detection, moved between tabs by the creator, etc.) get their flags corrected automatically. If shorts/livestreams are not allowed for the channel, the files are automatically deleted and episodes renumbered.
- **Scan jitter** - Added randomized delays between channel scans during scheduled runs to avoid predictable traffic patterns that YouTube bot detection can correlate. Channel scan order is also randomized. Configurable in Settings > Anti-Detection (default: 5 minute max jitter).

### Changed
- **Zero-touch shorts/livestream cleanup** - Users no longer need to click "Detect Shorts" or "Detect & Clean" manually. Every scheduled scan (or manual scan) now handles reclassification and cleanup automatically. Heuristics (title, duration, live_status) remain as fallback signals for videos that slip through the tab detection.
- **`_renumber_channel_episodes` moved** to `app/utils/renumber.py` so both the scan service and router endpoints can use it.

## [1.7.25] - 2026-04-13

### Changed
- **Quality target is the cutoff** - Removed the separate "Quality Cutoff" field per channel. The channel's Quality setting now acts as both the download target and the upgrade cutoff. "Search Upgrades" button moved next to the Quality dropdown. Upgrades are skipped when quality is set to "Best Available".
- **Min Duration field moved to Advanced** - The shorts threshold field is now inside the Shorts Management section (under the Advanced toggle) and labeled "Shorts Threshold" for clarity.

## [1.7.24] - 2026-04-13

### Fixed
- **Build failure from v1.7.23** - JSX fragment in Advanced toggle spanned across a div boundary. Each section is now gated individually.

## [1.7.23] - 2026-04-13

### Changed
- **Advanced options collapsible** - Shorts, Livestreams, and Subtitles sections on the channel detail page are now hidden by default behind an "Advanced Options" toggle. Preference is persisted in localStorage.

## [1.7.22] - 2026-04-13

### Fixed
- **TypeScript build error in v1.7.21** - Livestream API client types were missing the `message` field that the UI code accessed.

## [1.7.21] - 2026-04-13

### Added
- **Tab-based shorts and livestream detection** - YouTube channels are now scanned via their separate `/videos`, `/shorts`, and `/streams` tabs. This is far more reliable than previous heuristic detection, since YouTube itself tells us which category each video belongs to.
- **Livestream filter** - Livestreams are now excluded from downloads by default. Global toggle in Settings mirrors the shorts toggle, with per-channel opt-in. Scheduled livestreams that fail with "will begin in a few moments" are automatically flagged and unmonitored to stop retry loops.
- **LIVESTREAM_SCHEDULED error code** - Scheduled livestream errors now have their own error code instead of UNKNOWN, with a clearer message and no retries.
- **Manual Mark as Livestream** - Per-episode dropdown option to mark or unmark a video as a livestream.
- **Detect Livestreams / Delete Downloaded Livestreams** - Channel detail page buttons mirroring the shorts management UI.

### Changed
- **Livestreams excluded from episode numbering** - Livestreams now get episode 0 and are skipped during renumbering, matching the existing shorts behavior.

## [1.7.20] - 2026-04-13

### Fixed
- **React error #310 on channel detail page** - Bulk delete mutation hook was declared after an early return, causing hook count mismatch between renders. Moved above the early return.

## [1.7.19] - 2026-04-12

### Added
- **First/Last page buttons** - Channel video pagination now has skip-to-first and skip-to-last buttons alongside previous/next.
- **Bulk delete** - Select multiple videos and delete them (with files) in one action from the bulk action bar.
- **Manual mark as short** - Per-episode dropdown menu option to manually mark or unmark a video as a short.

### Changed
- **Shorts excluded from episode numbering** - Shorts now get episode 0 and are excluded from sequential episode numbering. Renumbering also skips shorts.
- **Improved shorts detection** - Detect Shorts now checks for #shorts/#short in the video title and considers small file size, not just duration. Fixes detection for videos with missing duration data.

### Fixed
- **Shorts not detected when duration is null** - Videos discovered via YouTube API or flat extraction often have no duration data. Previously these were silently skipped. Now title-based and file-size-based detection catches them.

## [1.7.18] - 2026-04-12

### Changed
- **Standalone downloads organized by uploader** - Standalone video downloads now create a proper channel record for the video's uploader and place files in the main download directory using the standard channel/season/episode folder structure, instead of dumping everything into a flat standalone folder.
- **Reorganize legacy standalone downloads** - New "Reorganize" button on the Standalone Downloads page migrates existing flat standalone downloads into proper per-uploader channel folders. Fetches uploader info for each video and moves files to the correct location.
- **4K quality option for standalone downloads** - Added 2160p/4K to the standalone download quality selector.

## [1.7.17] - 2026-04-11

### Added
- **Download subtitles for existing videos** - New "Download Missing Subtitles" button on channel detail page fetches English subtitles and auto-generated captions for all completed videos without re-downloading the video. Also available per-episode from the dropdown menu. Runs in the background for bulk operations.

## [1.7.16] - 2026-04-11

### Fixed
- **Startup crash on fresh installs** - Orphan cleanup referenced wrong table name (`download_logs` instead of `download_log`), preventing database initialization.

## [1.7.15] - 2026-04-11

### Fixed
- **Orphan videos blocking channel re-add** - When a channel was deleted and re-added, orphan video records from the old channel (left behind before the foreign key cascade fix) prevented all videos from being detected. Scan now claims orphan records for the new channel instead of silently skipping them.
- **Orphan cleanup on startup** - Automatically removes video records whose parent channel no longer exists, preventing stale data from accumulating across upgrades.

## [1.7.14] - 2026-04-11

### Fixed
- **Concurrent scan race condition** - When a manual scan and scheduled scan run at the same time on the same channel, the second scan would crash with an IntegrityError trying to insert duplicate videos. Now uses a scan lock to prevent concurrent scans and gracefully handles any remaining duplicate insertions.

## [1.7.13] - 2026-04-11

### Added
- **File move preview** - Save & Move and Move All now show a preview with file count, total size, and per-channel breakdown before moving anything
- **Detect & Clean shorts** - Combined button that detects shorts below threshold, deletes their files, and renumbers remaining episodes in one action
- **Force Re-scan** - Button to delete all video records for a stuck channel and re-scan from scratch, recovering from orphan record issues
- **Playlist support fix** - Playlist URLs no longer return "could not find channel" error (uses different extraction method for playlists)

### Changed
- **Shorts threshold lowered** - Default shorts detection threshold changed from 60s to 30s; per-channel minimum duration setting is used when configured
- **Cookie preservation on auth failure** - Cookie file is no longer auto-deleted when auth expires; instead it's flagged and the queue is paused, preventing the Tampermonkey cookie de-auth loop
- **File move reliability** - Move tasks now use DB file_path records as source of truth instead of guessing directory names; channel download_dir is updated after move completes (not before), eliminating race conditions
- **Subtitle limitation note** - Settings page now notes that PO token authentication has limited subtitle/caption support

### Fixed
- **File move race condition** - Channel download_dir was being updated before the background move task ran, causing path mismatches
- **Move All captures old directories** - All channel directories are now captured before any changes, preventing incorrect path replacements during bulk moves

## [1.5.7] - 2026-04-08

### Fixed
- **Queue stuck after successful download** - Post-download Phase 3 was not wrapped in error handling. If any exception occurred while recording completion (e.g. NFO write, DB update), the queue entry was never deleted, blocking all future downloads. Now has a fallback cleanup that always removes the queue entry even if recording fails.

## [1.5.6] - 2026-04-08

### Changed
- **Smart auth strategy** - When valid cookies are present, downloads use cookies as primary auth and skip PO token generation entirely. PO tokens are only used as a fallback when no cookies are available. This eliminates the BotGuard VM hanging issue for users with the cookie exporter running.

## [1.5.5] - 2026-04-08

### Fixed
- **PO token server runs as root** - BotGuard VM requires root privileges to function. The v1.5.0 PUID/PGID change caused it to run as appuser, which made token generation hang indefinitely. PO token server now runs as root while uvicorn still runs as appuser for file ownership.

## [1.5.4] - 2026-04-08

### Fixed
- **Download timeout triggers PO token server restart** - When a download times out (usually because the PO token server is hung), the watchdog now restarts the Node process automatically and the error message tells you to retry
- **"Unknown error" now shows the actual error** - UNKNOWN errors in the UI and diagnostic report now include the raw error message instead of just "Unknown error"
- **PO token timeout detection** - Downloads that time out waiting for a PO token are now classified as PO_TOKEN_FAILURE instead of NETWORK_ERROR

## [1.5.3] - 2026-04-08

### Added
- **PO token server watchdog** - Monitors the PO token server every 5 minutes by testing actual token generation (not just /ping). Automatically kills and restarts the Node.js process if it hangs, which resolves the "Waiting for download to begin" stuck state without requiring a full container restart.

## [1.5.0] - 2026-04-06

### Security
- **Path traversal fix**  - Validate all user-supplied filesystem paths (import scan/confirm, download dirs, channel create/update) against allowed roots using `validate_download_path()`
- **Settings export no longer leaks credentials**  - Telegram tokens, Pushover keys, and API keys are excluded from the export endpoint
- **Naming template injection prevention**  - Templates are validated against a whitelist of allowed variables; attribute access and indexing are rejected
- **Typed cookie push endpoint**  - Cookie push now uses a Pydantic model with size limits instead of untyped `dict`

### Fixed
- **Blocking async calls**  - `httpx.get()` and `subprocess.run()` in system endpoints now use async patterns instead of blocking the event loop
- **N+1 queries**  - `bulk_skip_videos` and `retry_all_failed` now batch-load queue entries in a single query instead of per-video SELECTs
- **Scan performance**  - Episode numbering during channel scans pre-fetches counts per season instead of running a COUNT query per video
- **NFS/SMB mount support**  - `chown` on `/downloads` and `/cookies` volumes is now non-fatal, preventing container startup failures on network mounts
- **Health check is now real**  - The `/health` endpoint pings the database instead of returning a static "healthy" response
- **WebSocket reconnect race**  - Added mounted-ref guard to prevent ghost connections after navigation
- **Missing cache invalidation**  - Channel video statuses now update after downloads complete
- **Standalone download page**  - Replaced DOM query with controlled React input; fixed WebSocket re-subscription dropping progress messages
- **Confirm dialogs work in Unraid iframes**  - Replaced `window.confirm()` with custom modal component
- **Error boundary**  - Added React ErrorBoundary so crashes show a friendly error with reload button instead of a blank white screen
- **Channel sort performance**  - Memoized sort computation on the channels page

### Improved
- **Code deduplication**  - Consolidated `_format_bytes` (3 copies), `escape_like` (6 inline copies), `_parse_upload_date` (2 copies), cookie expiry flagging (2 copies) into shared utilities
- **Dead code removed**  - `BulkMoveRequest` schema, `_safe_dirname` (replaced with `sanitize_filename`), dead variable assignments, redundant session close
- **Deprecated API replaced**  - `asyncio.ensure_future` replaced with `asyncio.create_task`
- **Circular import fixed**  - Settings router no longer imports from `app.main`; uses `request.app.state` instead
- **Health log cleanup**  - `system_health_log` table is trimmed to 7 days on startup
- **Composite index**  - Added `(status, downloaded_at)` index on videos table for faster history queries
- **CI pipeline**  - Added Python lint (Ruff) and TypeScript check before Docker build
- **README**  - Added missing API endpoints to documentation

## [1.4.4] - 2026-04-06

### Fixed
- **Database creation fails on fresh install**  - Models were not imported before `create_all`, so tables were never created on first run. The migration code then tried to ALTER non-existent tables, crashing the container on startup.

## [1.4.3] - 2026-04-05

### Fixed
- Unraid template: added PNG logo, support thread URL, TemplateURL, Project, /cookies volume, and Mask on API key field

## [1.4.2] - 2026-04-05

### Added
- MIT License with attribution requirement

### Fixed
- PUID/PGID now correctly applied in Docker entrypoint  - downloaded files are owned by the configured user instead of root (critical fix for Unraid/TrueNAS)
- `/cookies` volume mount added to docker-compose.yml example
- Removed deprecated `version: "3.8"` key from docker-compose.yml
- Removed dead `move-to-front` and `bulk-move-to-front` API endpoints left over from v1.4.1
- Removed `console.log` and `console.warn` debug statements from frontend
- Added `error_details` field to TypeScript `Video` interface

## [1.4.1] - 2026-03-28

### Changed
- Simplified queue controls  - removed move-to-front button, keeping only download-now for direct queue bypass

## [1.4.0] - 2026-03-27

### Added
- **Queue controls**  - Move items to front of queue, download immediately bypassing queue order, set custom priority per item. Available as per-item buttons (lightning bolt for download now, arrow for move to front) and bulk actions for selected items.
- **Queue position numbers**  - Each queued item now shows its position in the queue.
- **Inline download progress on standalone page**  - After submitting a video URL, progress bar, speed, ETA, and completion status are shown directly on the page instead of requiring navigation to the queue. Multiple videos can be tracked simultaneously.

### Fixed
- **Standalone video download broken**  - Missing `date` import in downloads router caused 500 Internal Server Error when downloading any video by URL.

## [1.3.8] - 2026-03-22

### Security
- **Path traversal fix**  - SPA catch-all route now validates resolved paths stay under static directory
- **CORS fix**  - Disable credentials with wildcard origins to prevent cross-origin attacks
- **URL validation**  - Reject `file://`, `ftp://`, and other unsafe URL schemes passed to yt-dlp
- **ILIKE escaping**  - Escape `%` and `_` wildcard characters in all search queries

### Fixed
- **Settings export/import unreachable**  - Route ordering bug where `/{key}` shadowed `/export` and `/import`
- **Event loop blocking**  - yt-dlp update, health check, and PO token server log now run in threads
- **Retry all failed**  - Now clears `error_details` like single retry does
- **Error misclassification**  - Tightened broad substring matching for "blocked", "pot", "update"
- **Hardcoded YouTube URLs**  - Downloads page now uses platform-aware video URLs (Rumble, Twitch, etc.)
- **video_id column too narrow**  - Widened from 16 to 128 chars for non-YouTube platform IDs
- **SCAN_FAILED missing from ErrorCode enum**  - Added to prevent lookup failures
- **Frontend type mismatches**  - Added `cookies_expired`, `platform` to TypeScript interfaces

### Added
- **Path validation utility**  - `validate_download_path()` and `validate_url_scheme()` in file_utils
- **`buildVideoUrl` frontend utility**  - Platform-aware video URL construction

### Removed
- Dead `get_db()` duplicate in database.py
- Unused imports (`date`, `and_`, `datetime`, `XCircle`, `RefreshCw`)

## [1.3.5] - 2026-03-21

### Improved
- **Delete Shorts confirmation modal**  - Clicking "Delete Downloaded Shorts" now opens an in-page modal showing exactly which shorts will be removed, with title, duration, file path, file size, and total size. Requires explicit confirmation before any files are deleted.

## [1.3.3] - 2026-03-21

### Fixed
- **Shorts detect/delete always available**  - Detect Shorts and Delete Downloaded Shorts buttons are now always visible on the channel detail page regardless of the global shorts toggle. The global setting only controls whether shorts are *downloaded*, not whether you can identify and remove them.
- **Error details visible on channel detail page**  - Failed videos in the channel video list now show the error message directly under the status badge, so you can see why a download failed without navigating to the Downloads page.

## [0.26.0] - 2026-03-15

### Added
- **YouTube OAuth2 authentication**  - Connect your YouTube account via Google's TV device flow for auto-refreshing tokens that never expire. Eliminates the hourly cookie expiry problem. One-time setup: click "Connect YouTube Account" in Settings, visit the displayed URL, enter the code, done. OAuth takes priority over cookies when configured.
- **OAuth status in Settings**  - New OAuth card at top of Authentication settings shows connection status, device code flow UI with copyable code, and disconnect button.
- **OAuth-aware health checks**  - Health checks now distinguish between OAuth and cookie auth failures, broadcasting appropriate notifications for each.

## [0.25.1] - 2026-03-15

### Added
- **Auto-import existing files on scan**  - Channel scans now automatically check the download directory for video files that match un-downloaded videos. Matched files are moved, renamed, and given full Plex metadata without needing to re-download.
- **Auto-pause queue on cookie expiry**  - When cookies are detected as expired (via download failures or health checks), the download queue is automatically paused to prevent spamming YouTube with failing requests. Resume after uploading fresh cookies.

### Fixed
- **Queue pagination blank page**  - Switching queue pages briefly showed an empty state while loading. Now keeps previous page data visible until the new page loads.

## [0.25.0] - 2026-03-15

### Added
- **Import existing videos**  - Scan a folder of already-downloaded videos, fuzzy-match them by title against known channel videos, then move/rename into the proper directory structure with full Plex-compatible NFO metadata. Available per-channel via "Import Existing" button. Supports configurable match confidence threshold (default 75%).
- **Bulk queue management**  - Multi-select queue items with checkboxes (including shift+click range select and select-all). Bulk remove selected items in one action.
- **Queue pagination**  - Queue now returns total count and supports pagination. Frontend shows page controls when queue exceeds 50 items, so all queued videos are accessible.

## [0.24.2] - 2026-03-15

### Added
- **Cookie expiration banner**  - Global red banner appears across all pages when YouTube cookies expire. Triggered by download auth failures and periodic health checks. Automatically clears when fresh cookies are uploaded. Also broadcasts via WebSocket for instant notification.

## [0.24.1] - 2026-03-15

### Fixed
- **Dashboard/API hang during downloads**  - Root cause: `DownloadService` held a single DB session open for the entire download duration (up to 15 min), starving other SQLite connections. Restructured to use three short-lived sessions (pre-download, download with no DB, post-download). Progress broadcasts now use only WebSocket with no DB writes during the download phase.

## [0.24.0] - 2026-03-15

### Fixed
- **Downloads stuck after restart**  - Container restarts left phantom "active" queue entries that blocked new downloads for up to 20 minutes. Added startup cleanup that resets all in-flight entries on boot.
- **Queue processor blocking scheduler**  - Download execution now runs as a background task (`asyncio.create_task`) instead of blocking the scheduler's 30-second tick for the full download duration (up to 15 min). The scheduler can now monitor stale entries and process new queue items while downloads are in progress.
- **Progress hook event loop**  - Fixed `asyncio.get_event_loop()` deprecation in Python 3.12+ by capturing the running loop before entering the download thread.

## [0.23.9] - 2026-03-15

### Fixed
- **Dashboard loading hang**  - Dashboard page would load indefinitely when downloads were active. Root cause: SQLite connection pool was limited to 1 connection (`pool_size=1, max_overflow=0`), so the long-running download session blocked all API endpoints. Increased pool to 5 connections + 5 overflow, allowing dashboard queries to proceed concurrently with active downloads via SQLite WAL mode.

## [0.23.8] - 2026-03-15

### Added
- **Queue pause/resume**  - Pause button stops new downloads from starting (in-flight downloads finish). Resume button restarts queue processing within 30 seconds.
- **Clear queue**  - Remove all queued (non-active) downloads at once with confirmation dialog
- **Queue count badge**  - Queue tab shows item count
- **Paused banner**  - Yellow banner on Downloads page when queue is paused

### API
- `GET /api/v1/downloads/paused`  - Check if queue is paused
- `POST /api/v1/downloads/pause`  - Pause the queue
- `POST /api/v1/downloads/resume`  - Resume the queue
- `POST /api/v1/downloads/clear-queue`  - Clear all non-active queue items

## [0.23.7] - 2026-03-15

### Fixed
- **Cookie file preservation**  - yt-dlp was writing back to the original cookies.txt after each request, causing YouTube to gradually invalidate entries (shrinking from 1100+ to 133 entries). Now uses a temporary copy for each yt-dlp session so the original file is never modified.

## [0.23.6] - 2026-03-15

### Changed
- **New pixel art octopus logo**  - replaced squirrel with a pixel art octopus grabbing YouTube play buttons with its tentacles
- **Comprehensive README rewrite**  - updated features list, added all API endpoints, expanded configuration docs, added screenshot gallery, added cookies documentation

## [0.23.5] - 2026-03-15

### Changed
- **New squirrel logo**  - replaced dragon with a cute squirrel hoarding YouTube play buttons

## [0.23.4] - 2026-03-15

### Fixed
- **Downloads still failing with mweb+web**  - using both player clients caused yt-dlp to prefer web formats (higher quality but SABR-restricted with no downloadable URL). Now uses mweb only, which has direct stream URLs.

## [0.23.3] - 2026-03-15

### Fixed
- **Downloads failing with "Sign in to confirm you're not a bot"**  - default player client changed from `web` to `mweb,web`. YouTube's web client forces SABR streaming (no direct download URLs), causing actual downloads to fail even when metadata extraction works. The mweb client returns 20 formats with direct URLs.

## [0.23.2] - 2026-03-15

### Fixed
- **Auth status endpoint crash**  - removed Pydantic response_model that could cause 500 errors, added try-except fallback
- **Settings page shows error details**  - if auth status fails, a red error banner with the actual error message is displayed instead of silently showing "Not configured"
- **Cookies Remove button no longer shows when status unknown**  - only appears when cookies are confirmed present
- **Loading spinners** for auth status instead of misleading "Checking..." / "Not configured" while loading

## [0.23.1] - 2026-03-15

### Changed
- Doubled sidebar logo size (64px to 128px)

## [0.23.0] - 2026-03-15

### Fixed
- **API key now saves even if validation fails**  - previously, network issues during validation would silently discard the key
- **YouTube n-parameter challenge solver**  - enabled remote JS challenge solver (`ejs:github`) required by yt-dlp 2026.x to extract video formats
- **Force PO token fetch for every video**  - yt-dlp's default `fetch_pot=auto` skips Player PO tokens for web client; now set to `always`
- **Add Node.js as JS runtime**  - yt-dlp 2026.x only includes deno by default; explicitly register node
- **Fix deprecated extractor arg**  - migrated from `youtube:getpot_bgutil_baseurl` to `youtubepot-bgutilhttp:base_url`
- **Test endpoint no longer starves PO token server**  - changed from calling `/get_pot` to `/ping`

### Added
- Auto-scan toggle on Add Channel dialog  - scans for videos immediately after adding (on by default)
- Redesigned sidebar logo  - larger logo with app name underneath
- Test-download now tries multiple player client strategies (web, mweb, web_creator, no cookies)

## [0.22.0] - 2026-03-15

### Fixed
- **Install missing canvas native libraries**  - PO token server's BotGuard VM requires `libcairo2-dev`, `libpango1.0-dev` and other native libs for the `canvas` npm package. These were missing from the Docker image, causing `/get_pot` to hang
- Added `tini` as init process for proper signal handling and zombie process prevention
- Set Node.js memory limit (256MB) for PO token server to prevent OOM kills (known ~25MB/request memory leak)

### Added
- PO token server log capture (`/config/pot-server.log`) for debugging server issues
- Server startup health check  - waits up to 30s for PO token server to respond to `/ping`
- New `/api/v1/system/pot-server-log` endpoint to view server logs and check process status

## [0.21.0] - 2026-03-15

### Fixed
- **PO tokens now generated per-video by plugin**  - removed manual pre-generation which YouTube rejects (GVS tokens are bound to video IDs)
- Stop injecting generic PO tokens via extractor args; let bgutil-ytdlp-pot-provider plugin handle per-video token generation automatically via its HTTP provider
- Fixed bgutil script paths in Docker  - added symlink from `/opt/pot-provider` to `/root/bgutil-ytdlp-pot-provider` so the plugin's script-node provider can find its files
- Simplified test-download endpoint to test plugin-based token generation

### Removed
- Manual PO token fetching/caching (`_fetch_po_token()`)  - plugin handles this now

## [0.20.0] - 2025-03-15

### Fixed
- URL-decode visitor_data from PO token server (was passing URL-encoded base64)
- Enhanced test-download to isolate variables: tests with/without cookies, with/without PO token, bare minimum
- Captures yt-dlp verbose output for debugging why PO tokens aren't recognized

## [0.19.0] - 2025-03-15

### Fixed
- **Direct PO token injection**  - bypasses broken bgutil yt-dlp plugin by fetching tokens directly from the PO token server and injecting them via `po_token` extractor arg
- PO tokens are cached for 5 minutes to avoid hammering the server
- Test endpoint now shows active extractor_args to verify PO token is being passed

## [0.18.0] - 2025-03-15

### Fixed
- PO token server running but not generating tokens  - test endpoint now probes multiple server API endpoints to find the issue
- Default player client changed from hardcoded "web" to yt-dlp's built-in default (which may handle auth better)

### Added
- Configurable player client strategy (`YTDLP_PLAYER_CLIENT` env var or API)
- Test-download endpoint now tries 6 different player client strategies and reports which ones work
- `PUT /api/v1/auth/player-client` endpoint to switch strategy without rebuilding
- Player client setting persists across container restarts

## [0.17.0] - 2025-03-15

### Fixed
- **ROOT CAUSE: PO token server was not connected to yt-dlp**  - the bgutil plugin was installed but never told the server URL, so PO tokens were never generated, causing all downloads to fail with AUTH_EXPIRED / "Sign in to confirm you're not a bot"
- Added `getpot_bgutil_baseurl` to yt-dlp extractor_args so the PO token plugin actually connects to the running server

### Added
- Test-download endpoint now checks PO token generation and plugin installation status

## [0.16.0] - 2025-03-15

### Fixed
- Downloads getting stuck in queue with empty progress bar  - added 15-minute timeout per download
- Stale queue entries (>20 min) now automatically unstick and retry
- Added diagnostic logging showing which auth method (cookies/PO token) is active during downloads
- yt-dlp errors no longer suppressed  - warnings are now logged for debugging

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
