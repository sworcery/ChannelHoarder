"""Queue rules in one place: starting a download, removing idle rows, quality waits.

The dispatcher (tasks/process_queue.py) and the downloads and channels routers all call
these, so a row is started, removed or released the same way whichever path got there.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, DownloadQueue, Video
from app.services import channel_activity
from app.services.download_service import enqueue_video
from app.utils.file_utils import status_after_dequeue

logger = logging.getLogger(__name__)

# A row started this long ago with no running download behind it is stuck.
STUCK_AFTER = timedelta(minutes=2)


def stuck_cutoff() -> datetime:
    return (datetime.now(timezone.utc) - STUCK_AFTER).replace(tzinfo=None)


def is_stuck(started_at: datetime | None, running: bool) -> bool:
    """Started over STUCK_AFTER ago and nothing running behind it."""
    if started_at is not None and started_at.tzinfo is not None:
        started_at = started_at.astimezone(timezone.utc).replace(tzinfo=None)
    return bool(started_at is not None and not running and started_at < stuck_cutoff())


def not_running_condition():
    """SQL condition for a row that may be removed or forced: not started, or started
    over 2 minutes ago with no running download. The age test sits in the statement
    itself, so a row the dispatcher has just started (its task not yet created) is
    never taken, atomically with the DELETE/UPDATE."""
    idle_or_old = or_(DownloadQueue.started_at.is_(None), DownloadQueue.started_at < stuck_cutoff())
    running = channel_activity.running_ids()
    if not running:
        return idle_or_old
    return and_(
        idle_or_old,
        DownloadQueue.video_id.notin_(select(Video.id).where(Video.video_id.in_(list(running)))),
    )


class ChannelBusy(ValueError):
    """A forced start was refused because a move, file work or exclusive scan holds the channel."""


async def start(db: AsyncSession, entry: DownloadQueue, channel: Channel, forced: bool = False) -> bool:
    """Mark a queue row started and launch its download task. ``entry.video`` is loaded.

    The one start path: sets started_at, clears retry_after, applies the channel's
    quality as target_quality, sets the video "downloading", commits, creates the task
    and registers it. forced (Download Now): the row also moves to the top of the queue,
    and the check that it isn't running is part of the UPDATE, so a row the dispatcher
    started meanwhile is refused (returns False), never run twice. Draining is not
    checked for a forced start: it is the user's explicit action.
    """
    from app.tasks.process_queue import _run_download

    video = entry.video
    now = datetime.now(timezone.utc)
    values = dict(started_at=now, retry_after=None, target_quality=channel.quality)
    if forced:
        reason = channel_activity.start_block_reason(channel.id)
        if reason:
            raise ChannelBusy(reason)
        max_priority = await db.scalar(select(func.max(DownloadQueue.priority))) or 0
        values["priority"] = max_priority + 1
        condition = not_running_condition()
    else:
        # Only a row nobody has started: Download Now may have taken it since the
        # dispatcher picked it.
        condition = DownloadQueue.started_at.is_(None)
    # The claim is part of the UPDATE, so a row started meanwhile is refused, never run twice.
    claimed = await db.execute(
        update(DownloadQueue)
        .where(DownloadQueue.id == entry.id, condition)
        .values(**values)
        .returning(DownloadQueue.id)
        .execution_options(synchronize_session=False)
    )
    if claimed.first() is None:
        return False
    video.status = "downloading"
    await db.commit()

    task = asyncio.create_task(
        _run_download(video.id, channel.id, entry.id),
        name=f"download-{video.video_id}",
    )
    channel_activity.active_tasks.add(task)
    task.add_done_callback(channel_activity.active_tasks.discard)
    return True


async def remove_idle(
    db: AsyncSession, *conditions, reset_statuses: tuple[str, ...] = ("queued", "downloading"),
) -> list[int]:
    """Delete matching queue rows whose download is not running (a row with started_at
    but no running download is stuck and is cleared too), give their videos a resting
    status back, and return the video ids. The running test is part of the DELETE."""
    result = await db.execute(
        delete(DownloadQueue)
        .where(*conditions, not_running_condition())
        .returning(DownloadQueue.video_id)
        .execution_options(synchronize_session=False)
    )
    video_ids = [row[0] for row in result.all()]
    for i in range(0, len(video_ids), 500):
        videos = (await db.execute(
            select(Video).where(Video.id.in_(video_ids[i:i + 500]), Video.status.in_(reset_statuses))
        )).scalars().all()
        for video in videos:
            video.status = status_after_dequeue(video)
    return video_ids


async def clear_quality_waits(db: AsyncSession, channel_id: int) -> None:
    """Videos of the channel waiting for higher quality have nothing left to wait for:
    they run on the next tick."""
    await db.execute(
        update(DownloadQueue)
        .where(
            DownloadQueue.video_id.in_(select(Video.id).where(Video.channel_id == channel_id)),
            DownloadQueue.quality_attempts > 0,
            DownloadQueue.retry_after.isnot(None),
        )
        .values(retry_after=None, quality_attempts=0)
        .execution_options(synchronize_session=False)
    )


async def download_anyway(db: AsyncSession, video: Video) -> str:
    """Queue a video that failed (or is waiting on) the minimum-quality check, skipping
    that check. Returns the user message; raises ValueError (the 409 text) when the video
    isn't in either state. Flushes; the caller commits."""
    if video.status == "queued":
        # Waiting for higher quality: its row has used a wait and has a retry time.
        entry = (await db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == video.id)
        )).scalar_one_or_none()
        if (
            entry is None or entry.quality_attempts <= 0 or entry.retry_after is None
            or channel_activity.is_running(video.video_id)
        ):
            raise ValueError("Only a video waiting for higher quality can be downloaded anyway")
        await enqueue_video(db, video, skip_min_quality=True)
        return f"Video '{video.title}' will download at the best available quality"
    if video.status != "failed" or video.error_code != "QUALITY_TOO_LOW":
        raise ValueError("Only a video that failed the minimum-quality check can be downloaded anyway")
    await enqueue_video(db, video, skip_min_quality=True, reset_retries=True)
    return f"Video '{video.title}' queued at the best available quality"
