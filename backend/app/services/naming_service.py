import os
from datetime import date

from app.config import settings
from app.utils.file_utils import sanitize_filename

DEFAULT_TEMPLATE = "{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]"


def build_output_path(
    channel_name: str,
    video_title: str,
    video_id: str,
    upload_date: date,
    season: int,
    episode: int,
    naming_template: str | None = None,
    base_dir: str | None = None,
) -> str:
    """Build the full output path for a downloaded video (without extension)."""
    template = naming_template or DEFAULT_TEMPLATE
    base = base_dir or settings.DOWNLOAD_DIR

    safe_channel = sanitize_filename(channel_name)
    safe_title = sanitize_filename(video_title)
    upload_date_str = upload_date.strftime("%Y%m%d")

    path = template.format(
        channel_name=safe_channel,
        season=season,
        episode=f"{episode:03d}",
        title=safe_title,
        upload_date=upload_date_str,
        video_id=video_id,
    )

    return os.path.join(base, path)


def preview_naming(
    template: str,
    channel_name: str = "TechChannel",
    title: str = "How to Build a PC",
    upload_date: str = "20240315",
    video_id: str = "dQw4w9WgXcQ",
    season: int = 2024,
    episode: int = 3,
) -> str:
    """Preview a naming template with sample data."""
    safe_channel = sanitize_filename(channel_name)
    safe_title = sanitize_filename(title)

    try:
        return template.format(
            channel_name=safe_channel,
            season=season,
            episode=f"{episode:03d}",
            title=safe_title,
            upload_date=upload_date,
            video_id=video_id,
        )
    except (KeyError, IndexError, ValueError) as e:
        return f"Invalid template: {e}"
