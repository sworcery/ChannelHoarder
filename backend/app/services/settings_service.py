"""Runtime settings reader. One way to read a setting everywhere.

Precedence: saved (DB) value, then the env/config value, then the caller's default.
Values are stored JSON-encoded and come back typed like the fallback, so a stored
string "false" reads as False.
"""

import json
import logging
import os

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

logger = logging.getLogger(__name__)

# apply_runtime_settings copies a saved User-Agent rotation onto the config object
# (yt-dlp option building reads it from worker threads). Keep the env value as it was
# at import, so the "env tier" the defaults table reads is never the saved row.
_ENV_USER_AGENT_ROTATION = settings.USER_AGENT_ROTATION

_QUALITIES = ("best", "2160p", "1080p", "720p", "480p")
_LOG_LEVELS = ("debug", "info", "warning", "error", "critical")


# The env tier is checked the way the schema checks a saved value, so the page
# never gets a value it can't show or can't save back.
def _env_quality() -> str:
    value = str(settings.DEFAULT_QUALITY).strip()
    return value if value in _QUALITIES else "best"


def _env_max_retries(value) -> int:
    return max(0, min(100, int(value)))


def _env_log_level(value) -> str:
    value = str(value).strip().lower()
    return value if value in _LOG_LEVELS else "info"


# One defaults table: every SettingsUpdate field, with the env/config value where
# there is one, else the built-in default. The type of the default types the read.
_DEFAULTS = {
    "default_quality": lambda: _env_quality(),
    "download_delay_min": lambda: settings.DOWNLOAD_DELAY_MIN,
    "download_delay_max": lambda: settings.DOWNLOAD_DELAY_MAX,
    "jitter_enabled": lambda: settings.JITTER_ENABLED,
    "scan_jitter_enabled": lambda: True,
    "scan_jitter_max_seconds": lambda: 300,
    "scan_window_start_hour": lambda: None,
    "scan_window_end_hour": lambda: None,
    "scan_min_interval_hours": lambda: 12,
    "rss_check_minutes": lambda: 0,
    "manual_scan_cooldown_minutes": lambda: 5,
    "max_concurrent_downloads": lambda: settings.MAX_CONCURRENT_DOWNLOADS,
    "max_retries": lambda: _env_max_retries(settings.MAX_RETRIES),
    "download_queue_order": lambda: "queued",
    "user_agent_rotation": lambda: _ENV_USER_AGENT_ROTATION,
    "naming_template": lambda: None,
    "telegram_bot_token": lambda: None,
    "telegram_chat_id": lambda: None,
    "pushover_app_token": lambda: None,
    "pushover_user_key": lambda: None,
    "webhook_events": lambda: None,
    "shorts_enabled": lambda: False,
    "livestreams_enabled": lambda: False,
    "subtitles_enabled": lambda: False,
    "chapters_enabled": lambda: False,
    "sponsorblock_mode": lambda: "off",
    "discord_webhook_url": lambda: None,
    "set_permissions": lambda: False,
    "chmod_folder": lambda: None,
    "chmod_file": lambda: None,
    "chown_group": lambda: None,
    "max_video_duration": lambda: 0,
    "log_level": lambda: _env_log_level(settings.LOG_LEVEL),
}

MIGRATION_KEY = "_migrated_env_overrides_2_0"
# env var -> setting key, for the one-time upgrade step
_ENV_OVERRIDES = {
    "MAX_RETRIES": "max_retries",
    "USER_AGENT_ROTATION": "user_agent_rotation",
    "LOG_LEVEL": "log_level",
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


async def get_setting(db: AsyncSession, key: str, default=None):
    """Read a setting: saved value, then env/config, then default. Typed like the fallback."""
    fallback = fallback_for(key, default)
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is not None:
        return coerce(decode_value(setting.value), fallback)
    return fallback


async def get_effective_settings(db: AsyncSession) -> dict:
    """What the app uses for every setting: the saved row, else env/config, else the
    default, plus any other saved keys. Internal keys (names starting with "_") are left out."""
    from app.services.channel_service import DATE_LOOKUP_KEY_PREFIX
    result = await db.execute(
        select(AppSetting).where(~AppSetting.key.like(DATE_LOOKUP_KEY_PREFIX + "%"))
    )
    saved = {s.key: s.value for s in result.scalars().all()}
    out = {}
    for key, raw in saved.items():
        if key not in _DEFAULTS and not key.startswith("_"):
            out[key] = decode_value(raw)
    for key in _DEFAULTS:
        fallback = fallback_for(key)
        out[key] = coerce(decode_value(saved[key]), fallback) if key in saved else fallback
    return out


async def read_scan_window(db: AsyncSession) -> tuple[int | None, int | None]:
    """The saved scan window as (start, end), or (None, None) when there is none or
    the saved one doesn't validate. The one reader for the tick, RSS, next-scan
    times, import and the start-up stagger."""
    from app.utils.scan_window import usable_window
    v = await get_settings(db, ["scan_window_start_hour", "scan_window_end_hour"])
    return usable_window(v["scan_window_start_hour"], v["scan_window_end_hour"])


async def migrate_env_overrides(db: AsyncSession) -> None:
    """One-time upgrade step. Until 1.9.50 MAX_RETRIES, USER_AGENT_ROTATION and
    LOG_LEVEL came from the environment while the settings page saved its own
    defaults; a saved row now wins. Where the env var is set and differs from the
    saved row, the env value goes into the row, so the upgrade changes nothing.
    LOG_LEVEL is always set (Dockerfile, Unraid template, compose), so a saved log
    level is reset to the env value once, which is what 1.9.50 did after a restart.
    Runs once (marked by an internal key); a later Save sticks."""
    if await db.get(AppSetting, MIGRATION_KEY) is not None:
        return
    for env_name, key in _ENV_OVERRIDES.items():
        if env_name not in os.environ:
            continue
        row = await db.get(AppSetting, key)
        if row is None:
            continue
        raw_env = os.environ[env_name].strip()
        try:
            if key == "max_retries":
                env_value = _env_max_retries(raw_env)
            elif key == "user_agent_rotation":
                env_value = raw_env.lower() not in _FALSE_WORDS
            else:
                env_value = _env_log_level(raw_env)
        except ValueError:
            continue
        if coerce(decode_value(row.value), env_value) == env_value:
            continue
        logger.info("Setting %s: using the %s environment value %r over the saved %s",
                    key, env_name, env_value, row.value)
        row.value = json.dumps(env_value)
    db.add(AppSetting(key=MIGRATION_KEY, value=json.dumps(True)))
    await db.commit()


async def apply_runtime_settings(db: AsyncSession | None = None) -> str:
    """Apply the saved settings that synchronous code reads from the config object,
    and return the Log Level now in force (env value if none saved).

    Log Level sets the root logger; User-Agent rotation is read by yt-dlp option
    building, which runs in worker threads and can't query the database.
    Called at startup and after settings are saved or imported.
    """
    if db is None:
        from app.database import async_session
        async with async_session() as db:
            return await apply_runtime_settings(db)
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
