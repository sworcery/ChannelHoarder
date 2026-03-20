import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.models import AppSetting, Channel, Video, DownloadQueue
from app.schemas import DashboardStats, VideoResponse
from app.services.storage_service import get_storage_usage, format_bytes
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    # Batch channel counts into one query
    ch_result = await db.execute(
        select(
            func.count(Channel.id),
            func.count(Channel.id).filter(Channel.enabled == True),
            func.max(Channel.last_scanned_at),
        )
    )
    ch_row = ch_result.one()
    total_channels, active_channels, last_scan = ch_row[0] or 0, ch_row[1] or 0, ch_row[2]

    # Batch video counts into one query using conditional aggregation
    vid_result = await db.execute(
        select(
            func.count(Video.id),
            func.count(Video.id).filter(Video.status == "completed"),
            func.count(Video.id).filter(Video.status == "failed"),
            func.count(Video.id).filter(Video.status.in_(["pending", "queued"])),
        )
    )
    vid_row = vid_result.one()
    total_videos = vid_row[0] or 0
    total_downloaded = vid_row[1] or 0
    total_failed = vid_row[2] or 0
    total_pending = vid_row[3] or 0

    # Batch queue counts into one query
    q_result = await db.execute(
        select(
            func.count(DownloadQueue.id),
            func.count(DownloadQueue.id).filter(DownloadQueue.started_at.isnot(None)),
        )
    )
    q_row = q_result.one()
    queue_length, active_downloads = q_row[0] or 0, q_row[1] or 0

    # Get custom download dirs + cookies_expired in parallel with above
    custom_dirs_result = await db.execute(
        select(Channel.download_dir).where(Channel.download_dir.isnot(None)).distinct()
    )
    custom_dirs = [row[0] for row in custom_dirs_result.all()]

    cookie_flag = await db.execute(
        select(AppSetting).where(AppSetting.key == "cookies_expired")
    )
    cookie_setting = cookie_flag.scalar_one_or_none()
    cookies_expired = cookie_setting is not None and cookie_setting.value == "true"

    storage = get_storage_usage(custom_dirs=custom_dirs)

    ytdlp = YtdlpService()
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
        cookies_expired=cookies_expired,
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
async def get_storage_info(db: AsyncSession = Depends(get_db)):
    custom_dirs_result = await db.execute(
        select(Channel.download_dir).where(Channel.download_dir.isnot(None)).distinct()
    )
    custom_dirs = [row[0] for row in custom_dirs_result.all()]
    storage = get_storage_usage(custom_dirs=custom_dirs)
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
