import logging
import os
import re
import shutil
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

ASSOCIATED_EXTENSIONS = [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt", ".en.ass"]


def validate_url_scheme(url: str) -> None:
    """Reject URLs with unsafe schemes (file://, ftp://, data:, etc.)."""
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https", ""):
        raise ValueError(f"Unsupported URL scheme: {parsed.scheme}://")


def validate_download_path(path: str, allowed_roots: list[str] | None = None) -> Path:
    """Validate that a download directory path is safe.

    Rejects paths containing '..' traversal and enforces containment
    within allowed_roots when provided.
    """
    resolved = Path(path).resolve()
    if ".." in Path(path).parts:
        raise ValueError(f"Path traversal not allowed: {path}")
    if allowed_roots:
        if not any(resolved.is_relative_to(Path(root).resolve()) for root in allowed_roots):
            raise ValueError(f"Path {path} is not under any allowed download directory")
    return resolved


def escape_like(search: str) -> str:
    """Escape SQL LIKE/ILIKE wildcard characters in a search string."""
    return search.replace("%", "\\%").replace("_", "\\_")


def parse_upload_date(date_str: str | None):
    """Parse a date string in YYYYMMDD or ISO-8601 format."""
    if not date_str:
        return None
    try:
        from datetime import date
        if len(date_str) == 8:
            return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
        return date.fromisoformat(date_str[:10])
    except (ValueError, TypeError):
        return None


def sanitize_filename(name: str, max_length: int = 200) -> str:
    """Make a string safe for use as a filename."""
    # Normalize unicode
    name = unicodedata.normalize("NFKD", name)

    # Replace unsafe characters
    unsafe = r'[<>:"/\\|?*\x00-\x1f]'
    name = re.sub(unsafe, "_", name)

    # Collapse multiple underscores/spaces
    name = re.sub(r"[_\s]+", " ", name).strip()

    # Remove leading/trailing dots and spaces
    name = name.strip(". ")

    # Truncate
    if len(name) > max_length:
        name = name[:max_length].rstrip(". ")

    return name or "Untitled"


def move_video_files(old_path: str, new_path: str, overwrite: bool = False) -> int:
    """Move a video file and all associated sidecar files to a new location.

    Returns the number of files moved.
    """
    moved = 0
    os.makedirs(os.path.dirname(new_path), exist_ok=True)

    if overwrite and os.path.exists(new_path):
        os.remove(new_path)
    shutil.move(old_path, new_path)
    moved += 1

    old_base = os.path.splitext(old_path)[0]
    new_base = os.path.splitext(new_path)[0]
    for ext in ASSOCIATED_EXTENSIONS:
        old_extra = old_base + ext
        if os.path.exists(old_extra):
            new_extra = new_base + ext
            try:
                if overwrite and os.path.exists(new_extra):
                    os.remove(new_extra)
                shutil.move(old_extra, new_extra)
                moved += 1
            except Exception as e:
                logger.warning("Failed to move sidecar %s: %s", old_extra, e)

    return moved


def delete_video_files(file_path: str) -> int:
    """Delete a video file and all associated sidecar files.

    Returns the number of files removed.
    """
    removed = 0
    base = os.path.splitext(file_path)[0]

    if os.path.exists(file_path):
        try:
            os.remove(file_path)
            removed += 1
        except Exception as e:
            logger.warning("Failed to delete %s: %s", file_path, e)

    for ext in ASSOCIATED_EXTENSIONS:
        extra = base + ext
        if os.path.exists(extra):
            try:
                os.remove(extra)
                removed += 1
            except Exception as e:
                logger.warning("Failed to delete %s: %s", extra, e)

    return removed
