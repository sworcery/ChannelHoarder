import asyncio
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import or_, select

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


async def scan_due_channels():
    """Tick task: scan any channel whose next_scan_at has arrived.

    Runs every 10 minutes. Each channel has its own randomized scan time assigned
    within the configured daily window, so scans spread naturally instead of
    firing in a burst.
    """
    async with async_session() as db:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(Channel)
            .where(Channel.enabled == True)
            .where(Channel.channel_id != "__standalone__")
            .where(or_(Channel.next_scan_at.is_(None), Channel.next_scan_at <= now_utc))
        )
        channels = result.scalars().all()

        if not channels:
            return

        logger.info("Scan tick: %d channels due", len(channels))

        jitter_enabled, jitter_max = await _get_jitter_settings(db)
        random.shuffle(channels)

        total_new = 0
        for i, channel in enumerate(channels):
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
                # Don't leave next_scan_at in the past forever - reschedule even on failure
                try:
                    channel.next_scan_at = await service._compute_next_scan_at()
                except Exception:
                    pass
                await db.commit()

        logger.info("Scan tick complete: scanned %d channels, found %d new videos", len(channels), total_new)

        await NotificationService.broadcast("scan_complete", {
            "channels_scanned": len(channels),
            "new_videos": total_new,
        })


# Keep the old name as an alias so any external references (including the
# system router's manual "scan all" trigger) continue to work.
async def scan_all_channels():
    """Backwards-compat alias that forces all enabled channels to scan now."""
    async with async_session() as db:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(Channel)
            .where(Channel.enabled == True)
            .where(Channel.channel_id != "__standalone__")
        )
        channels = result.scalars().all()
        for channel in channels:
            channel.next_scan_at = now_utc
        await db.commit()
    await scan_due_channels()
