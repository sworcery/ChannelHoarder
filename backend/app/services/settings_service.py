"""Runtime settings reader. One way to read a setting everywhere.

Precedence: saved (DB) value, then the env/config value, then the caller's default.
Values are stored JSON-encoded and come back typed like the fallback, so a stored
string "false" reads as False.
"""

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

logger = logging.getLogger(__name__)

# Map setting keys to their config.py defaults
_DEFAULTS = {
    "download_delay_min": lambda: settings.DOWNLOAD_DELAY_MIN,
    "download_delay_max": lambda: settings.DOWNLOAD_DELAY_MAX,
    "jitter_enabled": lambda: settings.JITTER_ENABLED,
    "user_agent_rotation": lambda: settings.USER_AGENT_ROTATION,
    "max_concurrent_downloads": lambda: settings.MAX_CONCURRENT_DOWNLOADS,
    "max_retries": lambda: settings.MAX_RETRIES,
    "default_quality": lambda: settings.DEFAULT_QUALITY,
    "log_level": lambda: settings.LOG_LEVEL,
    "download_queue_order": lambda: "queued",
    "rss_check_minutes": lambda: 0,
}

_FALSE_WORDS = {"false", "0", "no", "off", "n", "f", ""}


def decode_value(raw):
    """Decode a stored value; a value that is not valid JSON is the plain string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def coerce(value, fallback):
    """Give value the type of fallback (bool, int, float). Returns fallback if it can't."""
    if fallback is None or value is None:
        return value if value is not None else fallback
    try:
        if isinstance(fallback, bool):
            if isinstance(value, str):
                return value.strip().lower() not in _FALSE_WORDS
            return bool(value)
        if isinstance(fallback, int):
            if isinstance(value, bool):
                return int(value)
            return int(value)
        if isinstance(fallback, float):
            return float(value)
    except (TypeError, ValueError):
        return fallback
    return value


def fallback_for(key: str, default=None):
    """The env/config value for key, else the caller's default."""
    if key in _DEFAULTS:
        return _DEFAULTS[key]()
    return default


async def get_all_settings() -> dict:
    """Read all settings from the DB as a flat dict."""
    from app.database import async_session
    async with async_session() as db:
        result = await db.execute(select(AppSetting))
        return {s.key: decode_value(s.value) for s in result.scalars().all()}


async def get_setting(db: AsyncSession, key: str, default=None):
    """Read a setting: saved value, then env/config, then default. Typed like the fallback."""
    fallback = fallback_for(key, default)
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is not None:
        return coerce(decode_value(setting.value), fallback)
    return fallback


async def apply_log_level(db: AsyncSession | None = None) -> str:
    """Apply the saved settings that synchronous code reads from the config object,
    and return the Log Level now in force (env value if none saved).

    Log Level sets the root logger; User-Agent rotation is read by yt-dlp option
    building, which runs in worker threads and can't query the database.
    Called at startup and after settings are saved or imported.
    """
    if db is None:
        from app.database import async_session
        async with async_session() as db:
            return await apply_log_level(db)
    level = str(await get_setting(db, "log_level")).upper()
    if not isinstance(getattr(logging, level, None), int):
        level = "INFO"
    logging.getLogger().setLevel(getattr(logging, level))
    settings.USER_AGENT_ROTATION = bool(await get_setting(db, "user_agent_rotation"))
    return level


async def get_settings(db: AsyncSession, keys: list[str]) -> dict:
    """get_setting for several keys with one query. Missing keys get their fallback."""
    result = await db.execute(select(AppSetting).where(AppSetting.key.in_(keys)))
    saved = {s.key: s.value for s in result.scalars().all()}
    out = {}
    for key in keys:
        fallback = fallback_for(key)
        out[key] = coerce(decode_value(saved[key]), fallback) if key in saved else fallback
    return out
