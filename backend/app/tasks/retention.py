import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import async_session
from app.models import Channel, Video
from app.services.channel_service import ChannelService
from app.utils.file_utils import delete_video_files

logger = logging.getLogger(__name__)


def expired_videos_filter(channel_id: int, days: int):
    """WHERE conditions for a channel's downloaded videos older than `days`.

    downloaded_at is stored as naive UTC (DateTime column fed a UTC-aware value),
    so the cutoff is naive UTC too.
    """
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    return (
        Video.channel_id == channel_id,
        Video.status == "completed",
        Video.downloaded_at.is_not(None),
        Video.file_path.is_not(None),
        Video.downloaded_at < cutoff,
    )


async def apply_retention() -> None:
    """Delete downloaded files older than each channel's retention_days.

    Videos stay in the database as "skipped" so scans (which only add unseen
    video IDs) do not download them again.
    """
    async with async_session() as db:
        result = await db.execute(
            select(Channel.id).where(Channel.retention_days.is_not(None))
        )
        channel_ids = [row[0] for row in result.all()]

    for channel_id in channel_ids:
        try:
            await _apply_to_channel(channel_id)
        except Exception:
            logger.exception("Retention failed for channel id=%s", channel_id)


async def _apply_to_channel(channel_id: int) -> None:
    async with async_session() as db:
        channel = (await db.execute(
            select(Channel).where(Channel.id == channel_id)
        )).scalar_one_or_none()
        if not channel or not channel.retention_days:
            return
        days = channel.retention_days
        name = channel.channel_name

        videos = (await db.execute(
            select(Video).where(*expired_videos_filter(channel_id, days))
        )).scalars().all()
        if not videos:
            return

        service = ChannelService(db)
        removed = 0
        missing = 0
        for video in videos:
            # A cleared setting stops the run
            await db.refresh(channel)
            if not channel.retention_days:
                break
            path = video.file_path
            # A missing file may just be an unmounted share; leave the row alone
            # rather than forget a file that comes back later
            if not await asyncio.to_thread(os.path.exists, path):
                missing += 1
                continue
            await asyncio.to_thread(delete_video_files, path)
            if await asyncio.to_thread(os.path.exists, path):
                logger.error("Retention: could not delete %s; leaving video %s as completed",
                             path, video.video_id)
                continue
            await service._auto_delete_video_files(video)
            await db.commit()  # release the write lock between deletions
            removed += 1
        logger.info("Retention: removed %d video(s) older than %d days for channel %s",
                    removed, days, name)
        if missing:
            logger.info("Retention: %d expired video(s) for channel %s have no file on disk; left as-is",
                        missing, name)
