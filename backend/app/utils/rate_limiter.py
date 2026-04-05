import asyncio
import random
import time
import logging

from app.config import settings

logger = logging.getLogger(__name__)

_last_download_time: float = 0.0


async def wait_for_rate_limit():
    """Wait an appropriate amount of time before the next download."""
    global _last_download_time

    if _last_download_time == 0.0:
        _last_download_time = time.monotonic()
        return

    # Read delay settings from DB (falls back to config.py defaults)
    from app.database import async_session
    from app.services.settings_service import get_setting

    async with async_session() as db:
        delay_min = await get_setting(db, "download_delay_min")
        delay_max = await get_setting(db, "download_delay_max")
        jitter_enabled = await get_setting(db, "jitter_enabled")

    elapsed = time.monotonic() - _last_download_time
    delay = random.uniform(delay_min, delay_max)

    if jitter_enabled:
        jitter = random.uniform(0, 10)
        delay += jitter

    remaining = delay - elapsed
    if remaining > 0:
        logger.debug("Rate limiting: waiting %.1f seconds", remaining)
        await asyncio.sleep(remaining)

    _last_download_time = time.monotonic()


def mark_download_complete():
    """Record that a download just finished (updates rate limit timer)."""
    global _last_download_time
    _last_download_time = time.monotonic()
