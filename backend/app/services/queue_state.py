"""Single home for the queue pause and cookie-expired flags.

Values are stored JSON-encoded (what settings_service.get_setting reads). Reads
also accept older raw values ("true", cookies_expired without quotes, "").
Helpers never commit; the caller does.
"""
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AppSetting

logger = logging.getLogger(__name__)

PAUSED_KEY = "queue_paused"
REASON_KEY = "queue_pause_reason"
COOKIES_KEY = "cookies_expired"
FLAG_KEYS = (PAUSED_KEY, REASON_KEY, COOKIES_KEY)

REASON_MANUAL = "manual"
REASON_COOKIES = "cookies_expired"
REASON_DISK_FULL = "disk_full"


def _decode(raw):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _truthy(value) -> bool:
    return value is True or (isinstance(value, str) and value.strip().lower() == "true")


async def _get_row(db: AsyncSession, key: str):
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    return result.scalar_one_or_none()


async def _read(db: AsyncSession, key: str):
    row = await _get_row(db, key)
    return _decode(row.value) if row is not None else None


async def _write(db: AsyncSession, key: str, value) -> None:
    row = await _get_row(db, key)
    encoded = json.dumps(value)
    if row is not None:
        row.value = encoded
    else:
        db.add(AppSetting(key=key, value=encoded))
    await db.flush()


async def get_pause_state(db: AsyncSession) -> tuple[bool, str | None]:
    paused = _truthy(await _read(db, PAUSED_KEY))
    if not paused:
        return False, None
    reason = await _read(db, REASON_KEY)
    # A pause from before reasons existed was a manual one.
    return True, reason if isinstance(reason, str) and reason else REASON_MANUAL


async def pause_queue(db: AsyncSession, reason: str = REASON_MANUAL) -> None:
    await _write(db, PAUSED_KEY, True)
    await _write(db, REASON_KEY, reason or REASON_MANUAL)


async def resume_queue(db: AsyncSession) -> None:
    await _write(db, PAUSED_KEY, False)
    await _write(db, REASON_KEY, "")


async def get_cookies_expired(db: AsyncSession) -> bool:
    return _truthy(await _read(db, COOKIES_KEY))


async def set_cookies_expired(db: AsyncSession, expired: bool) -> None:
    await _write(db, COOKIES_KEY, bool(expired))


async def resume_if_paused_for_cookies(db: AsyncSession) -> bool:
    """Clear the cookies flag; resume only when the pause was for cookies.

    Returns True if the queue was resumed. A manual or disk_full pause stays.
    """
    await set_cookies_expired(db, False)
    # Fresh cookies: videos whose date lookups failed may work now.
    from app.services.channel_service import clear_date_lookup_failures
    await clear_date_lookup_failures(db)
    paused, reason = await get_pause_state(db)
    if paused and reason == REASON_COOKIES:
        await resume_queue(db)
        return True
    return False


async def normalize_flags(db: AsyncSession) -> None:
    """Rewrite the three flags into canonical JSON (boot-time, idempotent)."""
    for key in FLAG_KEYS:
        row = await _get_row(db, key)
        if row is None:
            continue
        value = _decode(row.value)
        if key == REASON_KEY:
            value = value if isinstance(value, str) else ""
        else:
            value = _truthy(value)
        encoded = json.dumps(value)
        if row.value != encoded:
            row.value = encoded
            logger.info("Normalised setting: %s", key)
    await db.flush()
    # Older versions left the reason behind after Resume, so a later manual pause
    # could read as a cookie pause and be lifted by a cookie upload. A cookie pause
    # always has the cookies flag set; without it, treat the pause as manual.
    paused, reason = await get_pause_state(db)
    if paused and reason == REASON_COOKIES and not await get_cookies_expired(db):
        await _write(db, REASON_KEY, REASON_MANUAL)
        logger.info("Normalised a stale cookie pause reason to manual")
        await db.flush()
