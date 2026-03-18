"""Import existing video files into ChannelHoarder.

Scans a user-specified folder for video files, fuzzy-matches them against
known videos for a channel, then moves/renames them into the proper
directory structure and writes Plex-compatible NFO metadata.
"""

import logging
import os
import re
import shutil
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, DownloadQueue, Video
from app.services.metadata_service import write_episode_nfo, write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.utils.file_utils import sanitize_filename

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".ts", ".m4v"}


def _normalize_title(text: str) -> str:
    """Normalize a title for fuzzy comparison."""
    text = unicodedata.normalize("NFKD", text)
    text = text.lower()
    # Remove common file naming artifacts
    text = re.sub(r"\[.*?\]", "", text)
    text = re.sub(r"\(.*?\)", "", text)
    # Strip punctuation and extra whitespace
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _match_score(file_title: str, video_title: str) -> float:
    """Return similarity ratio between two titles (0.0 to 1.0)."""
    a = _normalize_title(file_title)
    b = _normalize_title(video_title)
    return SequenceMatcher(None, a, b).ratio()


async def scan_folder_for_imports(
    db: AsyncSession,
    channel_id: int,
    folder_path: str,
    threshold: float = 0.75,
) -> list[dict]:
    """Scan a folder and match files against un-downloaded videos for a channel.

    Returns a list of match dicts: {file_path, file_name, file_size, matched_video_id,
    video_title, match_confidence, season, episode}.
    """
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise ValueError(f"Channel {channel_id} not found")

    folder = Path(folder_path)
    if not folder.is_dir():
        raise ValueError(f"Folder not found: {folder_path}")

    # Get all non-completed videos for this channel
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.status.in_(["pending", "failed", "queued"]))
    )
    videos = result.scalars().all()

    if not videos:
        return []

    # Scan folder for video files
    matches = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            continue

        file_title = entry.stem  # filename without extension

        best_match = None
        best_score = 0.0
        for video in videos:
            score = _match_score(file_title, video.title)
            if score > best_score:
                best_score = score
                best_match = video

        if best_match and best_score >= threshold:
            matches.append({
                "file_path": str(entry),
                "file_name": entry.name,
                "file_size": entry.stat().st_size,
                "matched_video_id": best_match.id,
                "video_db_id": best_match.id,
                "video_youtube_id": best_match.video_id,
                "video_title": best_match.title,
                "match_confidence": round(best_score * 100, 1),
                "season": best_match.season,
                "episode": best_match.episode,
            })

    # Sort by confidence descending
    matches.sort(key=lambda m: m["match_confidence"], reverse=True)
    return matches


async def import_matched_files(
    db: AsyncSession,
    channel_id: int,
    matches: list[dict],
) -> dict:
    """Import confirmed matches: move files, write NFOs, update DB records.

    Each match dict must have: file_path, matched_video_id.
    Returns {imported: int, errors: list[str]}.
    """
    channel = await db.get(Channel, channel_id)
    if not channel:
        raise ValueError(f"Channel {channel_id} not found")

    imported = 0
    errors = []

    for match in matches:
        file_path = match["file_path"]
        video_id = match["matched_video_id"]

        try:
            video = await db.get(Video, video_id)
            if not video:
                errors.append(f"Video ID {video_id} not found")
                continue

            if video.channel_id != channel_id:
                errors.append(f"Video {video_id} doesn't belong to channel {channel_id}")
                continue

            src = Path(file_path)
            if not src.exists():
                errors.append(f"File not found: {file_path}")
                continue

            ext = src.suffix

            # Build the target path
            target_base = build_output_path(
                channel_name=channel.channel_name,
                video_title=video.title,
                video_id=video.video_id,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                naming_template=channel.naming_template,
                base_dir=channel.download_dir,
            )
            target_path = target_base + ext

            # Create target directory
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # Move the file
            shutil.move(str(src), target_path)
            logger.info("Imported %s -> %s", file_path, target_path)

            # Write episode NFO
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
                video_file_path=target_path,
            )

            # Write tvshow.nfo if it doesn't exist yet
            safe_name = sanitize_filename(channel.channel_name)
            base_dir = channel.download_dir or None
            tvshow_nfo = os.path.join(
                base_dir or "",
                safe_name,
                "tvshow.nfo",
            )
            if base_dir:
                tvshow_nfo = os.path.join(base_dir, safe_name, "tvshow.nfo")
            else:
                from app.config import settings
                tvshow_nfo = os.path.join(settings.DOWNLOAD_DIR, safe_name, "tvshow.nfo")

            if not os.path.exists(tvshow_nfo):
                write_tvshow_nfo(
                    channel_name=channel.channel_name,
                    channel_id=channel.channel_id,
                    channel_url=channel.channel_url,
                    description=channel.description,
                    thumbnail_url=channel.thumbnail_url,
                    base_dir=channel.download_dir,
                )

            # Update video record
            video.status = "completed"
            video.file_path = target_path
            video.file_size = os.path.getsize(target_path)
            video.downloaded_at = datetime.now(timezone.utc)
            video.error_code = None
            video.error_message = None
            video.error_details = None

            # Remove from queue if present
            queue_result = await db.execute(
                select(DownloadQueue).where(DownloadQueue.video_id == video.id)
            )
            queue_entry = queue_result.scalar_one_or_none()
            if queue_entry:
                await db.delete(queue_entry)

            # Update channel counters
            channel.downloaded_count += 1

            imported += 1

        except Exception as e:
            logger.error("Failed to import %s: %s", file_path, e, exc_info=True)
            errors.append(f"Failed to import {os.path.basename(file_path)}: {e}")

    await db.commit()
    return {"imported": imported, "errors": errors}
