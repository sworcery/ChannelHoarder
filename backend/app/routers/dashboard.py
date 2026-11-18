import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.models import Channel, Video, DownloadQueue
from app.schemas import DashboardStats, VideoResponse
from app.services import queue_state
from app.services.queue_state import get_cookies_expired
from app.tasks.health_check import disk_space_status
from app.utils.error_codes import ERROR_CATALOG, ErrorCode
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
            func.count(DownloadQueue.id).filter(DownloadQueue.started_at.is_(None)),
        )
    )
    q_row = q_result.one()
    queue_length, active_downloads, queued_count = q_row[0] or 0, q_row[1] or 0, q_row[2] or 0

    # Get custom download dirs + cookies_expired in parallel with above
    custom_dirs_result = await db.execute(
        select(Channel.download_dir).where(Channel.download_dir.isnot(None)).distinct()
    )
    custom_dirs = [row[0] for row in custom_dirs_result.all()]

    cookies_expired = await get_cookies_expired(db)

    storage = await asyncio.to_thread(lambda: get_storage_usage(custom_dirs=custom_dirs))

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
        queued_count=queued_count,
        free_space_bytes=storage["disk_free"],
    )


# --- Alerts: what needs attention ---

_POT_CACHE_SECONDS = 60
_pot_cache: tuple[float, bool] | None = None  # (monotonic time, healthy)


async def _pot_server_healthy() -> bool:
    """Ping the PO token server; the answer is reused for 60 s."""
    global _pot_cache
    now = time.monotonic()
    if _pot_cache is not None and now - _pot_cache[0] < _POT_CACHE_SECONDS:
        return _pot_cache[1]
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.POT_SERVER_URL}/ping", timeout=3)
            healthy = resp.status_code == 200
    except Exception:
        healthy = False
    _pot_cache = (now, healthy)
    return healthy


def _alert(id: str, severity: str, title: str, message: str, label: str, kind: str, target: str | None = None) -> dict:
    return {
        "id": id,
        "severity": severity,
        "title": title,
        "message": message,
        "action": {"label": label, "kind": kind, "target": target},
    }


_PAUSE_REASONS = {
    queue_state.REASON_MANUAL: "You paused the queue. Nothing new starts until you press Resume.",
    queue_state.REASON_COOKIES: "The queue was paused when the cookies expired.",
    queue_state.REASON_DISK_FULL: "The queue was paused because the disk was full.",
}


@router.get("/alerts")
async def get_alerts(db: AsyncSession = Depends(get_db)):
    """What needs attention, most urgent first. Each alert has one action:
    kind "resume_queue" (POST /downloads/resume) or "link" (target is an app path)."""
    alerts: list[dict] = []
    paused, reason = await queue_state.get_pause_state(db)
    cookies_expired = await queue_state.get_cookies_expired(db)

    # Disk full
    disk_critical, free_text, where = False, "", ""
    try:
        free_gb, where, disk_status = await asyncio.to_thread(disk_space_status)
        disk_critical, free_text = disk_status == "critical", f"{free_gb:.1f} GB free{where}"
    except Exception as e:
        logger.warning("Could not read free disk space: %s", e)
    if (paused and reason == queue_state.REASON_DISK_FULL) or disk_critical:
        message = f"Only {free_text}." if free_text else "The disk is full."
        if paused and not disk_critical:
            # Space was freed after the pause; only the Resume is left
            alerts.append(_alert("disk_full", "warning", "Queue paused: the disk had filled up",
                                 f"{free_text if free_text else 'Space is free'} now. Resume when you are ready.",
                                 "Resume", "resume_queue"))
        elif paused:
            alerts.append(_alert("disk_full", "critical", "Disk full", message + " The queue is paused.",
                                 "Resume", "resume_queue"))
        else:
            alerts.append(_alert("disk_full", "critical", "Disk almost full", message + " Free up space before downloading more.",
                                 "Open Diagnostics", "link", "/settings?tab=diagnostics"))

    # Cookies expired
    if cookies_expired:
        alerts.append(_alert("cookies_expired", "critical", "Cookies expired",
                             ("YouTube cookies are no longer valid, so the whole queue is paused until fresh cookies work. Upload fresh cookies."
                              if paused and reason == queue_state.REASON_COOKIES else
                              "YouTube cookies are no longer valid, so downloads that need a sign-in will fail. Upload fresh cookies."),
                             "Upload cookies", "link", "/settings?tab=auth"))

    # PO token server down
    if settings.POT_SERVER_ENABLED and not await _pot_server_healthy():
        alerts.append(_alert("pot_server_down", "warning", "PO token server not responding",
                             "Without it YouTube downloads may fail.",
                             "Open Diagnostics", "link", "/settings?tab=diagnostics"))

    # Queue paused (the disk and cookie pauses have their own alerts)
    if paused and reason != queue_state.REASON_DISK_FULL and not (reason == queue_state.REASON_COOKIES and cookies_expired):
        why = _PAUSE_REASONS.get(reason, f"The queue is paused ({reason}).")
        alerts.append(_alert("queue_paused", "warning", "Queue paused", why, "Resume", "resume_queue"))

    # Unhealthy channels
    result = await db.execute(
        select(Channel.channel_name, Channel.last_error_code)
        .where(Channel.health_status == "unhealthy")
        .where(Channel.enabled == True)
        .order_by(Channel.channel_name)
    )
    unhealthy = result.all()
    if unhealthy:
        parts = []
        for name, code in unhealthy[:3]:
            try:
                why = ERROR_CATALOG[ErrorCode(code)].summary if code else "unknown problem"
            except (ValueError, KeyError):
                why = "unknown problem"
            parts.append(f"{name} ({why})")
        extra = len(unhealthy) - 3
        n = len(unhealthy)
        alerts.append(_alert("unhealthy_channels", "warning",
                             f"{n} channel{'s' if n != 1 else ''} need{'' if n != 1 else 's'} attention",
                             "; ".join(parts) + (f"; and {extra} more" if extra > 0 else "") + ".",
                             "View channels", "link", "/channels?health=unhealthy"))

    # Overdue scans (big libraries that don't fit the scan window)
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)
    overdue = (await db.execute(
        select(func.count(Channel.id))
        .where(Channel.enabled == True)
        .where(Channel.channel_id != "__standalone__")
        .where(Channel.next_scan_at.isnot(None))
        .where(Channel.next_scan_at < cutoff)
    )).scalar() or 0
    if overdue:
        alerts.append(_alert("overdue_scans", "info",
                             f"{overdue} channel{'s' if overdue != 1 else ''} overdue for a scan",
                             "Scans are more than a day late. The scan window may be too short for your library; try widening it.",
                             "Open scan settings", "link", "/settings?tab=scanning"))

    return alerts


@router.get("/recent", response_model=list[VideoResponse])
async def get_recent_downloads(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy.orm import joinedload
    result = await db.execute(
        select(Video)
        .options(joinedload(Video.channel))
        .where(Video.status == "completed")
        .order_by(Video.downloaded_at.desc())
        .limit(limit)
    )
    return result.scalars().unique().all()


@router.get("/storage")
async def get_storage_info(db: AsyncSession = Depends(get_db)):
    custom_dirs_result = await db.execute(
        select(Channel.download_dir).where(Channel.download_dir.isnot(None)).distinct()
    )
    custom_dirs = [row[0] for row in custom_dirs_result.all()]
    storage = await asyncio.to_thread(lambda: get_storage_usage(custom_dirs=custom_dirs))
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
