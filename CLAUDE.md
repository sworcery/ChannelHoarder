# CLAUDE.md - ChannelHoarder Development Guide

## Project Overview
ChannelHoarder is a self-hosted video channel archiver with a modern web UI. It monitors channels across YouTube, Rumble, Twitch, Dailymotion, Vimeo, and Odysee, automatically downloading new videos in Plex-compatible format.

**Architecture:** Single Docker container running FastAPI backend + React frontend + yt-dlp + PO token server.

## Tech Stack
- **Backend:** Python 3.12, FastAPI, SQLAlchemy (async), APScheduler, yt-dlp, aiosqlite
- **Frontend:** React 18, TypeScript, Vite, Tailwind CSS, TanStack Query, React Router, Radix UI
- **Database:** SQLite with WAL mode, async via aiosqlite
- **Auth:** bgutil-ytdlp-pot-provider (PO tokens), optional cookies.txt, optional YouTube Data API

## Project Structure
```
backend/
  app/
    config.py          # Settings (version lives here)
    database.py        # SQLAlchemy engine, session, PRAGMA tuning
    models.py          # SQLAlchemy models (Channel, Video, DownloadQueue, AppSetting, DownloadLog)
    schemas.py         # Pydantic request/response schemas
    deps.py            # FastAPI dependency injection
    routers/           # API route handlers (channels, downloads, dashboard, settings, auth, system)
    services/          # Business logic (channel_service, download_service, ytdlp_service, etc.)
    utils/             # Helpers (platform_utils, file_utils, naming_service)
  pyproject.toml       # Backend version lives here too
frontend/
  src/
    pages/             # React page components
    components/ui/     # Reusable UI components (shadcn-style)
    lib/
      api.ts           # API client
      types.ts         # TypeScript interfaces
      utils.ts         # Formatting helpers
    hooks/             # Custom React hooks
  package.json         # Frontend version lives here too
docker/                # Dockerfile, entrypoint
config/                # Default config templates
tools/                 # Cookie exporter scripts
```

## Version Management
**Version must be bumped in ALL THREE files for every commit (minimum patch 0.0.1):**
1. `backend/app/config.py` — `APP_VERSION`
2. `backend/pyproject.toml` — `version`
3. `frontend/package.json` — `version`

## Key Conventions

### Backend
- All SQLAlchemy relationships use `lazy="noload"` — use explicit `joinedload()` when needed
- Live counts: `total_videos` and `downloaded_count` on Channel are cached integers; the channels router computes live counts from the Video table to avoid stale values
- Platform detection via `backend/app/utils/platform_utils.py` — supports YouTube, Rumble, Twitch, Dailymotion, Vimeo, Odysee
- yt-dlp format strings use `bestvideo*` (with asterisk) to match both video-only AND muxed streams
- Prefer `release_date` over `upload_date` from yt-dlp (actual public release vs upload timestamp)
- YouTube API: prefer `contentDetails.videoPublishedAt` over `snippet.publishedAt`
- AppSettings table stores key-value pairs (JSON-encoded values) for runtime settings
- Shorts detection: duration <= 60s OR URL starts with youtube.com/shorts/

### Frontend
- Uses TanStack Query (React Query v5) for data fetching
- `placeholderData: keepPreviousData` means `isLoading` stays false — use `isFetching` + `!data` for loading states
- Display preferences (view mode, card size, sort) stored in localStorage
- All API calls go through `frontend/src/lib/api.ts`
- Toast notifications via custom `useToast` hook
- Styling: Tailwind CSS with dark mode support, shadcn-inspired component patterns

### Database
- SQLite with WAL mode, tuned PRAGMAs: `cache_size=-65536`, `mmap_size=268435456`, `temp_store=MEMORY`
- Async sessions via aiosqlite
- Alembic not currently used — schema changes via SQLAlchemy model updates

## Commit Rules
1. Always commit AND push after completing work
2. Always test changes before committing (TypeScript compilation, Python syntax)
3. Always bump version (minimum patch 0.0.1) in all three version files
4. Do NOT include Co-Authored-By in commits
5. Update CHANGELOG.md with every release
6. No hardcoded addresses — everything must be configurable

## Common Patterns

### Adding a new setting
1. Add field to `SettingsUpdate` in `schemas.py`
2. Handle in settings router's update endpoint
3. Store in `AppSetting` table as JSON-encoded value
4. Read via `_get_setting_bool()` or similar helper in services

### Adding a new API endpoint
1. Add route in appropriate router file under `backend/app/routers/`
2. Add API client method in `frontend/src/lib/api.ts`
3. Use TanStack Query hooks in the page component

### Adding a model field
1. Add to SQLAlchemy model in `models.py`
2. Add to Pydantic schema in `schemas.py`
3. Add to TypeScript interface in `frontend/src/lib/types.ts`
4. SQLite handles new nullable columns automatically on existing tables

## Testing
- Backend: `pytest` with `asyncio_mode="auto"`
- Frontend: No test framework currently configured — verify via TypeScript compilation (`tsc --noEmit`)
- Docker build validates both backend and frontend compilation
