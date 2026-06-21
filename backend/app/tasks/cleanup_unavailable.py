import logging

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DownloadLog, DownloadQueue, Video
from app.services.channel_service import _UNAVAILABLE_TITLES

logger = logging.getLogger(__name__)


async def cleanup_unavailable_videos(db: AsyncSession) -> int:
    """Remove stored Video rows that are private/deleted/unavailable placeholders
    (e.g. titled "[Private video]"), left over from before the scan-time filter
    existed. These entries are never downloadable, so they have no files on disk.

    Returns the number of rows removed.
    """
    # Candidates: title matches a marker (case- and whitespace-insensitive). The
    # status/file_path guards ensure we never remove a row that points at a real
    # downloaded file.
    result = await db.execute(
        select(Video.id).where(
            func.lower(func.trim(Video.title)).in_(list(_UNAVAILABLE_TITLES)),
            Video.status != "downloaded",
            Video.file_path.is_(None),
        )
    )
    ids = [row[0] for row in result.all()]
    if not ids:
        return 0

    # Delete child rows first (SQLite may not cascade without PRAGMA foreign_keys).
    await db.execute(sa_delete(DownloadLog).where(DownloadLog.video_id.in_(ids)))
    await db.execute(sa_delete(DownloadQueue).where(DownloadQueue.video_id.in_(ids)))
    await db.execute(sa_delete(Video).where(Video.id.in_(ids)))
    await db.commit()
    logger.info("Cleanup: removed %d private/deleted/unavailable video listing(s)", len(ids))
    return len(ids)
