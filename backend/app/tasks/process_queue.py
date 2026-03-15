import logging

from sqlalchemy import select, func
from sqlalchemy.orm import joinedload

from app.config import settings
from app.database import async_session
from app.models import Channel, DownloadQueue, Video
from app.services.download_service import DownloadService

logger = logging.getLogger(__name__)


async def process_download_queue():
    """Scheduled task: process the next video in the download queue."""
    async with async_session() as db:
        # Check how many downloads are currently active
        active_count = await db.scalar(
            select(func.count(DownloadQueue.id)).where(DownloadQueue.started_at.isnot(None))
        )

        if active_count and active_count >= settings.MAX_CONCURRENT_DOWNLOADS:
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

        # Download
        logger.info("Processing queue: downloading %s", video.title)
        service = DownloadService(db)
        await service.download_video(video, channel)
