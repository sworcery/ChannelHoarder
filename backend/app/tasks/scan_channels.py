import asyncio
import logging
import random
from datetime import datetime, timezone

from sqlalchemy import or_, select

from app.database import async_session
from app.models import AppSetting, Channel
from app.services import channel_activity
from app.services.channel_service import ChannelService, ScanFailed, record_scan_error
from app.services.notification_service import NotificationService

from app.services.settings_service import get_setting, read_scan_window
from app.utils.scan_window import in_scan_window

logger = logging.getLogger(__name__)


async def _get_jitter_settings(db) -> tuple[bool, int]:
    """Read scan jitter settings from the database."""
    return (
        await get_setting(db, "scan_jitter_enabled"),
        await get_setting(db, "scan_jitter_max_seconds"),
    )


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
        start_hour, end_hour = await read_scan_window(db)
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

    await scan_channel_list(
        due_channels, async_session,
        jitter_enabled=jitter_enabled, jitter_max=jitter_max, window=(start_hour, end_hour),
    )


async def scan_channel_list(
    channels: list[tuple[int, str]],
    session_factory,
    *,
    jitter_enabled: bool,
    jitter_max: int,
    window: tuple[int | None, int | None] | None = None,
) -> tuple[int, int, int]:
    """Scan channels one after another: the loop the scheduler tick and Scan All share.

    Jitter sleeps between channels; a channel something else holds (moving, scanning,
    file work) is skipped; each scan gets a fresh session so one failure cannot poison
    the rest. window=(start, end) stops the run when the window closes (the tick);
    None (manual Scan All) ignores it. Sends scan_complete at the end and returns
    (scanned, new_videos, skipped_busy).
    """
    total_new = 0
    attempted = 0
    skipped = 0
    for i, (channel_id, channel_name) in enumerate(channels):
        if jitter_enabled and i > 0 and jitter_max > 0:
            delay = random.uniform(0, jitter_max)
            logger.debug("Scan jitter: sleeping %.1fs before scanning %s", delay, channel_name)
            await asyncio.sleep(delay)

        # A long tick must not run on past the end of the window; the channels
        # left over stay due and go first when the window opens again
        if window is not None and not in_scan_window(*window):
            logger.info("Scan window closed; %d due channels wait for the next window",
                        len(channels) - i)
            break
        if channel_activity.flag_reason(channel_id) is not None:
            logger.info("Skipping scan for %s - the channel is busy", channel_name)
            skipped += 1
            continue
        attempted += 1

        # Fresh session per channel -- prevents rollback contamination and
        # avoids holding a connection during jitter sleeps
        try:
            async with session_factory() as db:
                channel = await db.get(Channel, channel_id)
                if not channel or not channel.enabled:
                    attempted -= 1
                    continue
                try:
                    new_count = await ChannelService(db).scan_channel(channel)
                except ScanFailed as e:
                    # Already recorded (unhealthy, SCAN_FAILED, next scan time, one notification)
                    logger.warning("Scan failed for %s: %s", channel_name, e)
                    continue
                except Exception as e:
                    await record_scan_error(db, channel, e)
                    continue
                total_new += new_count
                if new_count > 0:
                    logger.info("Found %d new videos for %s", new_count, channel_name)
        except Exception:
            # next_scan_at wasn't updated, so scan_due_channels (which treats
            # NULL as due) will retry this channel every 10-minute tick until the
            # DB recovers - a real rate-limit risk. Surface it.
            logger.warning(
                "Could not update scan status after failure for %s; it will be "
                "retried every tick until this clears", channel_name, exc_info=True,
            )

    logger.info("Scan complete: scanned %d channels, found %d new videos, %d busy skipped",
                attempted, total_new, skipped)

    if attempted:  # during Move All every tick would otherwise report "scanned 0"
        await NotificationService.broadcast("scan_complete", {
            "channels_scanned": attempted,
            "new_videos": total_new,
            "channels_skipped": skipped,
            "message": (f"Scanned {attempted} channels, found {total_new} new videos"
                        + (f", skipped {skipped} busy" if skipped else "")),
        })
    return attempted, total_new, skipped
