import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings as app_settings
from app.deps import get_db
from app.models import AppSetting, Channel, DownloadLog, DownloadQueue, Video
from app.schemas import BulkQueueRemove, PriorityUpdate, QueueAdd, QueueEntryResponse, VideoResponse
from app.services.ytdlp_service import YtdlpService
from app.utils.file_utils import escape_like, move_video_files, validate_download_path

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/queue")
async def get_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    # Count query (no eager loading needed)
    count_query = select(func.count(DownloadQueue.id))
    if search:
        count_query = count_query.join(DownloadQueue.video).where(Video.title.ilike(f"%{escape_like(search)}%"))
    total = await db.scalar(count_query) or 0

    # Data query with eager loading  - defer large text blobs not needed for queue display
    data_query = select(DownloadQueue).options(
        joinedload(DownloadQueue.video)
        .defer(Video.description)
        .defer(Video.error_details)
        .defer(Video.file_path)
        .joinedload(Video.channel)
    )
    if search:
        data_query = data_query.join(DownloadQueue.video).where(Video.title.ilike(f"%{escape_like(search)}%"))

    result = await db.execute(
        data_query
        .order_by(DownloadQueue.priority.desc(), DownloadQueue.queued_at.asc())
        .offset(skip)
        .limit(limit)
    )
    entries = result.scalars().unique().all()

    return {
        "items": [QueueEntryResponse.model_validate(e) for e in entries],
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

    video.status = "queued"
    db.add(DownloadQueue(video_id=body.video_id, priority=body.priority))
    await db.commit()
    return {"message": "Video added to queue"}


@router.post("/queue/bulk-remove")
async def bulk_remove_from_queue(body: BulkQueueRemove, db: AsyncSession = Depends(get_db)):
    """Remove multiple items from the queue at once."""
    result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .where(DownloadQueue.id.in_(body.queue_ids))
    )
    entries = result.scalars().unique().all()
    removed = 0
    for entry in entries:
        if entry.video and entry.video.status in ("queued", "downloading"):
            entry.video.status = "pending"
        await db.delete(entry)
        removed += 1
    await db.commit()
    return {"message": f"Removed {removed} items from queue", "removed": removed}


@router.delete("/queue/{queue_id}", status_code=204)
async def remove_from_queue(queue_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DownloadQueue).where(DownloadQueue.id == queue_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    # Reset video status
    result = await db.execute(select(Video).where(Video.id == entry.video_id))
    video = result.scalar_one_or_none()
    if video and video.status in ("queued", "downloading"):
        video.status = "pending"

    await db.delete(entry)
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

    video.status = "queued"
    video.error_code = None
    video.error_message = None
    video.error_details = None

    # Check if already in queue
    existing = await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == video.id))
    if not existing.scalar_one_or_none():
        db.add(DownloadQueue(video_id=video.id))

    await db.commit()
    return {"message": f"Video '{video.title}' queued for retry"}


@router.post("/retry-all-failed", status_code=202)
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.status == "failed"))
    videos = result.scalars().all()

    # Batch-load existing queue entries to avoid N+1
    video_ids = [v.id for v in videos]
    existing_result = await db.execute(
        select(DownloadQueue.video_id).where(DownloadQueue.video_id.in_(video_ids))
    )
    already_queued = {row[0] for row in existing_result.all()}

    count = 0
    for video in videos:
        video.status = "queued"
        video.error_code = None
        video.error_message = None
        video.error_details = None

        if video.id not in already_queued:
            db.add(DownloadQueue(video_id=video.id))
            count += 1

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
    return [QueueEntryResponse.model_validate(e) for e in entries]


# --- Queue control: pause / resume / clear ---

PAUSE_KEY = "queue_paused"


@router.get("/paused")
async def get_pause_status(db: AsyncSession = Depends(get_db)):
    """Check if the download queue is paused."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == PAUSE_KEY))
    setting = result.scalar_one_or_none()
    paused = setting is not None and setting.value == "true"
    return {"paused": paused}


@router.post("/pause")
async def pause_queue(db: AsyncSession = Depends(get_db)):
    """Pause the download queue. In-flight downloads will finish, but no new ones will start."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == PAUSE_KEY))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = "true"
    else:
        db.add(AppSetting(key=PAUSE_KEY, value="true"))
    await db.commit()
    logger.info("Download queue PAUSED")
    return {"paused": True, "message": "Queue paused. In-flight downloads will complete."}


@router.post("/resume")
async def resume_queue(db: AsyncSession = Depends(get_db)):
    """Resume the download queue."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == PAUSE_KEY))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = "false"
    else:
        db.add(AppSetting(key=PAUSE_KEY, value="false"))
    await db.commit()
    logger.info("Download queue RESUMED")
    return {"paused": False, "message": "Queue resumed. Downloads will start within 30 seconds."}


@router.post("/clear-queue", status_code=200)
async def clear_queue(db: AsyncSession = Depends(get_db)):
    """Remove all non-active items from the download queue and reset video statuses."""
    # Only clear items that haven't started (don't interrupt active downloads)
    result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .where(DownloadQueue.started_at.is_(None))
    )
    entries = result.scalars().unique().all()
    count = 0
    for entry in entries:
        if entry.video and entry.video.status in ("queued",):
            entry.video.status = "pending"
        await db.delete(entry)
        count += 1
    await db.commit()
    logger.info("Cleared %d items from download queue", count)
    return {"message": f"Cleared {count} items from queue", "cleared": count}


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


@router.post("/queue/{queue_id}/download-now")
async def download_now(queue_id: int, db: AsyncSession = Depends(get_db)):
    """Start downloading a queued item immediately, bypassing the queue scheduler."""
    from app.tasks.process_queue import _run_download, _active_tasks

    result = await db.execute(
        select(DownloadQueue).options(joinedload(DownloadQueue.video)).where(DownloadQueue.id == queue_id)
    )
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")
    if entry.started_at is not None:
        raise HTTPException(status_code=409, detail="Download already in progress")

    video = entry.video
    if not video:
        raise HTTPException(status_code=404, detail="Video not found for queue entry")

    result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Mark as started and bump priority
    max_priority = await db.scalar(select(func.max(DownloadQueue.priority))) or 0
    entry.priority = max_priority + 1
    entry.started_at = datetime.now(timezone.utc)
    video.status = "downloading"
    await db.commit()

    # Launch download as background task
    task = asyncio.create_task(
        _run_download(video.id, channel.id, entry.id),
        name=f"download-now-{video.video_id}",
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)

    logger.info("Download NOW triggered for: %s", video.title)
    return {"message": f"Starting download: {video.title}"}








