import logging
import shutil
import threading
import time
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

# The per-channel size walk stats every file in the library (tens of thousands of
# files on slow/FUSE mounts like Unraid user shares), which can take 20s+. Doing
# that on every dashboard request made /dashboard/stats hang, so the walk result
# is cached: requests are always served from cache, and a stale cache triggers a
# single background refresh.
_CACHE_TTL_SECONDS = 10 * 60
_cache_lock = threading.Lock()
_cached_usage: dict | None = None
_cache_computed_at = 0.0
_refresh_in_flight = False


def get_storage_usage(custom_dirs: list[str] | None = None) -> dict:
    """Get storage usage, served from cache.

    The first call (empty cache) computes synchronously. Afterwards, calls return
    the cached result instantly; when the cache is older than the TTL, one
    background refresh is kicked off so freshness catches up off-request. Disk
    totals (statvfs) are cheap and always live.
    """
    global _refresh_in_flight
    with _cache_lock:
        cached = _cached_usage
        stale = time.monotonic() - _cache_computed_at > _CACHE_TTL_SECONDS
        if cached is not None and stale and not _refresh_in_flight:
            _refresh_in_flight = True
            threading.Thread(
                target=_refresh_cache, args=(custom_dirs,), daemon=True,
                name="storage-usage-refresh",
            ).start()

    if cached is None:
        return _refresh_cache(custom_dirs)

    # Disk free/total are a single cheap statvfs - always report live values.
    disk = shutil.disk_usage(settings.DOWNLOAD_DIR)
    return {**cached, "disk_total": disk.total, "disk_used": disk.used, "disk_free": disk.free}


def _refresh_cache(custom_dirs: list[str] | None) -> dict:
    global _cached_usage, _cache_computed_at, _refresh_in_flight
    try:
        result = _compute_storage_usage(custom_dirs)
        with _cache_lock:
            _cached_usage = result
            _cache_computed_at = time.monotonic()
        return result
    finally:
        with _cache_lock:
            _refresh_in_flight = False


def _compute_storage_usage(custom_dirs: list[str] | None = None) -> dict:
    """Full storage walk (expensive - see cache above)."""
    download_dir = Path(settings.DOWNLOAD_DIR)
    disk = shutil.disk_usage(str(download_dir))

    channels = {}
    total_used = 0
    scanned_dirs = set()

    # Scan default download directory
    if download_dir.exists():
        for channel_dir in download_dir.iterdir():
            # Skip hidden dirs (e.g. .channelhoarder-tmp attempt workspaces)
            if channel_dir.is_dir() and not channel_dir.name.startswith("."):
                size = _dir_size(channel_dir)
                channels[channel_dir.name] = size
                total_used += size
        scanned_dirs.add(str(download_dir.resolve()))

    # Scan custom per-channel download directories
    if custom_dirs:
        for custom_dir in custom_dirs:
            if not custom_dir:
                continue
            custom_path = Path(custom_dir)
            # Skip if it's the same as or under the default dir (already scanned)
            try:
                resolved = str(custom_path.resolve())
                if resolved in scanned_dirs:
                    continue
                scanned_dirs.add(resolved)
            except Exception:
                continue

            if custom_path.exists():
                for channel_dir in custom_path.iterdir():
                    if (channel_dir.is_dir() and not channel_dir.name.startswith(".")
                            and channel_dir.name not in channels):
                        size = _dir_size(channel_dir)
                        channels[channel_dir.name] = size
                        total_used += size

    return {
        "disk_total": disk.total,
        "disk_used": disk.used,
        "disk_free": disk.free,
        "downloads_used": total_used,
        "channels": channels,
    }


def _dir_size(path: Path) -> int:
    """Calculate total size of a directory."""
    total = 0
    try:
        for entry in path.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
    except PermissionError:
        pass
    return total


def format_bytes(size: int) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} PB"
