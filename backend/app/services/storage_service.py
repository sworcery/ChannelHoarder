import os
import shutil
from pathlib import Path

from app.config import settings


def get_storage_usage() -> dict:
    """Get storage usage breakdown."""
    download_dir = Path(settings.DOWNLOAD_DIR)
    disk = shutil.disk_usage(str(download_dir))

    channels = {}
    total_used = 0

    if download_dir.exists():
        for channel_dir in download_dir.iterdir():
            if channel_dir.is_dir():
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
