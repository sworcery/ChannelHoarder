import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.deps import get_db
from app.models import AppSetting
from app.services.youtube_api_service import YouTubeAPIService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/cookies/upload")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload a cookies.txt file for YouTube authentication."""
    if not file.filename or not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="File must be a .txt file")

    cookie_path = settings.cookies_path
    cookie_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    cookie_path.write_bytes(content)

    # Clear the cookies_expired flag since we have fresh cookies
    async with async_session() as db:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "cookies_expired")
        )
        flag = result.scalar_one_or_none()
        if flag:
            flag.value = "false"
        # Also unpause the queue if it was auto-paused
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "queue_paused")
        )
        pause_flag = result.scalar_one_or_none()
        if pause_flag and pause_flag.value == "true":
            pause_flag.value = "false"
            logger.info("Queue auto-unpaused after fresh cookie upload")
        await db.commit()

    logger.info("Cookies uploaded: %d bytes (expired flag cleared, queue unpaused)", len(content))
    return {"message": "Cookies uploaded successfully. Queue resumed.", "size": len(content)}


@router.post("/cookies/push")
async def push_cookies(body: dict):
    """Accept cookies as JSON text (for browser extensions like Tampermonkey)."""
    cookies_txt = body.get("cookies_txt", "")
    if not cookies_txt or not cookies_txt.strip():
        raise HTTPException(status_code=400, detail="cookies_txt field is required and must not be empty")

    content = cookies_txt.encode("utf-8")
    cookie_path = settings.cookies_path
    cookie_path.parent.mkdir(parents=True, exist_ok=True)
    cookie_path.write_bytes(content)

    # Clear expired flag and unpause queue (same logic as file upload)
    async with async_session() as db:
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "cookies_expired")
        )
        flag = result.scalar_one_or_none()
        if flag:
            flag.value = "false"
        result = await db.execute(
            select(AppSetting).where(AppSetting.key == "queue_paused")
        )
        pause_flag = result.scalar_one_or_none()
        if pause_flag and pause_flag.value == "true":
            pause_flag.value = "false"
            logger.info("Queue auto-unpaused after cookie push")
        await db.commit()

    logger.info("Cookies pushed via API: %d bytes", len(content))
    return {"message": "Cookies received successfully. Queue resumed.", "size": len(content)}


@router.get("/cookies/status")
async def get_cookie_status():
    """Get cookie file status."""
    cookie_path = settings.cookies_path
    if not cookie_path.exists():
        return {"status": "not_configured", "message": "No cookies file uploaded"}

    stat = cookie_path.stat()
    age_hours = (datetime.now().timestamp() - stat.st_mtime) / 3600

    status = "healthy"
    if age_hours < 1:
        message = f"Cookies present (uploaded {int(age_hours * 60)} minutes ago)"
    elif age_hours < 24:
        message = f"Cookies present ({age_hours:.0f} hours old)"
    else:
        age_days = age_hours / 24
        message = f"Cookies present ({age_days:.0f} days old)"
        if age_days > 7:
            status = "warning"
            message = f"Cookies are {age_days:.0f} days old — may need re-uploading"

    return {
        "status": status,
        "message": message,
        "age_hours": round(age_hours, 1),
        "size": stat.st_size,
    }


@router.post("/cookies/validate")
async def validate_cookies():
    """Force a cookie health check."""
    from app.services.ytdlp_service import YtdlpService

    if not settings.has_cookies:
        return {"status": "not_configured", "message": "No cookies file to validate"}

    ytdlp = YtdlpService()
    success, message = ytdlp.test_download_capability()

    return {
        "status": "healthy" if success else "expired",
        "message": message,
    }


@router.delete("/cookies", status_code=204)
async def delete_cookies():
    """Remove stored cookies."""
    if settings.cookies_path.exists():
        settings.cookies_path.unlink()
        logger.info("Cookies deleted")


@router.put("/api-key")
async def set_api_key(api_key: str):
    """Set or update the YouTube Data API key."""
    if not api_key or not api_key.strip():
        raise HTTPException(status_code=400, detail="API key cannot be empty")

    api_key = api_key.strip()

    # Persist to config file first so it survives restarts
    config_dir = Path(settings.CONFIG_DIR)
    config_dir.mkdir(parents=True, exist_ok=True)
    api_key_file = config_dir / "youtube_api_key"
    api_key_file.write_text(api_key)
    settings.YOUTUBE_API_KEY = api_key
    logger.info("YouTube API key saved to %s", api_key_file)

    # Try to validate (non-blocking — key is saved regardless)
    yt_api = YouTubeAPIService()
    valid, message = await yt_api.validate_api_key()

    if valid:
        return {"message": "API key saved and validated successfully", "valid": True}
    else:
        logger.warning("API key saved but validation failed: %s", message)
        return {"message": f"API key saved, but validation failed: {message}. The key will still be used.", "valid": False}


@router.put("/player-client")
async def set_player_client(strategy: str):
    """Set yt-dlp player client strategy."""
    valid_clients = {"default", "web", "web_creator", "mweb", "android", "android_vr",
                     "ios", "tv", "mediaconnect"}
    parts = [p.strip() for p in strategy.split(",")]
    for part in parts:
        if part not in valid_clients:
            raise HTTPException(status_code=400, detail=f"Unknown player client: {part}. Valid: {', '.join(sorted(valid_clients))}")

    settings.YTDLP_PLAYER_CLIENT = strategy

    # Persist to config
    config_dir = Path(settings.CONFIG_DIR)
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "player_client").write_text(strategy)
    logger.info("Player client strategy set to: %s", strategy)

    return {"message": f"Player client set to: {strategy}", "strategy": strategy}


@router.get("/status")
async def get_auth_status():
    """Get overall authentication status."""
    try:
        # PO token status
        pot_status = "enabled" if settings.POT_SERVER_ENABLED else "disabled"
        pot_message = "PO token server is enabled" if settings.POT_SERVER_ENABLED else "PO token server is disabled"

        # Cookie status
        cookies_status = "not_configured"
        cookies_message = "Not configured"
        cookies_age_hours = None
        try:
            if settings.has_cookies:
                stat = settings.cookies_path.stat()
                cookies_age_hours = round((datetime.now().timestamp() - stat.st_mtime) / 3600, 1)
                cookies_status = "present"
                if cookies_age_hours < 1:
                    cookies_message = f"Present (uploaded {int(cookies_age_hours * 60)} minutes ago)"
                elif cookies_age_hours < 24:
                    cookies_message = f"Present ({cookies_age_hours:.0f} hours old)"
                else:
                    age_days = cookies_age_hours / 24
                    cookies_message = f"Present ({age_days:.0f} days old)"
                    if age_days > 7:
                        cookies_status = "warning"
                        cookies_message = f"May be expired ({age_days:.0f} days old)"
        except Exception as e:
            logger.error("Error checking cookies: %s", e)
            cookies_status = "error"
            cookies_message = f"Error checking cookies: {e}"

        # Check cookies_expired and last_successful_auth in a single DB session
        api_key_configured = bool(settings.YOUTUBE_API_KEY)
        last_auth = None

        async with async_session() as db:
            result = await db.execute(
                select(AppSetting).where(
                    AppSetting.key.in_(["cookies_expired", "last_successful_auth"])
                )
            )
            db_settings = {s.key: s.value for s in result.scalars().all()}

            if db_settings.get("cookies_expired") == "true":
                cookies_status = "expired"
                cookies_message = "Cookies expired — please upload fresh cookies.txt"

            last_auth = db_settings.get("last_successful_auth")

        return {
            "pot_status": pot_status,
            "pot_message": pot_message,
            "cookies_status": cookies_status,
            "cookies_message": cookies_message,
            "cookies_age_hours": cookies_age_hours,
            "api_key_configured": api_key_configured,
            "api_key_valid": None,
            "last_successful_auth": last_auth,
        }
    except Exception as e:
        logger.error("Auth status endpoint failed: %s", e, exc_info=True)
        return {
            "pot_status": "unknown",
            "pot_message": f"Error: {e}",
            "cookies_status": "unknown",
            "cookies_message": f"Error: {e}",
            "cookies_age_hours": None,
            "api_key_configured": False,
            "api_key_valid": None,
        }
