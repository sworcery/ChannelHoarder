# ChannelHoarder API Reference

All endpoints are under `/api/v1/`. An interactive, always-current version is available at `/docs` (Swagger UI) on a running instance.

### Channels
- `GET /channels`  - List all channels (supports search)
- `POST /channels`  - Add a new channel
- `GET /channels/{id}`  - Get channel details
- `PUT /channels/{id}`  - Update channel settings (quality, download dir, enabled)
- `DELETE /channels/{id}`  - Delete a channel (optionally delete files)
- `POST /channels/{id}/scan`  - Trigger a manual scan
- `GET /channels/{id}/videos`  - List channel videos (filterable by status)
- `POST /channels/{id}/download-all`  - Queue all pending videos
- `POST /channels/{id}/videos/bulk-queue`  - Queue selected videos
- `POST /channels/{id}/videos/bulk-skip`  - Skip selected videos
- `POST /channels/{id}/videos/bulk-unskip`  - Unskip selected videos
- `POST /channels/{id}/refresh-metadata`  - Re-fetch channel metadata from platform
- `POST /channels/{id}/import/scan`  - Scan folder for existing video files
- `POST /channels/{id}/import/confirm`  - Import matched files
- `POST /channels/download-all-missing`  - Queue all pending/failed videos across all channels
- `GET /channels/{id}/shorts`  - List videos identified as shorts
- `POST /channels/{id}/shorts/detect`  - Scan existing videos and mark shorts
- `POST /channels/{id}/shorts/detect-clean/preview`  - Preview detect and clean operation
- `POST /channels/{id}/shorts/detect-clean/confirm`  - Detect shorts, delete files, renumber episodes
- `POST /channels/{id}/shorts/delete`  - Delete downloaded shorts from disk
- `POST /channels/{id}/download-subtitles`  - Download subtitles for all completed videos
- `POST /channels/{id}/videos/{vid}/download-subtitles`  - Download subtitles for a single video
- `POST /channels/{id}/move-files/preview`  - Preview file move operation
- `POST /channels/{id}/force-rescan`  - Clear all video records and re-scan from scratch

### Downloads
- `GET /downloads/queue`  - Current download queue with progress
- `POST /downloads/queue`  - Add video to queue
- `DELETE /downloads/queue/{id}`  - Remove from queue
- `POST /downloads/queue/bulk-remove`  - Remove multiple items from queue
- `POST /downloads/queue/{id}/priority`  - Set queue item priority
- `POST /downloads/queue/{id}/download-now`  - Start download immediately, bypassing queue
- `POST /downloads/clear-queue`  - Clear all queued (non-active) downloads
- `GET /downloads/active`  - Currently downloading
- `GET /downloads/paused`  - Check if queue is paused
- `POST /downloads/pause`  - Pause the download queue
- `POST /downloads/resume`  - Resume the download queue
- `GET /downloads/history`  - Filterable download history
- `POST /downloads/retry/{id}`  - Retry a failed download
- `POST /downloads/retry-all-failed`  - Retry all failed downloads
- `POST /downloads/standalone`  - Download a standalone video by URL (auto-creates uploader channel)
- `POST /downloads/standalone/reorganize`  - Migrate legacy standalone downloads to per-uploader channels

### Dashboard
- `GET /dashboard/stats`  - Aggregate statistics
- `GET /dashboard/recent`  - Recent downloads
- `GET /dashboard/storage`  - Storage breakdown by channel

### Authentication
- `POST /auth/cookies/upload`  - Upload cookies.txt file
- `POST /auth/cookies/push`  - Push cookies as JSON (for browser extensions and scripts)
- `GET /auth/cookies/status`  - Cookie file status
- `POST /auth/cookies/validate`  - Force a cookie health check
- `DELETE /auth/cookies`  - Remove cookies
- `PUT /auth/api-key`  - Set YouTube API key
- `PUT /auth/player-client`  - Set yt-dlp player client strategy
- `GET /auth/status`  - Overall auth status

### Settings
- `GET /settings`  - Get all settings
- `PUT /settings`  - Update settings
- `GET /settings/userscript.user.js`  - Download pre-configured Tampermonkey script
- `GET /settings/export`  - Export config backup (JSON)
- `POST /settings/import`  - Import config backup
- `POST /settings/webhook/test`  - Test notification delivery

### System
- `GET /system/health`  - Health check with version
- `GET /system/diagnostics`  - Full diagnostic report
- `GET /system/diagnostics/{video_id}`  - Per-video diagnostic report
- `GET /system/logs`  - System logs with filtering
- `GET /system/pot-server-log`  - PO token server logs and process status
- `POST /system/test-download`  - Test download capability (multi-strategy)
- `POST /system/scan-all`  - Scan all enabled channels
- `GET /system/ytdlp/version`  - Current yt-dlp version

### WebSocket
- `WS /ws/progress`  - Real-time download progress updates
