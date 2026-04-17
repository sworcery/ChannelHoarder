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
    # Fetch due channels list with a short-lived session
    async with async_session() as db:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await db.execute(
            select(Channel.id, Channel.channel_name)
            .where(Channel.enabled == True)
            .where(Channel.channel_id != "__standalone__")
            .where(or_(Channel.next_scan_at.is_(None), Channel.next_scan_at <= now_utc))
        )
        due_channels = [(row[0], row[1]) for row in result.all()]

        if not due_channels:
            return

        logger.info("Scan tick: %d channels due", len(due_channels))
        jitter_enabled, jitter_max = await _get_jitter_settings(db)

    random.shuffle(due_channels)

    total_new = 0
    for i, (channel_id, channel_name) in enumerate(due_channels):
        if jitter_enabled and i > 0 and jitter_max > 0:
            delay = random.uniform(0, jitter_max)
            logger.debug("Scan jitter: sleeping %.1fs before scanning %s", delay, channel_name)
            await asyncio.sleep(delay)

        # Fresh session per channel -- prevents rollback contamination and
        # avoids holding a connection during jitter sleeps
        try:
            async with async_session() as db:
                channel = await db.get(Channel, channel_id)
                if not channel:
                    continue
                service = ChannelService(db)
                new_count = await service.scan_channel(channel)
                total_new += new_count
                if new_count > 0:
                    logger.info("Found %d new videos for %s", new_count, channel_name)
        except Exception as e:
            logger.error("Scan failed for %s: %s", channel_name, e)
            try:
                async with async_session() as db:
                    channel = await db.get(Channel, channel_id)
                    if channel:
                        channel.health_status = "warning"
                        channel.last_error_code = "SCAN_FAILED"
                        service = ChannelService(db)
                        channel.next_scan_at = await service._compute_next_scan_at()
                        await db.commit()
            except Exception:
                pass

    logger.info("Scan tick complete: scanned %d channels, found %d new videos", len(due_channels), total_new)

    await NotificationService.broadcast("scan_complete", {
        "channels_scanned": len(due_channels),
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
