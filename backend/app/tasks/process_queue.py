import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import async_session
from app.models import AppSetting, Channel, DownloadQueue, Video
from app.services.download_service import DownloadService
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

# Track background download tasks so we know what's actually running
_active_tasks: set[asyncio.Task] = set()


async def _run_download(video_id: int, channel_id: int, queue_id: int):
    """Run a download as a fire-and-forget background task.

    DownloadService manages its own short-lived DB sessions internally,
    so no session is held open during the long-running yt-dlp download.
    """
    try:
        service = DownloadService()
        await service.download_video(video_id, channel_id, queue_id)
    except Exception:
        logger.exception("Unhandled error in background download task for video_id=%d", video_id)


async def process_download_queue():
    """Scheduled task: process the next video in the download queue.

    This function checks the queue and kicks off downloads as background tasks.
    It returns quickly so the scheduler isn't blocked for the entire download
    duration (which can be 15+ minutes).
    """
    # Clean up finished tasks
    done = {t for t in _active_tasks if t.done()}
    _active_tasks.difference_update(done)

    async with async_session() as db:
        # Check if queue is paused
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "queue_paused")
        )
        pause_setting = result.scalar_one_or_none()
        if pause_setting and pause_setting.value == "true":
            return  # Queue is paused — don't start new downloads

        # Unstick any downloads that have been "active" for more than 20 minutes
        stale_cutoff = datetime.now(timezone.utc) - timedelta(minutes=20)
        stale_result = await db.execute(
            select(DownloadQueue)
            .options(joinedload(DownloadQueue.video))
            .where(DownloadQueue.started_at.isnot(None))
            .where(DownloadQueue.started_at < stale_cutoff)
        )
        for stale in stale_result.scalars().unique().all():
            logger.warning("Unsticking stale queue entry (started %s): video_id=%s",
                           stale.started_at, stale.video.video_id if stale.video else "?")
            stale.started_at = None
            stale.progress_percent = 0
            if stale.video:
                stale.video.status = "queued"
        await db.commit()

        # Check how many downloads are currently active
        active_count = await db.scalar(
            select(func.count(DownloadQueue.id)).where(DownloadQueue.started_at.isnot(None))
        )

        max_concurrent = await get_setting(db, "max_concurrent_downloads")
        if active_count and active_count >= max_concurrent:
            return  # At capacity

        # Get next queued video
        result = await db.execute(
            select(DownloadQueue)
            .options(joinedload(DownloadQueue.video))
            .where(DownloadQueue.started_at.is_(None))
            .order_by(DownloadQueue.priority.desc(), DownloadQueue.queued_at.asc())
            .limit(1)
        )
        queue_entry = result.scalar_one_or_none()
        if not queue_entry:
            return  # Nothing to download

        video = queue_entry.video
        if not video:
            await db.delete(queue_entry)
            await db.commit()
            return

        # Get channel
        result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
        channel = result.scalar_one_or_none()
        if not channel:
            logger.error("Channel not found for video %s", video.video_id)
            await db.delete(queue_entry)
            await db.commit()
            return

        # Mark as started NOW (in this session) so the next scheduler tick
        # sees it as active and doesn't double-pick it
        queue_entry.started_at = datetime.now(timezone.utc)
        video.status = "downloading"
        await db.commit()

        # Store IDs and kick off the download as a background task
        # (the task creates its own DB session)
        logger.info("Processing queue: launching download for %s", video.title)
        task = asyncio.create_task(
            _run_download(video.id, channel.id, queue_entry.id),
            name=f"download-{video.video_id}",
        )
        _active_tasks.add(task)
