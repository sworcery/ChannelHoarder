import asyncio
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import async_session
from app.models import AppSetting, Channel, DownloadQueue, Video
from app.services.download_service import DownloadService
from app.services.queue_order import queue_order_by
from app.services.queue_state import get_pause_state
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
        # Last resort: never leave the row "started" with nothing running behind it.
        try:
            async with async_session() as db:
                entry = await db.get(DownloadQueue, queue_id)
                if entry is not None and entry.started_at is not None:
                    entry.started_at = None
                    entry.progress_percent = 0
                    video = await db.get(Video, video_id)
                    if video is not None and video.status == "downloading":
                        video.status = "queued"
                    await db.commit()
        except Exception:
            logger.exception("Could not reset queue row %d after a download error", queue_id)


def _running_video_ids() -> set[str]:
    """Source video ids whose download is running right now."""
    from app.services import download_service
    running = set(download_service._active_downloads)
    running |= {t.get_name().removeprefix("download-") for t in _active_tasks if not t.done()}
    return running


async def reset_stale_entries(db) -> int:
    """Reset queue rows started more than 4 hours ago whose download is not running."""
    stale_cutoff = datetime.now(timezone.utc) - timedelta(hours=4)
    stale_result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .where(DownloadQueue.started_at.isnot(None))
        .where(DownloadQueue.started_at < stale_cutoff)
    )
    running = _running_video_ids()
    count = 0
    for stale in stale_result.scalars().unique().all():
        if stale.video and stale.video.video_id in running:
            continue
        logger.warning("Unsticking stale queue entry (started %s): video_id=%s",
                       stale.started_at, stale.video.video_id if stale.video else "?")
        stale.started_at = None
        stale.progress_percent = 0
        if stale.video:
            stale.video.status = "queued"
        count += 1
    await db.commit()
    return count


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
        # Unstick any downloads that have been "active" for more than 4 hours
        # (downloads use progress-based timeout so large files can run for hours).
        # Runs while the queue is paused too, and never touches a running download.
        await reset_stale_entries(db)

        # Check if queue is paused
        paused, _reason = await get_pause_state(db)
        if paused:
            return  # Queue is paused  - don't start new downloads

        # Check how many downloads are currently active
        active_count = await db.scalar(
            select(func.count(DownloadQueue.id)).where(DownloadQueue.started_at.isnot(None))
        ) or 0

        max_concurrent = await get_setting(db, "max_concurrent_downloads")
        slots = max_concurrent - active_count
        if slots <= 0:
            return  # At capacity

        # Video IDs whose download task is still alive. The 4h stale-entry reset above
        # clears started_at, which would otherwise let the slot-fill below re-dispatch a
        # download that is still running (its long-lived task hasn't finished) - a second
        # concurrent yt-dlp for the same video. Exclude those.
        running_video_ids = {
            t.get_name().removeprefix("download-") for t in _active_tasks if not t.done()
        }

        # Get next queued videos (fill all available slots). Over-fetch so we can drop
        # any still-running ones and still fill the slots.
        queue_order = await get_setting(db, "download_queue_order")
        from app.services.channel_service import _moving_channels, draining_channel_ids
        draining = draining_channel_ids()
        query = (
            select(DownloadQueue)
            # Outer joins so entries whose video is gone still reach the cleanup below
            .outerjoin(Video, DownloadQueue.video_id == Video.id)
            .outerjoin(Channel, Video.channel_id == Channel.id)
            .options(joinedload(DownloadQueue.video))
            .where(DownloadQueue.started_at.is_(None))
            .where(or_(
                DownloadQueue.retry_after.is_(None),
                DownloadQueue.retry_after <= datetime.now(timezone.utc).replace(tzinfo=None),
            ))
        )
        if _moving_channels:
            # Leave out channels whose files are being moved, in the query itself, so
            # their entries can't take every slot and stall the rest of the queue.
            query = query.where(or_(Channel.id.is_(None), Channel.id.notin_(list(_moving_channels))))
        if draining:
            # A whole-channel action is waiting for this channel's running download to
            # end: start nothing new there so it can finish.
            query = query.where(or_(Channel.id.is_(None), Channel.id.notin_(list(draining))))
        result = await db.execute(
            query.order_by(*queue_order_by(queue_order))
            .limit(slots + len(running_video_ids))
        )
        candidates = result.scalars().unique().all()
        queue_entries = [
            qe for qe in candidates
            if not (qe.video and qe.video.video_id in running_video_ids)
        ][:slots]
        if not queue_entries:
            return  # Nothing to download

        for queue_entry in queue_entries:
            video = queue_entry.video
            if not video:
                await db.delete(queue_entry)
                await db.commit()
                continue

            # Get channel
            ch_result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
            channel = ch_result.scalar_one_or_none()
            if not channel:
                logger.error("Channel not found for video %s", video.video_id)
                await db.delete(queue_entry)
                await db.commit()
                continue

            from app.services.channel_service import _moving_channels
            if channel.id in _moving_channels or channel.id in draining:
                continue  # moving or draining; stays queued for a later tick

            # Mark as started NOW so the next scheduler tick
            # sees it as active and doesn't double-pick it
            queue_entry.started_at = datetime.now(timezone.utc)
            queue_entry.retry_after = None
            queue_entry.target_quality = channel.quality
            video.status = "downloading"
            await db.commit()

            logger.info("Processing queue: launching download for %s", video.title)
            task = asyncio.create_task(
                _run_download(video.id, channel.id, queue_entry.id),
                name=f"download-{video.video_id}",
            )
            _active_tasks.add(task)
