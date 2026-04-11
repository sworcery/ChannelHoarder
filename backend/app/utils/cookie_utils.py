import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

logger = logging.getLogger(__name__)


async def flag_cookies_expired(db: AsyncSession) -> None:
    """Flag cookies as expired and auto-pause the download queue.

    Sets both 'cookies_expired' and 'queue_paused' AppSettings to 'true'.
    Does NOT delete the cookie file - the user can re-upload or let
    Tampermonkey/watcher push fresh ones. Deleting the file causes a loop
    where freshly pushed cookies get invalidated by in-flight failures.
    """
    for key, value in [("cookies_expired", "true"), ("queue_paused", "true")]:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == key)
        )
        flag = result.scalar_one_or_none()
        if flag:
            flag.value = value
        else:
            db.add(AppSetting(key=key, value=value))

    logger.warning("Cookies flagged as expired - queue auto-paused (cookie file preserved)")
