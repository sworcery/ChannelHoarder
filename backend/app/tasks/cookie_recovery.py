import asyncio
import logging

from app.database import async_session
from app.services.notification_service import NotificationService
from app.services.queue_state import REASON_COOKIES, get_pause_state, resume_if_paused_for_cookies
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
        paused, reason = await get_pause_state(db)
        if not (paused and reason == REASON_COOKIES):
            return  # Not paused for cookies - nothing to recover

        # Cheap auth test (flat metadata extract) - run in a thread to avoid
        # blocking the event loop.
        ytdlp = YtdlpService()
        success, _ = await asyncio.to_thread(ytdlp.test_download_capability)
        if not success:
            return  # Still failing - leave it paused

        await resume_if_paused_for_cookies(db)
        await db.commit()

        logger.info("Cookie recovery: yt-dlp auth test passed, cookies_expired cleared, queue resumed")
        await NotificationService.broadcast("cookies_refreshed", {
            "message": "Cookies are working again. Queue resumed automatically.",
        })
