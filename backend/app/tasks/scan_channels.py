import logging

from sqlalchemy import select

from app.database import async_session
from app.models import Channel
from app.services.channel_service import ChannelService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


async def scan_all_channels():
    """Scheduled task: scan all enabled channels for new videos."""
    logger.info("Starting scheduled channel scan")

    async with async_session() as db:
        result = await db.execute(select(Channel).where(Channel.enabled == True))
        channels = result.scalars().all()

        total_new = 0
        for channel in channels:
            try:
                service = ChannelService(db)
                new_count = await service.scan_channel(channel)
                total_new += new_count
                if new_count > 0:
                    logger.info("Found %d new videos for %s", new_count, channel.channel_name)
            except Exception as e:
                logger.error("Scan failed for %s: %s", channel.channel_name, e)
                channel.health_status = "warning"
                channel.last_error_code = "SCAN_FAILED"
                await db.commit()

        logger.info("Scan complete: checked %d channels, found %d new videos", len(channels), total_new)

        await NotificationService.broadcast("scan_complete", {
            "channels_scanned": len(channels),
            "new_videos": total_new,
        })
