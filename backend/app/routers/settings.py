import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import AppSetting, Channel
from app.schemas import SettingsUpdate, NamingPreviewRequest, NamingPreviewResponse
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
    result = await db.execute(select(AppSetting))
    settings_list = result.scalars().all()
    return {s.key: json.loads(s.value) for s in settings_list}


@router.put("/")
async def update_settings(body: SettingsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(value)
        else:
            db.add(AppSetting(key=key, value=json.dumps(value)))

    await db.commit()

    # Apply runtime-reactive settings immediately
    if "global_schedule_cron" in update_data:
        try:
            scheduler = request.app.state.scheduler
            scheduler.reschedule_scan(update_data["global_schedule_cron"])
        except Exception as e:
            logger.warning("Could not reschedule scan: %s", e)

    if "log_level" in update_data:
        level = update_data["log_level"].upper()
        logging.getLogger().setLevel(getattr(logging, level, logging.INFO))
        logger.info("Log level changed to %s", level)

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


@router.get("/export")
async def export_config(db: AsyncSession = Depends(get_db)):
    """Export all settings and channels as JSON for backup."""
    # Settings
    result = await db.execute(select(AppSetting))
    settings_list = result.scalars().all()
    SENSITIVE_KEYS = {
        "telegram_bot_token", "pushover_app_token", "pushover_user_key",
        "youtube_api_key", "telegram_chat_id",
    }
    settings_dict = {}
    for s in settings_list:
        if s.key in SENSITIVE_KEYS:
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
            "quality": ch.quality,
            "naming_template": ch.naming_template,
            "download_dir": ch.download_dir,
            "check_schedule": ch.check_schedule,
            "enabled": ch.enabled,
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

    imported_settings = 0
    imported_channels = 0
    skipped_channels = 0

    # Merge settings
    for key, value in data.get("settings", {}).items():
        # Skip sensitive keys that shouldn't be imported
        if key in ("last_successful_auth",):
            continue
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(value)
        else:
            db.add(AppSetting(key=key, value=json.dumps(value)))
        imported_settings += 1

    # Add channels that don't exist yet
    for ch_data in data.get("channels", []):
        cid = ch_data.get("channel_id")
        if not cid:
            continue
        result = await db.execute(select(Channel).where(Channel.channel_id == cid))
        if result.scalar_one_or_none():
            skipped_channels += 1
            continue
        channel = Channel(
            channel_id=cid,
            channel_name=ch_data.get("channel_name", "Imported"),
            channel_url=ch_data.get("channel_url", ""),
            quality=ch_data.get("quality", "best"),
            naming_template=ch_data.get("naming_template"),
            download_dir=ch_data.get("download_dir"),
            check_schedule=ch_data.get("check_schedule"),
            enabled=ch_data.get("enabled", True),
            health_status="unknown",
        )
        db.add(channel)
        imported_channels += 1

    await db.commit()
    return {
        "imported_settings": imported_settings,
        "imported_channels": imported_channels,
        "skipped_channels": skipped_channels,
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
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return {"key": key, "value": json.loads(setting.value)}


@router.put("/{key}")
async def update_setting(key: str, value: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = json.dumps(value)
    else:
        db.add(AppSetting(key=key, value=json.dumps(value)))

    await db.commit()
    return {"key": key, "value": value}
