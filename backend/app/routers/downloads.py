import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.config import settings as app_settings
from app.deps import get_db
from app.models import Channel, DownloadLog, DownloadQueue, Video
from app.services import queue_state
from app.schemas import BulkQueueRemove, PriorityUpdate, QueueAdd, QueueEntryResponse, VideoResponse
from app.services.download_service import enqueue_video, enqueue_videos
from app.services.queue_order import queue_order_by
from app.services.settings_service import get_setting
from app.services.ytdlp_service import YtdlpService
from app.utils.file_utils import escape_like, move_video_files, status_after_dequeue, validate_download_path

logger = logging.getLogger(__name__)
router = APIRouter()


async def _delete_idle(db: AsyncSession, *conditions) -> list[int]:
    """Delete matching queue rows that have not started; returns their video ids."""
    result = await db.execute(
        delete(DownloadQueue)
        .where(*conditions, DownloadQueue.started_at.is_(None))
        .returning(DownloadQueue.video_id)
        .execution_options(synchronize_session=False)
    )
    return [row[0] for row in result.all()]


async def _reset_dequeued(db: AsyncSession, video_ids: list[int], statuses: tuple[str, ...]) -> None:
    """Give videos whose row was just deleted their resting status back."""
    for i in range(0, len(video_ids), 500):
        videos = (await db.execute(
            select(Video).where(Video.id.in_(video_ids[i:i + 500]), Video.status.in_(statuses))
        )).scalars().all()
        for video in videos:
            video.status = status_after_dequeue(video)


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

    await enqueue_video(db, video, priority=body.priority)
    await db.commit()
    return {"message": "Video added to queue"}


@router.post("/queue/bulk-remove")
async def bulk_remove_from_queue(body: BulkQueueRemove, db: AsyncSession = Depends(get_db)):
    """Remove multiple items from the queue at once."""
    deleted = await _delete_idle(db, DownloadQueue.id.in_(body.queue_ids))
    removed = len(deleted)
    await _reset_dequeued(db, deleted, ("queued", "downloading"))
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
    # Delete only an idle row; the started_at test is part of the DELETE itself
    deleted = await _delete_idle(db, DownloadQueue.id == queue_id)
    if not deleted:
        exists = await db.scalar(select(DownloadQueue.id).where(DownloadQueue.id == queue_id))
        if exists is None:
            raise HTTPException(status_code=404, detail="Queue entry not found")
        raise HTTPException(status_code=409, detail="Cannot remove a download that is already running")
    await _reset_dequeued(db, deleted, ("queued", "downloading"))
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
    if video.status != "failed" or video.error_code != "QUALITY_TOO_LOW":
        raise HTTPException(
            status_code=409,
            detail="Only a video that failed the minimum-quality check can be downloaded anyway",
        )

    await enqueue_video(db, video, skip_min_quality=True, reset_retries=True)
    await db.commit()
    return {"message": f"Video '{video.title}' queued at the best available quality"}


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
    return [QueueEntryResponse.model_validate(e) for e in entries]


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
    deleted = await _delete_idle(db)
    count = len(deleted)
    await _reset_dequeued(db, deleted, ("queued",))
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

    # Same name the queue dispatcher uses, so each side sees the other's running
    # download. After the 4h stale reset started_at is cleared while the task may
    # still be running.
    task_name = f"download-{video.video_id}"
    if any(t.get_name() == task_name and not t.done() for t in _active_tasks):
        raise HTTPException(status_code=409, detail="Download already in progress")

    result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
    from app.services.channel_service import _moving_channels
    if channel.id in _moving_channels:
        raise HTTPException(status_code=409, detail="This channel's files are being moved; try again shortly")

    # Mark as started and bump priority
    max_priority = await db.scalar(select(func.max(DownloadQueue.priority))) or 0
    entry.priority = max_priority + 1
    entry.started_at = datetime.now(timezone.utc)
    video.status = "downloading"
    await db.commit()

    # Launch download as background task
    task = asyncio.create_task(
        _run_download(video.id, channel.id, entry.id),
        name=task_name,
    )
    _active_tasks.add(task)
    task.add_done_callback(_active_tasks.discard)

    logger.info("Download NOW triggered for: %s", video.title)
    return {"message": f"Starting download: {video.title}"}








