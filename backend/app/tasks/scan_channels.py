import asyncio
import logging
import random

from sqlalchemy import select

from app.database import async_session
from app.models import AppSetting, Channel
from app.services.channel_service import ChannelService
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


async def _get_jitter_settings(db) -> tuple[bool, int]:
    """Read scan jitter settings from the database."""
    import json

    enabled = True
    max_seconds = 300

    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_jitter_enabled")
        )
        setting = result.scalar_one_or_none()
        if setting:
            enabled = bool(json.loads(setting.value))
    except Exception:
        pass

    try:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "scan_jitter_max_seconds")
        )
        setting = result.scalar_one_or_none()
        if setting:
            max_seconds = int(json.loads(setting.value))
    except Exception:
        pass

    return enabled, max_seconds


async def scan_all_channels():
    """Scheduled task: scan all enabled channels for new videos.

    Applies jitter between channel scans to avoid predictable traffic patterns
    that YouTube's bot detection can correlate.
    """
    logger.info("Starting scheduled channel scan")

    async with async_session() as db:
        jitter_enabled, jitter_max = await _get_jitter_settings(db)

        result = await db.execute(select(Channel).where(Channel.enabled == True))
        channels = result.scalars().all()

        # Randomize channel order so the same channel isn't always scanned first
        random.shuffle(channels)

        total_new = 0
        for i, channel in enumerate(channels):
            # Apply jitter between channels (skip before the first one)
            if jitter_enabled and i > 0 and jitter_max > 0:
                delay = random.uniform(0, jitter_max)
                logger.debug("Scan jitter: sleeping %.1fs before scanning %s", delay, channel.channel_name)
                await asyncio.sleep(delay)

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
