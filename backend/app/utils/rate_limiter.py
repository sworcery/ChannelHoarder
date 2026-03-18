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

    elapsed = time.monotonic() - _last_download_time
    delay = random.uniform(settings.DOWNLOAD_DELAY_MIN, settings.DOWNLOAD_DELAY_MAX)

    if settings.JITTER_ENABLED:
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
