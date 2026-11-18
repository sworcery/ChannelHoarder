import logging

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def flag_cookies_expired(db: AsyncSession) -> None:
    """Flag cookies as expired and auto-pause the download queue.

    Sets both 'cookies_expired' and 'queue_paused' AppSettings to 'true'.
    Does NOT delete the cookie file - the user can re-upload or let
    Tampermonkey/watcher push fresh ones. Deleting the file causes a loop
    where freshly pushed cookies get invalidated by in-flight failures.
    """
    from app.services.queue_state import REASON_COOKIES, get_pause_state, pause_queue, set_cookies_expired

    await set_cookies_expired(db, True)
    paused, _reason = await get_pause_state(db)
    if not paused:  # keep an existing manual / disk_full pause as it is
        await pause_queue(db, REASON_COOKIES)

    logger.warning("Cookies flagged as expired - queue auto-paused (cookie file preserved)")
