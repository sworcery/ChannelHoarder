import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.models import Channel, Video, DownloadQueue
from app.schemas import DashboardStats, VideoResponse
from app.services.storage_service import get_storage_usage, format_bytes
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    total_channels = await db.scalar(select(func.count(Channel.id))) or 0
    active_channels = await db.scalar(
        select(func.count(Channel.id)).where(Channel.enabled == True)
    ) or 0
    total_videos = await db.scalar(select(func.count(Video.id))) or 0
    total_downloaded = await db.scalar(
        select(func.count(Video.id)).where(Video.status == "completed")
    ) or 0
    total_failed = await db.scalar(
        select(func.count(Video.id)).where(Video.status == "failed")
    ) or 0
    total_pending = await db.scalar(
        select(func.count(Video.id)).where(Video.status.in_(["pending", "queued"]))
    ) or 0
    queue_length = await db.scalar(select(func.count(DownloadQueue.id))) or 0
    active_downloads = await db.scalar(
        select(func.count(DownloadQueue.id)).where(DownloadQueue.started_at.isnot(None))
    ) or 0

    last_scan = await db.scalar(
        select(func.max(Channel.last_scanned_at))
    )

    storage = get_storage_usage()

    ytdlp = YtdlpService()

    # Check PO token status
    pot_status = "enabled" if settings.POT_SERVER_ENABLED else "disabled"
    cookies_status = "present" if settings.has_cookies else "not configured"

    return DashboardStats(
        total_channels=total_channels,
        active_channels=active_channels,
        total_videos_known=total_videos,
        total_downloaded=total_downloaded,
        total_failed=total_failed,
        total_pending=total_pending,
        queue_length=queue_length,
        storage_used_bytes=storage["downloads_used"],
        storage_used_formatted=format_bytes(storage["downloads_used"]),
        pot_status=pot_status,
        cookies_status=cookies_status,
        api_key_configured=settings.has_youtube_api_key,
        ytdlp_version=ytdlp.get_version(),
        last_scan_at=last_scan,
        active_downloads=active_downloads,
    )


@router.get("/recent", response_model=list[VideoResponse])
async def get_recent_downloads(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Video)
        .where(Video.status == "completed")
        .order_by(Video.downloaded_at.desc())
        .limit(limit)
    )
    return result.scalars().all()


@router.get("/storage")
async def get_storage_info():
    storage = get_storage_usage()
    return {
        "disk_total": storage["disk_total"],
        "disk_total_formatted": format_bytes(storage["disk_total"]),
        "disk_used": storage["disk_used"],
        "disk_used_formatted": format_bytes(storage["disk_used"]),
        "disk_free": storage["disk_free"],
        "disk_free_formatted": format_bytes(storage["disk_free"]),
        "downloads_used": storage["downloads_used"],
        "downloads_used_formatted": format_bytes(storage["downloads_used"]),
        "channels": {
            name: {"bytes": size, "formatted": format_bytes(size)}
            for name, size in storage["channels"].items()
        },
    }
