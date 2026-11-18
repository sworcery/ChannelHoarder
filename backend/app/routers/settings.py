import json
import logging
import random
from datetime import date
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import AppSetting, Channel
from app.schemas import ChannelCreate, SettingsUpdate, NamingPreviewRequest, NamingPreviewResponse
from app.services.channel_service import DATE_LOOKUP_KEY_PREFIX, pick_channel_folder
from app.services.queue_state import FLAG_KEYS
from app.utils.file_utils import channel_folder_name, sanitize_filename
from app.services.naming_service import preview_naming, DEFAULT_TEMPLATE

logger = logging.getLogger(__name__)
router = APIRouter()

# Path to the Tampermonkey userscript template
# In Docker: /app/app/routers/settings.py -> /app/tools/
# In dev:    backend/app/routers/settings.py -> tools/ (repo root)
_app_root = Path(__file__).resolve().parent.parent.parent  # backend/ or /app/
USERSCRIPT_TEMPLATE = _app_root / "tools" / "tampermonkey_cookie_exporter.user.js"
if not USERSCRIPT_TEMPLATE.exists():
    # Fall back to repo root layout (dev environment)
    USERSCRIPT_TEMPLATE = _app_root.parent / "tools" / "tampermonkey_cookie_exporter.user.js"


@router.get("/")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    from app.services.settings_service import get_effective_settings
    # What the app uses: the saved row, else env/config, else the default
    settings_dict = {
        k: v for k, v in (await get_effective_settings(db)).items() if k not in FLAG_KEYS
    }
    logger.debug("Returning %d settings: %s", len(settings_dict), list(settings_dict.keys()))
    return settings_dict


async def _check_update(db: AsyncSession, update_data: dict) -> None:
    """Cross-field checks a SettingsUpdate can't do alone. Raises HTTPException 400."""
    # Validate scan window if either hour field is being updated
    if "scan_window_start_hour" in update_data or "scan_window_end_hour" in update_data:
        from app.utils.scan_window import validate_scan_window
        # Pull current values to combine with incoming updates
        from app.services.settings_service import get_settings
        saved = await get_settings(db, list(_HOUR_KEYS))

        def _hour(key: str):
            value = update_data[key] if key in update_data else saved[key]
            try:
                return int(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        start_h = _hour("scan_window_start_hour")
        end_h = _hour("scan_window_end_hour")
        try:
            validate_scan_window(start_h, end_h)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    # Reject a malformed naming template at the door. It is applied by every
    # rename/renumber path, not just downloads, so saving a broken one would
    # otherwise surface as repeated scan failures long after the fact.
    if update_data.get("naming_template"):
        from app.services.naming_service import validate_template
        try:
            validate_template(update_data["naming_template"])
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid naming template: {e}")


class _ImportExtras(BaseModel):
    """Channel fields ChannelCreate doesn't carry; a wrong type skips the channel."""
    channel_name: str = "Imported"
    channel_url: str = ""
    platform: str = "youtube"
    include_shorts: bool = False
    include_livestreams: bool = False
    prioritize_downloads: bool = False


_IMPORT_CHECKED_FIELDS = (
    "quality", "quality_cutoff", "naming_template", "min_quality", "preferred_codec", "min_video_duration",
    "title_filter", "title_filter_is_regex", "title_filter_mode", "enabled", "auto_download",
)
_HOUR_KEYS = ("scan_window_start_hour", "scan_window_end_hour")


def _parse_settings_block(raw: dict) -> dict:
    """Validate raw settings through SettingsUpdate; return the typed values.
    Raises HTTPException 400 naming the first bad field."""
    try:
        return SettingsUpdate.model_validate(raw).model_dump(exclude_unset=True)
    except ValidationError as e:
        err = e.errors()[0]
        field = ".".join(str(p) for p in err["loc"]) or "settings"
        raise HTTPException(status_code=400, detail=f"Invalid value for '{field}': {err['msg']}")


@router.put("/")
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    update_data = body.model_dump(exclude_unset=True)
    await _check_update(db, update_data)

    logger.info("Saving settings: %s", list(update_data.keys()))
    for key, value in update_data.items():
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(value)
            logger.debug("Updated setting %s = %s", key, json.dumps(value))
        else:
            db.add(AppSetting(key=key, value=json.dumps(value)))
            logger.debug("Created setting %s = %s", key, json.dumps(value))

    await db.commit()
    logger.info("Settings committed to database")

    # Apply runtime-reactive settings immediately (log level, User-Agent rotation)
    if {"log_level", "user_agent_rotation"} & set(update_data):
        from app.services.settings_service import apply_runtime_settings
        logger.info("Log level is %s", await apply_runtime_settings(db))

    return {"message": "Settings updated", "updated": list(update_data.keys())}


@router.get("/userscript.user.js")
async def get_userscript(request: Request):
    """Serve the Tampermonkey cookie exporter with server URL pre-configured."""
    if not USERSCRIPT_TEMPLATE.exists():
        raise HTTPException(status_code=404, detail="Userscript template not found")

    script = USERSCRIPT_TEMPLATE.read_text(encoding="utf-8")

    # Build the base URL from the incoming request
    forwarded_proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    forwarded_host = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    base_url = f"{forwarded_proto}://{forwarded_host}".rstrip("/")

    download_url = f"{base_url}/api/v1/settings/userscript.user.js"

    # Inject server URL and update URLs
    script = script.replace(
        'const PRECONFIGURED_SERVER_URL = "";',
        f'const PRECONFIGURED_SERVER_URL = "{base_url}";',
    )
    script = script.replace("__DOWNLOAD_URL__", download_url)
    script = script.replace("__UPDATE_URL__", download_url)

    return PlainTextResponse(content=script, media_type="text/javascript")


@router.post("/webhook/test")
async def test_webhook(provider: str = "telegram"):
    """Send a test notification to verify webhook configuration."""
    from app.services.webhook_service import send_test_notification
    return await send_test_notification(provider)


async def _import_folder_name(db, ch_data: dict, taken: set[str] | None = None) -> str:
    """Folder for an imported channel: the one it was exported with, else the plain
    name-based folder old backups always used, so a restore finds its files.

    taken: folders of channels that existed before the import; if the folder is one
    of those (a different channel already lives there), a free one is picked.
    """
    from app.services.channel_service import _folder_key, pick_free_folder, taken_folder_keys

    if taken is None:
        taken = await taken_folder_keys(db)
    saved = ch_data.get("folder_name")
    name = ch_data.get("channel_name")
    if isinstance(saved, str) and saved.strip():
        folder = sanitize_filename(saved)
    elif isinstance(name, str) and name.strip():
        folder = sanitize_filename(name)
    else:
        return await pick_channel_folder(
            db, "Imported", ch_data.get("platform", "youtube"), ch_data.get("download_dir"))
    # Importing into an install that already has a different channel in that folder
    # would mix the two; pick a free one instead.
    if _folder_key(ch_data.get("download_dir"), folder) in taken:
        folder = pick_free_folder(taken, name or folder, ch_data.get("platform", "youtube"),
                                  ch_data.get("download_dir"))
    return folder


def _import_from_date(ch_data: dict):
    """Parse download_from_date (ISO string); fall back to legacy download_from_year (Jan 1)."""
    raw = ch_data.get("download_from_date")
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            return None
    year = ch_data.get("download_from_year")
    if year:
        try:
            return date(int(year), 1, 1)
        except (TypeError, ValueError):
            return None
    return None


def _import_retention_days(raw):
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return val if 1 <= val <= 36500 else None


@router.get("/export")
async def export_config(db: AsyncSession = Depends(get_db)):
    """Export all settings and channels as JSON for backup."""
    # Settings
    result = await db.execute(
        select(AppSetting).where(~AppSetting.key.like(DATE_LOOKUP_KEY_PREFIX + "%"))
    )
    settings_list = result.scalars().all()
    SENSITIVE_KEYS = {
        "telegram_bot_token", "pushover_app_token", "pushover_user_key",
        "youtube_api_key", "telegram_chat_id", "discord_webhook_url",
    } | set(FLAG_KEYS)  # queue pause / cookie state belongs to this install, not the backup
    settings_dict = {}
    for s in settings_list:
        if s.key in SENSITIVE_KEYS or s.key.startswith("_"):
            continue
        try:
            settings_dict[s.key] = json.loads(s.value)
        except (json.JSONDecodeError, TypeError):
            settings_dict[s.key] = s.value

    # Channels (no videos  - just channel config)
    result = await db.execute(select(Channel))
    channels = result.scalars().all()
    channels_list = [
        {
            "channel_id": ch.channel_id,
            "channel_name": ch.channel_name,
            "channel_url": ch.channel_url,
            "platform": ch.platform,
            "quality": ch.quality,
            "quality_cutoff": ch.quality_cutoff,
            "min_quality": ch.min_quality,
            "preferred_codec": ch.preferred_codec,
            "min_video_duration": ch.min_video_duration,
            "download_from_date": ch.download_from_date.isoformat() if ch.download_from_date else None,
            "retention_days": ch.retention_days,
            "naming_template": ch.naming_template,
            "download_dir": ch.download_dir,
            "folder_name": channel_folder_name(ch),
            "enabled": ch.enabled,
            "include_shorts": ch.include_shorts,
            "include_livestreams": ch.include_livestreams,
            "auto_download": ch.auto_download,
            "prioritize_downloads": ch.prioritize_downloads,
            "title_filter": ch.title_filter,
            "title_filter_is_regex": ch.title_filter_is_regex,
            "title_filter_mode": ch.title_filter_mode,
        }
        for ch in channels
    ]

    return {
        "version": "1",
        "settings": settings_dict,
        "channels": channels_list,
    }


@router.post("/import")
async def import_config(db: AsyncSession = Depends(get_db), file: UploadFile = File(...)):
    """Import settings and channels from a JSON backup."""
    content = await file.read()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON file")

    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid backup file")
    raw_settings = data.get("settings", {})
    if not isinstance(raw_settings, dict):
        raise HTTPException(status_code=400, detail="Invalid value for 'settings'")

    channels_in = data.get("channels", [])
    if not isinstance(channels_in, list):
        raise HTTPException(status_code=400, detail="Invalid value for 'channels'")

    # Each setting is validated on its own: one that no longer validates (an old
    # naming template, an out-of-range value) is skipped and the current value kept.
    # Per-install state and keys SettingsUpdate doesn't know are not imported.
    known = set(SettingsUpdate.model_fields)
    typed_settings: dict = {}
    skipped_settings: list[dict] = []
    for key, value in raw_settings.items():
        if key not in known or key in FLAG_KEYS:
            continue
        try:
            typed = _parse_settings_block({key: value})
            if key not in _HOUR_KEYS:  # the window is checked as a pair below
                await _check_update(db, typed)
        except HTTPException as e:
            skipped_settings.append({"key": key, "reason": str(e.detail)})
            continue
        typed_settings.update(typed)
    hours = {k: typed_settings[k] for k in _HOUR_KEYS if k in typed_settings}
    if hours and any(s["key"] in _HOUR_KEYS for s in skipped_settings):
        # One hour of the backup's window is bad: keep the current window whole
        # rather than mixing the backup's other hour into it.
        for k in hours:
            typed_settings.pop(k)
            skipped_settings.append({"key": k, "reason": "skipped with the other scan window hour"})
    elif hours:
        try:
            await _check_update(db, hours)
        except HTTPException as e:
            for k in hours:
                typed_settings.pop(k)
                skipped_settings.append({"key": k, "reason": str(e.detail)})

    from app.config import settings as app_config
    from app.utils.file_utils import validate_download_path

    imported_settings = 0
    imported_channels = 0
    skipped_channels: list[dict] = []

    # Merge settings
    for key, value in typed_settings.items():
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(value)
        else:
            db.add(AppSetting(key=key, value=json.dumps(value)))
        imported_settings += 1

    # Folders of channels that were here before this import. Channels restored from
    # the same backup may share a folder (old backups did), so they don't count.
    from app.services.channel_service import taken_folder_keys
    pre_existing_folders = await taken_folder_keys(db)

    # Add channels that don't exist yet
    new_channels: list[Channel] = []
    for ch_data in channels_in:
        if not isinstance(ch_data, dict):
            continue
        cid = ch_data.get("channel_id")
        if not cid:
            continue
        name = str(ch_data.get("channel_name") or cid)
        result = await db.execute(select(Channel).where(Channel.channel_id == cid))
        if result.scalar_one_or_none():
            skipped_channels.append({"channel_id": cid, "channel_name": name, "reason": "already exists"})
            continue
        # A channel whose folder isn't allowed or mounted here is skipped, never
        # imported with its path cleared.
        download_dir = ch_data.get("download_dir")
        if download_dir:
            try:
                if not isinstance(download_dir, str):
                    raise ValueError("Its download folder in the backup isn't a path")
                validate_download_path(download_dir, app_config.allowed_download_roots)
            except ValueError as e:
                skipped_channels.append({"channel_id": cid, "channel_name": name, "reason": str(e)})
                continue
        # Same checks as adding a channel by hand: a bad quality, filter mode,
        # codec or template skips this channel with a reason.
        try:
            checked = ChannelCreate.model_validate({
                "url": ch_data.get("channel_url") or cid,
                **{k: ch_data[k] for k in _IMPORT_CHECKED_FIELDS if ch_data.get(k) is not None},
            })
            extras = _ImportExtras.model_validate(
                {k: ch_data[k] for k in _ImportExtras.model_fields if ch_data.get(k) is not None}
            )
            if checked.naming_template:
                from app.services.naming_service import validate_template
                validate_template(checked.naming_template)
        except (ValidationError, ValueError, TypeError) as e:
            if isinstance(e, ValidationError):
                err = e.errors()[0]
                reason = f"Invalid value for '{'.'.join(str(p) for p in err['loc'])}': {err['msg']}"
            else:
                reason = f"Invalid naming template: {e}"
            skipped_channels.append({"channel_id": cid, "channel_name": name, "reason": reason})
            continue
        channel = Channel(
            channel_id=cid,
            channel_name=extras.channel_name,
            channel_url=extras.channel_url,
            platform=extras.platform,
            quality=checked.quality,
            quality_cutoff=checked.quality_cutoff,
            min_video_duration=checked.min_video_duration,
            download_from_date=_import_from_date(ch_data),
            retention_days=_import_retention_days(ch_data.get("retention_days")),
            naming_template=checked.naming_template,
            download_dir=ch_data.get("download_dir"),
            folder_name=await _import_folder_name(db, ch_data, pre_existing_folders),
            enabled=checked.enabled,
            include_shorts=extras.include_shorts,
            include_livestreams=extras.include_livestreams,
            min_quality=checked.min_quality,
            preferred_codec=checked.preferred_codec,
            title_filter=checked.title_filter,
            title_filter_is_regex=checked.title_filter_is_regex,
            title_filter_mode=checked.title_filter_mode,
            auto_download=checked.auto_download,
            prioritize_downloads=extras.prioritize_downloads,
            health_status="unknown",
        )
        db.add(channel)
        new_channels.append(channel)
        imported_channels += 1

    # Give imported channels scan times spread across the scan window, so they
    # don't all scan back to back
    if new_channels:
        from app.services.settings_service import read_scan_window
        from app.utils.scan_window import spread_into_window

        start_h, end_h = await read_scan_window(db)
        times = spread_into_window(len(new_channels), start_hour=start_h, end_hour=end_h)
        random.shuffle(times)
        for ch, t in zip(new_channels, times):
            ch.next_scan_at = t

    await db.commit()
    from app.services.settings_service import apply_runtime_settings
    await apply_runtime_settings(db)
    return {
        "imported_settings": imported_settings,
        "imported_channels": imported_channels,
        "skipped_channels": skipped_channels,
        "skipped_settings": skipped_settings,
    }


@router.post("/naming/preview", response_model=NamingPreviewResponse)
async def preview_naming_template(body: NamingPreviewRequest):
    template = body.template or DEFAULT_TEMPLATE
    result = preview_naming(
        template=template,
        channel_name=body.channel_name,
        title=body.title,
        upload_date=body.upload_date,
        video_id=body.video_id,
        season=body.season,
        episode=body.episode,
    )
    return NamingPreviewResponse(
        preview_path=result,
        full_path=f"/downloads/{result}.mp4",
    )


# --- Dynamic key routes MUST come AFTER all named routes to avoid shadowing ---


@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    internal = key.startswith(("_", DATE_LOOKUP_KEY_PREFIX)) or key in FLAG_KEYS
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = None if internal else result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return {"key": key, "value": json.loads(setting.value)}


@router.put("/{key}")
async def update_setting(key: str, value: str, db: AsyncSession = Depends(get_db)):
    if key not in SettingsUpdate.model_fields:
        raise HTTPException(status_code=400, detail=f"Unknown setting '{key}'")
    typed = _parse_settings_block({key: value})[key]
    await _check_update(db, {key: typed})

    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = json.dumps(typed)
    else:
        db.add(AppSetting(key=key, value=json.dumps(typed)))

    await db.commit()
    if key in ("log_level", "user_agent_rotation"):
        from app.services.settings_service import apply_runtime_settings
        await apply_runtime_settings(db)
    return {"key": key, "value": typed}
