import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models import Channel

logger = logging.getLogger(__name__)

TEMP_DIR_NAME = ".channelhoarder-tmp"
# Well beyond the 10-min download stall timeout and the 4h stale-queue unstick,
# so a dir this old can only belong to an abandoned attempt.
MAX_AGE_SECONDS = 6 * 60 * 60


async def cleanup_download_temp() -> None:
    """Remove abandoned per-attempt download temp dirs.

    Each download attempt works inside <download_root>/.channelhoarder-tmp/<uuid>/
    (see DownloadService). Successful attempts remove their own dir; stalled or
    failed attempts leave it behind (an orphaned yt-dlp thread may still hold it),
    and this sweeper reclaims them once they are safely old.
    """
    roots = {settings.DOWNLOAD_DIR}
    try:
        async with async_session() as db:
            result = await db.execute(
                select(Channel.download_dir).where(Channel.download_dir.isnot(None)).distinct()
            )
            roots.update(r for (r,) in result.all() if r)
    except Exception:
        logger.exception("Temp cleanup: could not list channel download roots")

    now = time.time()
    removed = 0
    for root in roots:
        tmp_root = Path(root) / TEMP_DIR_NAME
        if not tmp_root.is_dir():
            continue
        for entry in tmp_root.iterdir():
            if not entry.is_dir():
                continue
            try:
                if now - entry.stat().st_mtime > MAX_AGE_SECONDS:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
            except OSError:
                logger.exception("Temp cleanup: failed to remove %s", entry)

    if removed:
        logger.info("Download temp cleanup: removed %d abandoned attempt dir(s)", removed)
