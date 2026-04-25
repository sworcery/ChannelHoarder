import asyncio
import random
import time
import logging

logger = logging.getLogger(__name__)

_last_dispatch_time: float = 0.0
_dispatch_lock = asyncio.Lock()


async def wait_for_rate_limit():
    """Wait an appropriate amount of time before dispatching the next download.

    Uses a lock so concurrent downloads are staggered correctly: each one
    waits for the delay since the previous dispatch, not since its own start.
    """
    global _last_dispatch_time

    async with _dispatch_lock:
        from app.database import async_session
        from app.services.settings_service import get_setting

        async with async_session() as db:
            delay_min = await get_setting(db, "download_delay_min")
            delay_max = await get_setting(db, "download_delay_max")
            jitter_enabled = await get_setting(db, "jitter_enabled")

        if _last_dispatch_time > 0:
            elapsed = time.monotonic() - _last_dispatch_time
            delay = random.uniform(delay_min, delay_max)

            if jitter_enabled:
                delay += random.uniform(0, 10)

            remaining = delay - elapsed
            if remaining > 0:
                logger.debug("Rate limiting: waiting %.1f seconds", remaining)
                await asyncio.sleep(remaining)

        _last_dispatch_time = time.monotonic()
