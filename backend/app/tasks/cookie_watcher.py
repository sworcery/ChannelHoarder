"""Watch /cookies directory for cookie files dropped via network share.

Flow:
1. User drops cookies.txt on their network share mapped to /cookies
2. Watcher detects it, moves it to /config/cookies.txt, deletes from /cookies
3. Clears expired flag, unpauses queue
4. When cookies expire, the expiry handler deletes /config/cookies.txt
5. Watcher waits for a new file to appear in /cookies

Runs every 60 seconds via scheduler.
"""

import logging
import shutil
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models import AppSetting
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)


def _find_cookie_file() -> Path | None:
    """Find the newest .txt file in the watch directory."""
    watch_dir = Path(settings.COOKIE_WATCH_DIR)
    if not watch_dir.is_dir():
        return None

    txt_files = [f for f in watch_dir.glob("*.txt") if f.stat().st_size >= 50]
    if not txt_files:
        return None

    # Return the most recently modified one
    return max(txt_files, key=lambda f: f.stat().st_mtime)


async def watch_cookie_file() -> None:
    """Check the cookie watch directory for new cookie files."""
    source = _find_cookie_file()
    if source is None:
        return

    try:
        file_size = source.stat().st_size
    except OSError:
        return

    # Move to the config cookies.txt location
    dest = settings.cookies_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(str(source), str(dest))
        source.unlink()
        logger.info("Cookie file imported: %s (%d bytes) → deleted from watch dir", source.name, file_size)
    except Exception as e:
        logger.error("Failed to import cookie file %s: %s", source, e)
        return

    # Clear expired flag and unpause queue
    async with async_session() as db:
        for key, value in [("cookies_expired", "false"), ("queue_paused", "false")]:
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            flag = result.scalar_one_or_none()
            if flag and flag.value != value:
                flag.value = value
        await db.commit()

    logger.info("Cookies imported, expired flag cleared, queue unpaused")

    await NotificationService.broadcast("cookies_refreshed", {
        "message": f"Fresh cookies imported from {source.name} ({file_size} bytes). Queue resumed.",
    })
