import asyncio
import logging

from sqlalchemy import select

from app.database import async_session
from app.models import AppSetting
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)


async def check_cookie_recovery():
    """Scheduled task: quickly auto-resume the queue when it was paused due to
    expired cookies and downloading works again.

    The full health check that also recovers from this only runs every 6 hours,
    so this lightweight check fills the gap - it does nothing unless the queue is
    currently paused with reason 'cookies_expired', and only then runs the cheap
    yt-dlp auth test to decide whether to resume.
    """
    async with async_session() as db:
        result = await db.execute(
            select(AppSetting).where(
                AppSetting.key.in_(["cookies_expired", "queue_paused", "queue_pause_reason"])
            )
        )
        flags = {s.key: s for s in result.scalars().all()}

        paused = flags.get("queue_paused")
        reason = flags.get("queue_pause_reason")
        if not (paused and paused.value == "true" and reason and reason.value == "cookies_expired"):
            return  # Not paused for cookies - nothing to recover

        # Cheap auth test (flat metadata extract) - run in a thread to avoid
        # blocking the event loop.
        ytdlp = YtdlpService()
        success, _ = await asyncio.to_thread(ytdlp.test_download_capability)
        if not success:
            return  # Still failing - leave it paused

        expired = flags.get("cookies_expired")
        if expired:
            expired.value = "false"
        paused.value = "false"
        reason.value = ""
        await db.commit()

        logger.info("Cookie recovery: yt-dlp auth test passed, cookies_expired cleared, queue resumed")
        await NotificationService.broadcast("cookies_refreshed", {
            "message": "Cookies are working again. Queue resumed automatically.",
        })
