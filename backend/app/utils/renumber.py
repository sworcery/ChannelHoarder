"""Shared helper for chronological episode renumbering on a channel."""

import logging
import os

from app.services.naming_service import build_output_path
from app.utils.file_utils import move_video_files

logger = logging.getLogger(__name__)


def renumber_channel_episodes(videos: list, channel) -> int:
    """Renumber episodes in chronological order, excluding shorts and livestreams.

    Videos list must be pre-sorted by upload_date ASC. Returns count of files
    that were renamed on disk.
    """
    season_counts: dict[int, int] = {}
    renamed = 0

    for video in videos:
        # Shorts and livestreams are excluded from episode numbering
        if video.is_short or video.is_livestream:
            if video.episode != 0:
                video.episode = 0
            continue

        season = video.upload_date.year
        season_counts.setdefault(season, 0)
        season_counts[season] += 1
        new_episode = season_counts[season]

        if video.season != season or video.episode != new_episode:
            old_path = video.file_path
            video.season = season
            video.episode = new_episode

            if old_path and os.path.exists(old_path):
                new_path = build_output_path(
                    channel_name=channel.channel_name,
                    video_title=video.title,
                    video_id=video.video_id,
                    upload_date=video.upload_date,
                    season=season,
                    episode=new_episode,
                    naming_template=channel.naming_template,
                    base_dir=channel.download_dir,
                ) + ".mp4"

                if old_path != new_path:
                    move_video_files(old_path, new_path)
                    video.file_path = new_path
                    renamed += 1

    return renamed
