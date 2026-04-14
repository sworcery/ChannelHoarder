"""Scheduled task: automatic quality upgrade search.

Checks all channels with a quality cutoff set and re-queues
completed videos that are below the cutoff for re-download.
"""

import logging

from sqlalchemy import select

from app.database import async_session
from app.models import Channel, DownloadQueue, Video
from app.utils.quality_utils import quality_met

logger = logging.getLogger(__name__)


async def check_quality_upgrades():
    """Re-queue videos that are below their channel's target quality."""
    async with async_session() as db:
        # Channels with an explicit cutoff, OR channels with a specific quality target (not 'best')
        result = await db.execute(
            select(Channel).where(Channel.enabled == True)
        )
        channels = result.scalars().all()

        if not channels:
            return

        total_queued = 0
        channels_checked = 0

        for channel in channels:
            # Use quality_cutoff if explicitly set, else fall back to channel.quality.
            # 'best' means no cutoff check.
            cutoff = channel.quality_cutoff or channel.quality
            if not cutoff or cutoff == "best":
                continue

            subquery = select(DownloadQueue.video_id)
            result = await db.execute(
                select(Video)
                .where(Video.channel_id == channel.id)
                .where(Video.status == "completed")
                .where(Video.monitored == True)
                .where(Video.id.notin_(subquery))
            )
            videos = result.scalars().all()

            queued = 0
            for video in videos:
                if not quality_met(video.quality_downloaded, cutoff):
                    video.status = "queued"
                    db.add(DownloadQueue(video_id=video.id, target_quality=channel.quality))
                    queued += 1

            if queued > 0:
                total_queued += queued
                logger.info("Quality upgrade: queued %d videos for %s", queued, channel.channel_name)

            channels_checked += 1

        await db.commit()
        logger.info("Quality upgrade check complete: queued %d videos across %d channels", total_queued, channels_checked)

        if total_queued > 0:
            from app.services.notification_service import NotificationService
            await NotificationService.broadcast("quality_upgrade", {
                "message": f"Quality upgrade: queued {total_queued} videos for re-download across {channels_checked} channels",
            })
