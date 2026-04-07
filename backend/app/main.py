import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import settings
from app.database import init_database

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger.info("Starting ChannelHoarder v%s", settings.APP_VERSION)

    Path(settings.CONFIG_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.DOWNLOAD_DIR).mkdir(parents=True, exist_ok=True)

    await init_database()
    logger.info("Database initialized")

    # Reset any "active" queue entries left over from a previous run.
    # When the container restarts, in-flight downloads are killed but their
    # queue rows still have started_at set — blocking new downloads until
    # the 20-minute stale timeout kicks in.
    from app.database import async_session
    from sqlalchemy import select, delete
    from sqlalchemy.orm import joinedload
    from app.models import DownloadQueue, Video, SystemHealthLog
    from datetime import datetime, timezone, timedelta
    async with async_session() as db:
        result = await db.execute(
            select(DownloadQueue)
            .options(joinedload(DownloadQueue.video))
            .where(DownloadQueue.started_at.isnot(None))
        )
        stale = result.scalars().unique().all()
        if stale:
            for entry in stale:
                logger.warning(
                    "Resetting stale queue entry from previous run: video_id=%s",
                    entry.video.video_id if entry.video else "?",
                )
                entry.started_at = None
                entry.progress_percent = 0
                if entry.video and entry.video.status == "downloading":
                    entry.video.status = "queued"
            await db.commit()
            logger.info("Reset %d stale queue entries on startup", len(stale))

        # Clean up old health log entries (keep last 7 days)
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        await db.execute(delete(SystemHealthLog).where(SystemHealthLog.checked_at < cutoff))
        await db.commit()

    # Start scheduler
    from app.services.scheduler_service import SchedulerService
    scheduler = SchedulerService()
    await scheduler.start()
    app.state.scheduler = scheduler

    yield

    # Shutdown scheduler
    await scheduler.shutdown()
    logger.info("Shutting down ChannelHoarder")


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import and register routers
from app.routers import channels, downloads, dashboard, auth, settings as settings_router, system, websocket  # noqa: E402

app.include_router(channels.router, prefix="/api/v1/channels", tags=["channels"])
app.include_router(downloads.router, prefix="/api/v1/downloads", tags=["downloads"])
app.include_router(dashboard.router, prefix="/api/v1/dashboard", tags=["dashboard"])
app.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
app.include_router(settings_router.router, prefix="/api/v1/settings", tags=["settings"])
app.include_router(system.router, prefix="/api/v1/system", tags=["system"])
app.include_router(websocket.router, tags=["websocket"])

# Serve built React frontend as static files
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(static_dir / "assets")), name="assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = static_dir / full_path
        # Prevent path traversal — ensure resolved path stays under static_dir
        try:
            file_path = file_path.resolve()
            if not file_path.is_relative_to(static_dir.resolve()):
                return FileResponse(str(static_dir / "index.html"))
        except (ValueError, OSError):
            return FileResponse(str(static_dir / "index.html"))
        if file_path.exists() and file_path.is_file():
            return FileResponse(str(file_path))
        return FileResponse(str(static_dir / "index.html"))
