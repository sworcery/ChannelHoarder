# Changelog

All notable changes to ChannelHoarder will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
