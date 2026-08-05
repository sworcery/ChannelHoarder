import logging
import os
import re
from datetime import date

from app.config import settings
from app.utils.file_utils import sanitize_filename

logger = logging.getLogger(__name__)

DEFAULT_TEMPLATE = "{channel_name}/Season {season}/S{season}E{episode} - {title} - {upload_date} - [{video_id}]"

ALLOWED_TEMPLATE_VARS = {"channel_name", "season", "episode", "title", "upload_date", "video_id"}

# Matches a {season} field (with optional format spec) inside a template
_SEASON_FIELD_RE = re.compile(r"\{season(?::[^}]*)?\}")


def template_uses_season_folder(template: str | None) -> bool:
    """True if the template places videos inside a season-based directory.

    A Plex 'Season XX/poster.jpg' artwork file only makes sense when videos live
    in season folders. Users with a flat template (e.g.
    '{channel_name}/{upload_date}_{title}') must not get empty 'Season YYYY'
    folders created solely to hold a poster.
    """
    directory = (template or DEFAULT_TEMPLATE).rpartition("/")[0]
    return bool(_SEASON_FIELD_RE.search(directory))


async def resolve_naming_template(db, channel_template: str | None) -> str | None:
    """Resolve which naming template applies to a channel's files.

    Precedence: the channel's own override, then the global "Default Naming
    Template" setting, then None (meaning DEFAULT_TEMPLATE).

    Every path that builds or moves a video file must resolve the template this
    way. Reading only the per-channel value silently falls back to
    DEFAULT_TEMPLATE for anyone who set just the global template, so a scan
    would keep moving finished downloads into a Season folder they were never
    downloaded into.
    """
    if channel_template:
        return channel_template

    import json

    from sqlalchemy import select

    from app.models import AppSetting

    try:
        result = await db.execute(select(AppSetting).where(AppSetting.key == "naming_template"))
        setting = result.scalar_one_or_none()
        if setting is not None:
            value = json.loads(setting.value)
            if isinstance(value, str) and value:
                # A template saved by an older build was never validated. Falling back
                # to the default beats letting it raise, which would fail every scan's
                # rename pass and mark otherwise-healthy channels as unhealthy.
                validate_template(value)
                return value
    except Exception as e:
        logger.warning("Ignoring global naming template, using default layout: %s", e)
    return None


def validate_template(template: str) -> None:
    """Reject templates with attribute access, indexing, or unknown variables."""
    # Find all {field_name} references, allowing optional format specs like {:03d}
    fields = re.findall(r"\{([^}]*)\}", template)
    for field in fields:
        # Strip format spec (everything after ":")
        var_name = field.split(":")[0].strip()
        if not var_name:
            continue
        if "." in var_name or "[" in var_name:
            raise ValueError(f"Template variable '{var_name}' contains unsafe attribute access or indexing")
        if var_name not in ALLOWED_TEMPLATE_VARS:
            raise ValueError(f"Unknown template variable '{var_name}'. Allowed: {', '.join(sorted(ALLOWED_TEMPLATE_VARS))}")

    # The scan above only sees balanced {...} pairs, so a stray brace ("{title")
    # or a bad format spec would slip through and raise from str.format() later -
    # during a rename pass, far from the settings screen that accepted it.
    # Formatting once with placeholder values proves the template is usable.
    try:
        template.format(
            channel_name="x", season=1, episode="001",
            title="x", upload_date="20240101", video_id="x",
        )
    except (KeyError, IndexError, ValueError) as e:
        raise ValueError(f"Template is not a valid format string: {e}")


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
    validate_template(template)
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
    validate_template(template)
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
