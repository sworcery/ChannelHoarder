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
            allowed = ", ".join(allowed_roots)
            raise ValueError(
                f"Path {path} is not under an allowed download directory "
                f"(currently allowed: {allowed}). To use another location, map it as a "
                f"volume in your container and add its container path to the "
                f"EXTRA_DOWNLOAD_DIRS environment variable (comma-separated for multiple)."
            )
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


def channel_folder_name(channel) -> str:
    """Folder segment for a channel under its download root.

    Channels added after per-channel folders existed carry an explicit
    folder_name; older rows (NULL/empty) keep the name-based folder they
    always had, so nothing moves.
    """
    explicit = getattr(channel, "folder_name", None)
    if explicit:
        return explicit
    return sanitize_filename(channel.channel_name)


def move_video_files(old_path: str, new_path: str, overwrite: bool = False, sidecars: bool = True) -> int:
    """Move a video file and (unless sidecars is False) all associated sidecar files.

    Returns the number of files moved.
    """
    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    _place_file(old_path, new_path, overwrite)
    if not sidecars:
        return 1
    return 1 + move_sidecar_files(old_path, new_path, overwrite)


def _place_file(src: str, dst: str, overwrite: bool) -> None:
    """Move src to dst without ever deleting the only copy of a file.

    If dst already exists and is the very same file (case-insensitive filesystem,
    two bind mounts of one folder), nothing on disk is touched.
    A different file at dst is replaced only when overwrite is set and the sizes
    match; otherwise FileExistsError is raised and both files are left alone.
    """
    if os.path.exists(dst):
        if os.path.samefile(src, dst):
            # Already reachable at dst (same path, case-insensitive name, another
            # mount of the same folder, or a hard link). Touch nothing: any rename
            # here could strand or delete the only copy. Callers record dst.
            return
        if not overwrite:
            raise FileExistsError(f"Refusing to move {src} onto existing file {dst}")
        if os.path.getsize(src) != os.path.getsize(dst):
            raise FileExistsError(
                f"Refusing to replace {dst} with a different file {src} (sizes differ)"
            )
    # Same filesystem: one atomic rename (replacing an allowed dst in place).
    try:
        os.replace(src, dst)
        return
    except OSError:
        pass
    # Another filesystem (e.g. a different bind mount): copy under a temporary
    # name and only then take dst's name, so a failed copy never leaves a
    # half-written file at dst that would block every retry.
    tmp = dst + ".chmove"
    try:
        shutil.copyfile(src, tmp)
        try:
            shutil.copystat(src, tmp)
        except OSError:
            pass  # e.g. NTFS/exFAT refusing permission or time changes
        os.replace(tmp, dst)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    os.remove(src)


def move_sidecar_files(old_path: str, new_path: str, overwrite: bool = False) -> int:
    """Move the sidecar files of a video (nfo, thumbnails, subtitles). Returns the count moved."""
    moved = 0
    old_base = os.path.splitext(old_path)[0]
    new_base = os.path.splitext(new_path)[0]
    for ext in ASSOCIATED_EXTENSIONS:
        old_extra = old_base + ext
        if os.path.exists(old_extra):
            try:
                _place_file(old_extra, new_base + ext, overwrite)
                moved += 1
            except Exception as e:
                logger.warning("Failed to move sidecar %s: %s", old_extra, e)
    return moved


_CHANNEL_IMAGE_STEMS = ("poster", "fanart", "banner", "folder", "logo", "landscape", "clearart")
_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _is_channel_metadata_name(name: str) -> bool:
    low = name.lower()
    if low == "tvshow.nfo":
        return True
    stem, ext = os.path.splitext(low)
    if ext not in _IMAGE_EXTS:
        return False
    if stem in _CHANNEL_IMAGE_STEMS:
        return True
    # Kodi/Plex season artwork in the show root, e.g. season01-poster.jpg
    return stem.startswith("season") and stem.rsplit("-", 1)[-1] in _CHANNEL_IMAGE_STEMS


def remove_channel_metadata(channel_dir: str) -> int:
    """Delete a channel folder's own metadata files (tvshow.nfo, channel and season artwork).

    Never touches other files. Returns the number of files removed.
    """
    removed = 0
    if not os.path.isdir(channel_dir) or os.path.islink(channel_dir):
        return removed
    dirs = [channel_dir]
    try:
        dirs += [
            os.path.join(channel_dir, d) for d in os.listdir(channel_dir)
            if d.lower().startswith("season") and os.path.isdir(os.path.join(channel_dir, d))
        ]
    except OSError:
        pass
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            path = os.path.join(d, name)
            if os.path.isfile(path) and _is_channel_metadata_name(name):
                try:
                    os.remove(path)
                    removed += 1
                except OSError as e:
                    logger.warning("Failed to delete %s: %s", path, e)
    return removed


def move_channel_metadata(old_channel_dir: str, new_channel_dir: str) -> tuple[int, list[str]]:
    """Move a channel's own metadata (tvshow.nfo, channel and season artwork).

    Never overwrites a destination file and never touches other files.
    Returns (files moved, error messages).
    """
    moved = 0
    errors: list[str] = []
    if not os.path.isdir(old_channel_dir):
        return moved, errors

    def _move_matching(src_dir: str, dst_dir: str) -> None:
        nonlocal moved
        try:
            names = os.listdir(src_dir)
        except OSError as e:
            errors.append(f"{src_dir}: {e}")
            return
        for name in names:
            src = os.path.join(src_dir, name)
            if not os.path.isfile(src) or not _is_channel_metadata_name(name):
                continue
            dst = os.path.join(dst_dir, name)
            if os.path.exists(dst):
                continue
            try:
                os.makedirs(dst_dir, exist_ok=True)
                shutil.move(src, dst)
                moved += 1
            except Exception as e:
                errors.append(f"{src}: {e}")

    _move_matching(old_channel_dir, new_channel_dir)
    try:
        subdirs = [d for d in os.listdir(old_channel_dir)
                   if d.lower().startswith("season") and os.path.isdir(os.path.join(old_channel_dir, d))]
    except OSError:
        subdirs = []
    for d in subdirs:
        _move_matching(os.path.join(old_channel_dir, d), os.path.join(new_channel_dir, d))
    return moved, errors


def count_files(root: str) -> int:
    """Count files left under root."""
    return sum(len(files) for _, _, files in os.walk(root))


def remove_empty_dirs(root: str) -> None:
    """Remove empty folders under root and root itself if empty, bottom-up.

    Uses os.rmdir only, so anything holding a file is kept. Never goes above root.
    """
    if not os.path.isdir(root) or os.path.islink(root):
        return
    for dirpath, _dirs, _files in os.walk(root, topdown=False):
        try:
            os.rmdir(dirpath)
        except OSError:
            pass


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
