import logging
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.schemas import AuthStatus
from app.services.youtube_api_service import YouTubeAPIService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/cookies/upload")
async def upload_cookies(file: UploadFile = File(...)):
    """Upload a cookies.txt file (optional, for edge cases)."""
    if not file.filename or not file.filename.endswith(".txt"):
        raise HTTPException(status_code=400, detail="File must be a .txt file")

    cookie_path = settings.cookies_path
    cookie_path.parent.mkdir(parents=True, exist_ok=True)

    content = await file.read()
    cookie_path.write_bytes(content)

    logger.info("Cookies uploaded: %d bytes", len(content))
    return {"message": "Cookies uploaded successfully", "size": len(content)}


@router.get("/cookies/status")
async def get_cookie_status():
    """Get cookie file status."""
    cookie_path = settings.cookies_path
    if not cookie_path.exists():
        return {"status": "not_configured", "message": "No cookies file uploaded (not required for normal operation)"}

    stat = cookie_path.stat()
    age_days = (datetime.now().timestamp() - stat.st_mtime) / 86400

    status = "healthy"
    message = f"Cookies present ({age_days:.0f} days old)"
    if age_days > 14:
        status = "warning"
        message = f"Cookies are {age_days:.0f} days old and may be expired"

    return {
        "status": status,
        "message": message,
        "age_days": round(age_days, 1),
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
    # Validate the key
    settings.YOUTUBE_API_KEY = api_key
    yt_api = YouTubeAPIService()
    valid = await yt_api.validate_api_key()

    if not valid:
        settings.YOUTUBE_API_KEY = ""
        raise HTTPException(status_code=400, detail="Invalid API key")

    return {"message": "API key configured successfully", "valid": True}


@router.get("/status", response_model=AuthStatus)
async def get_auth_status():
    """Get overall authentication status."""
    # PO token status
    pot_status = "enabled" if settings.POT_SERVER_ENABLED else "disabled"
    pot_message = "PO token server is enabled" if settings.POT_SERVER_ENABLED else "PO token server is disabled"

    # Cookie status
    cookies_status = "not_configured"
    cookies_message = "Not configured (not required)"
    cookies_age = None
    if settings.has_cookies:
        stat = settings.cookies_path.stat()
        cookies_age = round((datetime.now().timestamp() - stat.st_mtime) / 86400, 1)
        cookies_status = "present"
        cookies_message = f"Present ({cookies_age} days old)"
        if cookies_age > 14:
            cookies_status = "warning"
            cookies_message = f"May be expired ({cookies_age} days old)"

    # API key
    api_key_configured = settings.has_youtube_api_key

    return AuthStatus(
        pot_status=pot_status,
        pot_message=pot_message,
        cookies_status=cookies_status,
        cookies_message=cookies_message,
        cookies_age_days=int(cookies_age) if cookies_age else None,
        api_key_configured=api_key_configured,
        api_key_valid=None,  # Not validated until explicitly requested
    )
