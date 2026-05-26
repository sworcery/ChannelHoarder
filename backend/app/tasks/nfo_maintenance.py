"""Scheduled task: regenerate stale or missing episode .nfo sidecar files.

Compares each downloaded video's .nfo content against the current DB metadata
and rewrites any that are missing or out of sync (wrong episode/season).
"""

import logging
import os
import xml.etree.ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.database import async_session
from app.models import Channel, Video
from app.services.metadata_service import write_episode_nfo

logger = logging.getLogger(__name__)


async def sync_nfo_files():
    """Check all downloaded videos for missing or stale .nfo files and regenerate."""
    async with async_session() as db:
        result = await db.execute(
            select(Video)
            .where(Video.status == "downloaded", Video.file_path.isnot(None))
            .options(joinedload(Video.channel))
        )
        videos = result.scalars().all()

        if not videos:
            return

        repaired = 0
        for video in videos:
            channel = video.channel
            if not channel or not video.file_path:
                continue

            nfo_path = os.path.splitext(video.file_path)[0] + ".nfo"

            if not os.path.exists(nfo_path):
                if not os.path.exists(video.file_path):
                    continue
                _write_nfo(video, channel)
                repaired += 1
                continue

            if _nfo_is_stale(nfo_path, video):
                _write_nfo(video, channel)
                repaired += 1

        if repaired:
            logger.info("NFO maintenance: regenerated %d stale/missing .nfo files", repaired)


def _nfo_is_stale(nfo_path: str, video) -> bool:
    """Check if the .nfo content disagrees with the video's current metadata."""
    try:
        tree = ET.parse(nfo_path)
        root = tree.getroot()

        nfo_season = root.findtext("season")
        nfo_episode = root.findtext("episode")
        nfo_title = root.findtext("title")

        if nfo_season is not None and int(nfo_season) != video.season:
            return True
        if nfo_episode is not None and int(nfo_episode) != video.episode:
            return True
        if nfo_title is not None and nfo_title != video.title:
            return True
    except Exception:
        return True

    return False


def _write_nfo(video, channel) -> None:
    try:
        write_episode_nfo(
            channel_name=channel.channel_name,
            video_title=video.title,
            video_id=video.video_id,
            description=video.description,
            upload_date=video.upload_date,
            season=video.season,
            episode=video.episode,
            duration=video.duration,
            thumbnail_url=video.thumbnail_url,
            video_file_path=video.file_path,
            platform=channel.platform,
        )
    except Exception as e:
        logger.warning("NFO maintenance: failed to write .nfo for %s: %s", video.video_id, e)
