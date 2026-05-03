import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

QUICK_DOWNLOAD_DIR = "/tmp/quick-downloads"
MAX_AGE_SECONDS = 7 * 24 * 60 * 60


async def cleanup_quick_downloads() -> None:
    """Remove files older than 7 days from the quick-downloads staging directory."""
    download_dir = Path(QUICK_DOWNLOAD_DIR)
    if not download_dir.exists():
        return

    now = time.time()
    removed = 0

    for entry in download_dir.iterdir():
        if not entry.is_file():
            continue
        try:
            mtime = entry.stat().st_mtime
            if now - mtime > MAX_AGE_SECONDS:
                entry.unlink()
                logger.info("Deleted stale quick-download file: %s", entry.name)
                removed += 1
        except Exception:
            logger.exception("Failed to remove quick-download file: %s", entry.name)

    if removed > 0:
        logger.info("Quick download cleanup: removed %d files", removed)
