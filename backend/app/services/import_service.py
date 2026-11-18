"""Import existing video files into ChannelHoarder.

Scans a user-specified folder for video files, fuzzy-matches them against
known videos for a channel, then moves/renames them into the proper
directory structure and writes Plex-compatible NFO metadata.
"""

import json
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
from app.utils.file_utils import channel_folder_name, move_sidecar_files, validate_download_path

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


def _exact_id_match(entry: Path, by_video_id: dict) -> "Video | None":
    """Return the video whose id is in the file's [tag] or sibling .info.json."""
    # The default template puts the id last, so check tags from last to first.
    for tag in reversed(re.findall(r"\[([^\[\]]+)\]", entry.stem)):
        if tag in by_video_id:
            return by_video_id[tag]
    info_path = entry.with_suffix(".info.json")
    if info_path.is_file():
        try:
            with open(info_path, encoding="utf-8") as f:
                info_id = json.load(f).get("id")
        except (OSError, ValueError, AttributeError):
            return None
        if isinstance(info_id, str) and info_id in by_video_id:
            return by_video_id[info_id]
    return None


# yt-dlp format streams left by an unfinished merge: .f137, .f251-drc, .f251-1,
# .fhls-1080p, .fdash-..., plus .temp. Never adopt these as finished videos.
_PARTIAL_STEM_RE = re.compile(r"\.(f[\w-]+|temp)$", re.IGNORECASE)


def _file_key(path: str):
    """(device, inode) of an existing file, so two paths to one file compare equal."""
    try:
        st = os.stat(path)
        return (st.st_dev, st.st_ino)
    except OSError:
        return None


def _is_partial_download(entry: Path) -> bool:
    """yt-dlp part-files (.f137.mp4, .temp.mp4) and empty files are never real videos."""
    if _PARTIAL_STEM_RE.search(entry.stem):
        return True
    try:
        return entry.stat().st_size == 0
    except OSError:
        return True


async def load_owned_paths(db: AsyncSession) -> set[str]:
    """Every file path recorded on any video (any status, any channel), normalized."""
    paths = await db.execute(select(Video.file_path).where(Video.file_path.is_not(None)))
    return {os.path.normpath(p) for p in paths.scalars().all() if p}


async def scan_folder_for_imports(
    db: AsyncSession,
    channel_id: int,
    folder_path: str,
    threshold: float = 0.75,
    exact_id_only: bool = False,
    owned_paths: set[str] | None = None,
) -> list[dict]:
    """Scan a folder and match files against un-downloaded videos for a channel.

    Returns a list of match dicts: {file_path, file_name, file_size, matched_video_id,
    video_title, match_confidence, season, episode}.

    With exact_id_only (the automatic path, no user review) a file matches only
    when its [video_id] tag or sibling .info.json id equals the video's id, and
    files already recorded as any video's file_path are skipped (in both modes).
    owned_paths lets a caller scanning many folders load that set once.
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

    owned = owned_paths if owned_paths is not None else await load_owned_paths(db)
    by_video_id: dict[str, Video] = {}
    exact_candidates: dict[int, list[tuple[Path, Video]]] = {}
    effective_template = None
    if exact_id_only:
        by_video_id = {v.video_id: v for v in videos if v.video_id}
        from app.services.naming_service import resolve_naming_template
        effective_template = await resolve_naming_template(db, channel.naming_template)

    # Scan folder for video files
    matches = []
    for entry in folder.iterdir():
        if not entry.is_file():
            continue
        ext = entry.suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            continue

        file_title = entry.stem  # filename without extension

        if os.path.normpath(str(entry)) in owned:
            continue

        if exact_id_only:
            if _is_partial_download(entry):
                continue
            exact = _exact_id_match(entry, by_video_id)
            if exact:
                exact_candidates.setdefault(exact.id, []).append((entry, exact))
            continue

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

    # Exact mode: at most one file per video, preferring the one already at its target.
    for candidates in exact_candidates.values():
        video = candidates[0][1]
        target_base = build_output_path(
            channel_name=channel.channel_name,
            video_title=video.title,
            video_id=video.video_id,
            upload_date=video.upload_date,
            season=video.season,
            episode=video.episode,
            naming_template=effective_template,
            base_dir=channel.download_dir,
            folder_name=channel_folder_name(channel),
        )
        entry = min(
            (e for e, _ in candidates),
            key=lambda e: (
                os.path.normpath(os.path.splitext(str(e))[0]) != os.path.normpath(target_base),
                e.name,
            ),
        )
        matches.append({
            "file_path": str(entry),
            "file_name": entry.name,
            "file_size": entry.stat().st_size,
            "matched_video_id": video.id,
            "video_db_id": video.id,
            "video_youtube_id": video.video_id,
            "video_title": video.title,
            "match_confidence": 100.0,
            "season": video.season,
            "episode": video.episode,
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

    from app.config import settings as app_settings
    from app.services.naming_service import resolve_naming_template
    effective_template = await resolve_naming_template(db, channel.naming_template)

    rows = await db.execute(select(Video.id, Video.file_path).where(Video.file_path.is_not(None)))
    owned = {os.path.normpath(p): vid for vid, p in rows.all() if p}
    owned_by_key: dict | None = None  # built on first use: stats every owned file once

    for match in matches:
        file_path = match["file_path"]
        video_id = match["matched_video_id"]

        try:
            validate_download_path(file_path, app_settings.allowed_download_roots)

            video = await db.get(Video, video_id)
            if not video:
                errors.append(f"Video ID {video_id} not found")
                continue

            if video.channel_id != channel_id:
                errors.append(f"Video {video_id} doesn't belong to channel {channel_id}")
                continue

            if video.status in ("downloading", "completed"):
                errors.append(f"Video {video_id} is {video.status}, not importing")
                continue

            src = Path(file_path)
            if not src.exists():
                errors.append(f"File not found: {file_path}")
                continue

            # Never take a file another video already records as its own, including
            # when it is reached through another path (a second mount of the share).
            owner = owned.get(os.path.normpath(file_path))
            if owner is None:
                if owned_by_key is None:
                    owned_by_key = {}
                    for p, vid in owned.items():
                        key = _file_key(p)
                        if key is not None:
                            owned_by_key[key] = vid
                owner = owned_by_key.get(_file_key(file_path))
            if owner is not None and owner != video.id:
                errors.append(
                    f"{os.path.basename(file_path)} already belongs to another video"
                )
                continue

            # A video that still has its own file (e.g. queued for a quality upgrade)
            # never adopts a different file in its place.
            if (video.file_path and os.path.exists(video.file_path)
                    and not os.path.samefile(video.file_path, src)):
                errors.append(f"Video {video_id} already has a file; not importing another")
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
                naming_template=effective_template,
                base_dir=channel.download_dir,
                folder_name=channel_folder_name(channel),
            )
            target_path = target_base + ext

            # Create target directory
            os.makedirs(os.path.dirname(target_path), exist_ok=True)

            # Never overwrite an existing file (it may belong to another video):
            # the target name, any other video extension with the same stem, or its .nfo.
            src_base = os.path.splitext(str(src))[0]
            blockers = [target_path] + [target_base + e for e in sorted(VIDEO_EXTENSIONS)]
            nfo = target_base + ".nfo"
            if os.path.exists(nfo) and not (
                os.path.exists(src_base + ".nfo") and os.path.samefile(nfo, src_base + ".nfo")
            ):
                blockers.append(nfo)
            clash = next(
                (b for b in blockers
                 if os.path.exists(b) and not os.path.samefile(src, b)),
                None,
            )
            if clash:
                logger.warning(
                    "Import skipped: %s already exists (source %s)", clash, file_path,
                )
                errors.append(
                    f"Target already exists, not overwriting: {os.path.basename(clash)}"
                )
                continue

            # Move the file (no-op when it already sits at the target), then its
            # sidecars (info.json, thumbnail, subtitles) without overwriting.
            if not os.path.exists(target_path):
                shutil.move(str(src), target_path)
                # Sidecars are found by name, so skip them while another video file
                # with the same name stays behind: they may be that video's.
                if not any(os.path.exists(src_base + e) for e in VIDEO_EXTENSIONS):
                    move_sidecar_files(str(src), target_path, overwrite=False)
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
            safe_name = channel_folder_name(channel)
            from app.config import settings
            base_dir = channel.download_dir or settings.DOWNLOAD_DIR
            tvshow_nfo = os.path.join(base_dir, safe_name, "tvshow.nfo")

            if not os.path.exists(tvshow_nfo):
                write_tvshow_nfo(
                    channel_name=channel.channel_name,
                    channel_id=channel.channel_id,
                    channel_url=channel.channel_url,
                    description=channel.description,
                    thumbnail_url=channel.thumbnail_url,
                    base_dir=channel.download_dir,
                    folder_name=channel_folder_name(channel),
                )

            # Update video record. quality_downloaded must be set or the daily
            # quality-upgrade job would treat the import as below-cutoff and
            # re-download every imported file; the channel's configured quality
            # is the best available assumption for a pre-existing file.
            video.status = "completed"
            video.file_path = target_path
            owned[os.path.normpath(target_path)] = video.id
            video.file_size = os.path.getsize(target_path)
            video.quality_downloaded = channel.quality
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
