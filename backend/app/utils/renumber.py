"""Shared helper for chronological episode renumbering on a channel."""

import logging
import os

from app.services.metadata_service import write_episode_nfo
from app.services.naming_service import build_output_path
from app.utils.file_utils import move_video_files

logger = logging.getLogger(__name__)


def renumber_channel_episodes(videos: list, channel, naming_template: str | None = None) -> int:
    """Renumber episodes in chronological order, excluding shorts and livestreams.

    Videos list must be pre-sorted by upload_date ASC. Returns count of files
    that were renamed on disk.

    naming_template must be the template already resolved by
    resolve_naming_template() - this runs in a worker thread and cannot read the
    global setting itself. It falls back to the channel's own value only so
    existing callers keep working.
    """
    if naming_template is None:
        naming_template = channel.naming_template
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
                    naming_template=naming_template,
                    base_dir=channel.download_dir,
                # Keep the file's real container. Imported files can be .mkv/.webm,
                # and renaming one to .mp4 mislabels it - the move doesn't transcode.
                ) + (os.path.splitext(old_path)[1] or ".mp4")

                if old_path != new_path:
                    try:
                        move_video_files(old_path, new_path)
                        video.file_path = new_path
                        renamed += 1
                    except FileExistsError as e:
                        # A different video already occupies the target path (custom
                        # naming template without unique tokens). Skip this one rather
                        # than overwrite it or abort the whole renumber pass.
                        logger.warning("Skipping renumber move: %s", e)
                        video.file_path = old_path

            _regenerate_nfo(video, channel)

    return renamed


def _regenerate_nfo(video, channel) -> None:
    """Rewrite the episode .nfo to match current season/episode metadata.

    Videos with no file on disk are skipped. An episode .nfo is only meaningful
    next to its video, and writing one without a known file path falls back to
    the built-in template - which would recreate a "Season YYYY" folder even for
    a channel using a flat naming template.
    """
    if not video.file_path or not os.path.exists(video.file_path):
        return
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
            platform=getattr(channel, "platform", "youtube"),
        )
    except Exception as e:
        logger.warning("Failed to regenerate NFO for %s: %s", video.video_id, e)
