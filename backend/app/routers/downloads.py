import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, delete, or_, select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings as app_settings
from app.deps import get_db
from app.models import Channel, DownloadLog, DownloadQueue, Video
from app.services import channel_activity, queue_service, queue_state
from app.schemas import BulkQueueRemove, PriorityUpdate, QueueAdd, QueueEntryResponse, VideoResponse
from app.services.download_service import enqueue_video, enqueue_videos
from app.services.queue_order import queue_order_by
from app.services.settings_service import get_setting
from app.services.ytdlp_service import YtdlpService
from app.utils.file_utils import escape_like, move_video_files, status_after_dequeue, validate_download_path

logger = logging.getLogger(__name__)
router = APIRouter()


def _queue_response(entry: DownloadQueue, running: set[str] | None = None) -> QueueEntryResponse:
    resp = QueueEntryResponse.model_validate(entry)
    if running is None:
        running = channel_activity.running_ids()
    resp.running = bool(entry.video and entry.video.video_id in running)
    resp.stuck = queue_service.is_stuck(entry.started_at, resp.running)
    return resp


@router.get("/queue")
async def get_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    # Count query (no eager loading needed); same joins as the data query so
    # leftover rows without a video don't inflate the total
    count_query = (
        select(func.count(DownloadQueue.id))
        .join(Video, DownloadQueue.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
    )
    if search:
        count_query = count_query.where(Video.title.ilike(f"%{escape_like(search)}%"))
    total = await db.scalar(count_query) or 0

    # Data query with eager loading  - defer large text blobs not needed for queue display
    queue_order = await get_setting(db, "download_queue_order")
    data_query = (
        select(DownloadQueue)
        .join(Video, DownloadQueue.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .options(
            joinedload(DownloadQueue.video)
            .defer(Video.description)
            .defer(Video.error_details)
            .defer(Video.file_path)
            .joinedload(Video.channel)
        )
    )
    if search:
        data_query = data_query.where(Video.title.ilike(f"%{escape_like(search)}%"))

    result = await db.execute(
        data_query
        # Active downloads first, then the order they will be dispatched in
        .order_by(DownloadQueue.started_at.is_(None), *queue_order_by(queue_order))
        .offset(skip)
        .limit(limit)
    )
    entries = result.scalars().unique().all()
    running = channel_activity.running_ids()

    return {
        "items": [_queue_response(e, running) for e in entries],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post("/queue", status_code=201)
async def add_to_queue(body: QueueAdd, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.id == body.video_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Check if already queued
    existing = await db.execute(
        select(DownloadQueue).where(DownloadQueue.video_id == body.video_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Video already in queue")

    await enqueue_video(db, video, priority=body.priority)
    await db.commit()
    return {"message": "Video added to queue"}


@router.post("/queue/bulk-remove")
async def bulk_remove_from_queue(body: BulkQueueRemove, db: AsyncSession = Depends(get_db)):
    """Remove multiple items from the queue at once."""
    deleted = await queue_service.remove_idle(db, DownloadQueue.id.in_(body.queue_ids))
    removed = len(deleted)
    # Rows still there were running: yt-dlp keeps going, so they stay
    skipped = await db.scalar(
        select(func.count(DownloadQueue.id)).where(DownloadQueue.id.in_(body.queue_ids))
    ) or 0
    await db.commit()
    message = f"Removed {removed} items from queue"
    if skipped:
        message += f"; left {skipped} running downloads alone"
    return {"message": message, "removed": removed}


@router.delete("/queue/{queue_id}", status_code=204)
async def remove_from_queue(queue_id: int, db: AsyncSession = Depends(get_db)):
    # Delete any row that isn't running; the running test is part of the DELETE itself
    deleted = await queue_service.remove_idle(db, DownloadQueue.id == queue_id)
    if not deleted:
        exists = await db.scalar(select(DownloadQueue.id).where(DownloadQueue.id == queue_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        raise HTTPException(status_code=409, detail="Cannot remove a download that is already running")
    await db.commit()


@router.get("/history")
async def get_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    channel_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    error_code: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    # Build WHERE conditions for reuse in both count and data queries
    conditions = []
    if channel_id:
        conditions.append(Video.channel_id == channel_id)
    if status:
        conditions.append(Video.status == status)
    if search:
        conditions.append(Video.title.ilike(f"%{escape_like(search)}%"))
    if error_code:
        conditions.append(Video.error_code == error_code)

    # Count query  - lightweight, no joins
    count_query = select(func.count(Video.id))
    for cond in conditions:
        count_query = count_query.where(cond)
    total = await db.scalar(count_query) or 0

    # Data query  - eager-load channel for channel_name display
    data_query = (
        select(Video)
        .options(joinedload(Video.channel))
        .order_by(Video.downloaded_at.desc().nullslast(), Video.created_at.desc())
    )
    for cond in conditions:
        data_query = data_query.where(cond)

    result = await db.execute(data_query.offset(skip).limit(limit))
    videos = result.scalars().unique().all()

    return {
        "items": [VideoResponse.model_validate(v) for v in videos],
        "total": total or 0,
        "skip": skip,
        "limit": limit,
    }


@router.post("/retry/{video_id}", status_code=202)
async def retry_download(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in ("failed", "skipped"):
        raise HTTPException(status_code=400, detail=f"Cannot retry video with status '{video.status}'")

    await enqueue_video(db, video, reset_retries=True)
    await db.commit()
    return {"message": f"Video '{video.title}' queued for retry"}


@router.post("/download-anyway/{video_id}", status_code=202)
async def download_anyway(video_id: int, db: AsyncSession = Depends(get_db)):
    """Queue a video that failed the minimum-quality check, skipping that check."""
    video = (await db.execute(select(Video).where(Video.id == video_id))).scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    try:
        message = await queue_service.download_anyway(db, video)
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    await db.commit()
    return {"message": message}


@router.post("/retry-all-failed", status_code=202)
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.status == "failed"))
    videos = result.scalars().all()

    count = await enqueue_videos(db, videos, reset_retries=True)

    await db.commit()
    return {"message": f"Queued {count} failed videos for retry", "count": count}


@router.get("/active")
async def get_active_downloads(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .where(DownloadQueue.started_at.isnot(None))
    )
    entries = result.scalars().unique().all()
    running = channel_activity.running_ids()
    return [_queue_response(e, running) for e in entries]


# --- Queue control: pause / resume / clear ---

@router.get("/paused")
async def get_pause_status(db: AsyncSession = Depends(get_db)):
    """Check if the download queue is paused."""
    paused, reason = await queue_state.get_pause_state(db)
    return {"paused": paused, "reason": reason}


@router.post("/pause")
async def pause_queue(db: AsyncSession = Depends(get_db)):
    """Pause the download queue. In-flight downloads will finish, but no new ones will start."""
    await queue_state.pause_queue(db, queue_state.REASON_MANUAL)
    await db.commit()
    logger.info("Download queue PAUSED")
    return {"paused": True, "message": "Queue paused. In-flight downloads will complete."}


@router.post("/resume")
async def resume_queue(db: AsyncSession = Depends(get_db)):
    """Resume the download queue (clears the pause reason)."""
    await queue_state.resume_queue(db)
    await db.commit()
    logger.info("Download queue RESUMED")
    return {"paused": False, "message": "Queue resumed. Downloads will start within 30 seconds."}


@router.post("/clear-queue", status_code=200)
async def clear_queue(db: AsyncSession = Depends(get_db)):
    """Remove all non-active items from the download queue and reset video statuses."""
    # Only clear items that haven't started (don't interrupt active downloads)
    deleted = await queue_service.remove_idle(db)
    count = len(deleted)
    await db.commit()
    logger.info("Cleared %d items from download queue", count)
    return {
        "message": f"Cleared {count} items from queue. Running downloads are left alone.",
        "cleared": count,
    }


@router.post("/queue/{queue_id}/priority")
async def set_queue_priority(queue_id: int, body: PriorityUpdate, db: AsyncSession = Depends(get_db)):
    """Set the priority of a queue entry. Higher priority downloads first."""
    result = await db.execute(select(DownloadQueue).where(DownloadQueue.id == queue_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    if entry.started_at is not None:
        raise HTTPException(status_code=409, detail="Cannot change priority of an active download")
    entry.priority = body.priority
    await db.commit()
    return {"message": f"Priority set to {body.priority}", "priority": body.priority}


@router.post("/queue/{queue_id}/move-to-top")
async def move_queue_item_to_top(queue_id: int, db: AsyncSession = Depends(get_db)):
    """Put a queue entry ahead of everything else currently queued."""
    result = await db.execute(select(DownloadQueue).where(DownloadQueue.id == queue_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    if entry.started_at is not None:
        raise HTTPException(status_code=409, detail="Cannot change priority of an active download")
    max_priority = await db.scalar(select(func.max(DownloadQueue.priority))) or 0
    entry.priority = max(max_priority, entry.priority) + 1
    await db.commit()
    return {"message": "Moved to top of queue", "priority": entry.priority}


@router.post("/queue/{queue_id}/download-now")
async def download_now(queue_id: int, db: AsyncSession = Depends(get_db)):
    """Start downloading a queued item immediately, bypassing the queue scheduler."""
    result = await db.execute(
        select(DownloadQueue).options(joinedload(DownloadQueue.video)).where(DownloadQueue.id == queue_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    video = entry.video
    if not video:
        raise HTTPException(status_code=404, detail="Video not found for queue entry")

    # Refuse only a download that is really running; a started row with nothing
    # running behind it is stuck and may be started again.
    if channel_activity.is_running(video.video_id):
        raise HTTPException(status_code=409, detail="Download already in progress")

    result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # A move, file work or exclusive scan on the channel refuses (409 with its reason).
    # A draining channel (a whole-channel action is waiting) is not checked on purpose:
    # Download Now is the user's explicit action.
    try:
        started = await queue_service.start(db, entry, channel, forced=True)
    except queue_service.ChannelBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    if not started:
        raise HTTPException(status_code=409, detail="Download already in progress")

    logger.info("Download NOW triggered for: %s", video.title)
    return {"message": f"Starting download: {video.title}"}








