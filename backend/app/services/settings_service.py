"""Runtime settings reader  - reads from DB with config.py fallbacks."""

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
    "global_schedule_cron": lambda: settings.DEFAULT_SCAN_CRON,
    "log_level": lambda: settings.LOG_LEVEL,
}


async def get_setting(db: AsyncSession, key: str, default=None):
    """Read a setting from the DB, falling back to config.py default."""
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting is not None:
        return json.loads(setting.value)
    if key in _DEFAULTS:
        return _DEFAULTS[key]()
    return default
