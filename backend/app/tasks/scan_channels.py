import asyncio
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.database import async_session
from app.models import AppSetting, Channel
from app.services.channel_service import ChannelService, ScanFailed
from app.services.notification_service import NotificationService

from app.services.settings_service import get_setting
from app.utils.scan_window import in_scan_window, usable_window

logger = logging.getLogger(__name__)


async def _get_jitter_settings(db) -> tuple[bool, int]:
    """Read scan jitter settings from the database."""
    enabled = True
    max_seconds = 300
    try:
        enabled = await get_setting(db, "scan_jitter_enabled", True)
        max_seconds = await get_setting(db, "scan_jitter_max_seconds", 300)
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
        # Outside the scan window nothing scans; next_scan_at stays as it is, so
        # overdue channels run once the window opens
        start_hour = await get_setting(db, "scan_window_start_hour", None)
        end_hour = await get_setting(db, "scan_window_end_hour", None)
        start_hour, end_hour = usable_window(start_hour, end_hour)
        if not in_scan_window(start_hour, end_hour):
            logger.debug("Scan tick skipped: outside the scan window")
            return

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        # Scans run one after another, oldest due first
        result = await db.execute(
            select(Channel.id, Channel.channel_name)
            .where(Channel.enabled == True)
            .where(Channel.channel_id != "__standalone__")
            .where(or_(Channel.next_scan_at.is_(None), Channel.next_scan_at <= now_utc))
            .order_by(Channel.next_scan_at.asc())
        )
        due_channels = [(row[0], row[1]) for row in result.all()]

        if not due_channels:
            return

        logger.info("Scan tick: %d channels due", len(due_channels))
        jitter_enabled, jitter_max = await _get_jitter_settings(db)

    total_new = 0
    attempted = 0
    for i, (channel_id, channel_name) in enumerate(due_channels):
        if jitter_enabled and i > 0 and jitter_max > 0:
            delay = random.uniform(0, jitter_max)
            logger.debug("Scan jitter: sleeping %.1fs before scanning %s", delay, channel_name)
            await asyncio.sleep(delay)

        # A long tick must not run on past the end of the window; the channels
        # left over stay due and go first when the window opens again
        if not in_scan_window(start_hour, end_hour):
            logger.info("Scan window closed; %d due channels wait for the next window",
                        len(due_channels) - i)
            break
        attempted += 1

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
        except ScanFailed as e:
            # The scan already recorded the failure (unhealthy, SCAN_FAILED, next scan
            # time, one notification); don't overwrite it with the generic handling.
            logger.warning("Scan failed for %s: %s", channel_name, e)
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
                # next_scan_at wasn't updated, so scan_due_channels (which treats
                # NULL as due) will retry this channel every 10-minute tick until the
                # DB recovers - a real rate-limit risk. Surface it.
                logger.warning(
                    "Could not update scan status after failure for %s; it will be "
                    "retried every tick until this clears", channel_name, exc_info=True,
                )

    logger.info("Scan tick complete: scanned %d channels, found %d new videos", attempted, total_new)

    await NotificationService.broadcast("scan_complete", {
        "channels_scanned": attempted,
        "new_videos": total_new,
    })
