import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

logger = logging.getLogger(__name__)


async def flag_cookies_expired(db: AsyncSession) -> None:
    """Flag cookies as expired and auto-pause the download queue.

    Sets both 'cookies_expired' and 'queue_paused' AppSettings to 'true',
    and deletes the local cookies file so the watcher can re-import.
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

    settings.cookies_path.unlink(missing_ok=True)
    logger.warning("Cookies expired — queue auto-paused, cookies deleted")
