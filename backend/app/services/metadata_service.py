"""Generate Plex-compatible NFO metadata files for channels and episodes.

Plex reads these NFO files via the "Local Media Assets" agent to populate
series titles, descriptions, episode names, air dates, and artwork.

File structure:
  /downloads/ChannelName/
    tvshow.nfo          <- series metadata
    poster.jpg          <- channel thumbnail as series poster
    Season 2024/
      S2024E001 - Title.mp4
      S2024E001 - Title.nfo   <- episode metadata
"""

import logging
import os
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree, indent

import httpx

from app.config import settings
from app.utils.file_utils import sanitize_filename

logger = logging.getLogger(__name__)


def write_tvshow_nfo(
    channel_name: str,
    channel_id: str,
    channel_url: str,
    description: str | None = None,
    thumbnail_url: str | None = None,
    base_dir: str | None = None,
    platform: str = "youtube",
) -> str | None:
    """Write tvshow.nfo in the channel's root download folder.

    Returns the path written, or None on failure.
    """
    from app.utils.platform_utils import get_platform_label
    platform_label = get_platform_label(platform)

    safe_name = sanitize_filename(channel_name)
    series_dir = os.path.join(base_dir or settings.DOWNLOAD_DIR, safe_name)
    os.makedirs(series_dir, exist_ok=True)

    nfo_path = os.path.join(series_dir, "tvshow.nfo")

    root = Element("tvshow")
    SubElement(root, "title").text = channel_name
    SubElement(root, "showtitle").text = channel_name
    SubElement(root, "plot").text = description or f"{platform_label} channel: {channel_name}"
    SubElement(root, "genre").text = platform_label
    SubElement(root, "studio").text = platform_label
    SubElement(root, "premiered").text = ""

    uid = SubElement(root, "uniqueid", type=platform, default="true")
    uid.text = channel_id

    if thumbnail_url:
        thumb = SubElement(root, "thumb", aspect="poster")
        thumb.text = thumbnail_url

    SubElement(root, "namedseason", number="1").text = "Season 1"

    indent(root)
    tree = ElementTree(root)

    try:
        tree.write(nfo_path, encoding="unicode", xml_declaration=True)
        logger.info("Wrote tvshow.nfo for %s", channel_name)
    except Exception as e:
        logger.error("Failed to write tvshow.nfo for %s: %s", channel_name, e)
        return None

    # Download channel poster
    if thumbnail_url:
        _download_image(thumbnail_url, os.path.join(series_dir, "poster.jpg"))

    return nfo_path


def write_episode_nfo(
    channel_name: str,
    video_title: str,
    video_id: str,
    description: str | None,
    upload_date: date,
    season: int,
    episode: int,
    duration: int | None = None,
    thumbnail_url: str | None = None,
    video_file_path: str | None = None,
    platform: str = "youtube",
) -> str | None:
    """Write an episode .nfo file alongside the video file.

    If video_file_path is provided, the NFO is written next to it.
    Otherwise, it's derived from the naming template.

    Returns the path written, or None on failure.
    """
    # Determine NFO path: same as video file but with .nfo extension
    if video_file_path:
        # Strip video extension, add .nfo
        base = os.path.splitext(video_file_path)[0]
        nfo_path = base + ".nfo"
    else:
        # Fall back to constructing the path
        from app.services.naming_service import build_output_path
        base_path = build_output_path(
            channel_name=channel_name,
            video_title=video_title,
            video_id=video_id,
            upload_date=upload_date,
            season=season,
            episode=episode,
        )
        nfo_path = base_path + ".nfo"

    os.makedirs(os.path.dirname(nfo_path), exist_ok=True)

    root = Element("episodedetails")
    SubElement(root, "title").text = video_title
    SubElement(root, "season").text = str(season)
    SubElement(root, "episode").text = str(episode)
    SubElement(root, "plot").text = description or ""
    SubElement(root, "aired").text = upload_date.isoformat()

    uid = SubElement(root, "uniqueid", type=platform, default="true")
    uid.text = video_id

    if duration:
        # Duration in minutes for Plex
        SubElement(root, "runtime").text = str(max(1, duration // 60))

    if thumbnail_url:
        thumb = SubElement(root, "thumb")
        thumb.text = thumbnail_url

    indent(root)
    tree = ElementTree(root)

    try:
        tree.write(nfo_path, encoding="unicode", xml_declaration=True)
        logger.debug("Wrote episode NFO: %s", nfo_path)
    except Exception as e:
        logger.error("Failed to write episode NFO for %s: %s", video_id, e)
        return None

    return nfo_path


def _download_image(url: str, dest_path: str) -> bool:
    """Download an image URL to a local path. Returns True on success."""
    if os.path.exists(dest_path):
        return True  # Already have it

    try:
        with httpx.Client(timeout=30, follow_redirects=True) as client:
            resp = client.get(url)
            resp.raise_for_status()
            with open(dest_path, "wb") as f:
                f.write(resp.content)
        logger.info("Downloaded poster: %s", dest_path)
        return True
    except Exception as e:
        logger.warning("Failed to download image %s: %s", url, e)
        return False
