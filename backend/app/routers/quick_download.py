import asyncio
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import unquote

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import FileResponse

from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService
from app.utils.file_utils import sanitize_filename, validate_url_scheme
from app.utils.platform_utils import detect_platform

logger = logging.getLogger(__name__)
router = APIRouter()

QUICK_DOWNLOAD_DIR = "/tmp/quick-downloads"

_VALID_QUALITIES = {"best", "2160p", "1080p", "720p", "480p"}
_VIDEO_EXTENSIONS = {".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv"}


# ── Request / response models ────────────────────────────────────────────────

class QuickDownloadRequest(BaseModel):
    url: str = Field(..., description="Video URL to download")
    quality: str = Field(default="best", description="Desired quality (best/2160p/1080p/720p/480p)")


class QuickDownloadStarted(BaseModel):
    download_id: str
    title: str
    thumbnail: str | None
    duration: int | None


class QuickDownloadFile(BaseModel):
    filename: str
    size_bytes: int
    created_at: str
    expires_at: str


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    os.makedirs(QUICK_DOWNLOAD_DIR, exist_ok=True)


def _safe_filename(filename: str) -> str:
    """Reject filenames containing path traversal characters."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return filename


def _file_path(filename: str) -> str:
    return os.path.join(QUICK_DOWNLOAD_DIR, filename)


# ── Background download task ─────────────────────────────────────────────────

async def _run_quick_download(
    download_id: str,
    url: str,
    title: str,
    quality: str,
    platform: str,
    output_path: str,
) -> None:
    """Download a video in a thread and broadcast progress over WebSocket."""
    _loop = asyncio.get_running_loop()

    async def _broadcast_progress(d: dict) -> None:
        if d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
            downloaded = d.get("downloaded_bytes", 0)
            percent = (downloaded / total * 100) if total > 0 else 0
            speed = d.get("speed", 0) or 0
            eta = d.get("eta", 0) or 0
            await NotificationService.broadcast("quick_download_progress", {
                "download_id": download_id,
                "title": title,
                "percent": round(percent, 1),
                "speed_bytes": round(speed, 1),
                "downloaded_bytes": int(downloaded),
                "total_bytes": int(total),
                "eta": int(eta),
            })

    def _progress_hook(d: dict) -> None:
        try:
            asyncio.run_coroutine_threadsafe(_broadcast_progress(d), _loop)
        except Exception:
            pass

    try:
        await asyncio.to_thread(
            YtdlpService().download_video,
            url,
            output_path,
            quality=quality,
            progress_hook=_progress_hook,
            platform=platform,
        )

        # yt-dlp writes <output_path>.mp4 after muxing/conversion
        mp4_path = output_path + ".mp4"
        if not os.path.exists(mp4_path):
            # Scan for any .mp4 that starts with the base name
            parent = os.path.dirname(output_path)
            base = os.path.basename(output_path)
            for name in os.listdir(parent):
                if name.startswith(base) and name.endswith(".mp4"):
                    mp4_path = os.path.join(parent, name)
                    break

        size_bytes = os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0
        if size_bytes == 0:
            raise RuntimeError(f"Output file missing or empty: {mp4_path}")

        # Clean up sidecar files (.info.json, .jpg, .webp, .part, etc.)
        parent = os.path.dirname(mp4_path)
        base_stem = os.path.splitext(os.path.basename(mp4_path))[0]
        for name in os.listdir(parent):
            if name == os.path.basename(mp4_path):
                continue
            if name.startswith(base_stem) and not name.endswith((".mp4", ".mkv", ".webm")):
                try:
                    os.remove(os.path.join(parent, name))
                except OSError:
                    pass

        filename = os.path.basename(mp4_path)
        await NotificationService.broadcast("quick_download_complete", {
            "download_id": download_id,
            "title": title,
            "filename": filename,
            "size_bytes": size_bytes,
        })
        logger.info("Quick download complete: %s (%d bytes)", filename, size_bytes)

    except Exception as exc:
        logger.error("Quick download failed for %s: %s", url, exc, exc_info=True)
        await NotificationService.broadcast("quick_download_failed", {
            "download_id": download_id,
            "title": title,
            "error": str(exc),
        })


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.post("/quick-download", response_model=QuickDownloadStarted)
async def start_quick_download(body: QuickDownloadRequest):
    """Start a quick (no-database) download and return immediately."""
    # Validate URL scheme
    try:
        validate_url_scheme(body.url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Normalise quality
    quality = body.quality if body.quality in _VALID_QUALITIES else "best"

    _ensure_dir()

    # Fetch video metadata (runs in thread to avoid blocking the event loop)
    info = await asyncio.to_thread(YtdlpService().get_video_info_by_url, body.url)
    if not info:
        raise HTTPException(status_code=422, detail="Could not retrieve video info from the provided URL")

    title = info.get("title") or "Untitled"
    video_id = info.get("id") or str(uuid.uuid4())
    thumbnail = info.get("thumbnail")
    duration = info.get("duration")

    safe_title = sanitize_filename(title)
    filename_stem = f"{safe_title} [{video_id}]"
    output_path = os.path.join(QUICK_DOWNLOAD_DIR, filename_stem)

    platform = detect_platform(body.url)
    download_id = str(uuid.uuid4())

    asyncio.create_task(
        _run_quick_download(
            download_id=download_id,
            url=body.url,
            title=title,
            quality=quality,
            platform=platform,
            output_path=output_path,
        )
    )

    return QuickDownloadStarted(
        download_id=download_id,
        title=title,
        thumbnail=thumbnail,
        duration=int(duration) if duration else None,
    )


@router.get("/quick-download/files", response_model=list[QuickDownloadFile])
async def list_quick_download_files():
    """List all files in the quick-download directory, newest first."""
    _ensure_dir()

    files: list[QuickDownloadFile] = []
    try:
        entries = os.scandir(QUICK_DOWNLOAD_DIR)
    except OSError as exc:
        logger.error("Cannot scan quick-download directory: %s", exc)
        raise HTTPException(status_code=500, detail="Could not read quick-download directory")

    with entries:
        for entry in entries:
            if not entry.is_file():
                continue
            if os.path.splitext(entry.name)[1].lower() not in _VIDEO_EXTENSIONS:
                continue
            stat = entry.stat()
            created_dt = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
            expires_dt = created_dt + timedelta(days=7)
            files.append(QuickDownloadFile(
                filename=entry.name,
                size_bytes=stat.st_size,
                created_at=created_dt.isoformat(),
                expires_at=expires_dt.isoformat(),
            ))

    files.sort(key=lambda f: f.created_at, reverse=True)
    return files


@router.get("/quick-download/files/{filename}")
async def download_quick_file(filename: str):
    """Serve a quick-download file to the browser as an attachment."""
    filename = _safe_filename(unquote(filename))
    path = _file_path(filename)

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        path=path,
        media_type="video/mp4",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.delete("/quick-download/files/{filename}")
async def delete_quick_file(filename: str):
    """Delete a quick-download file."""
    filename = _safe_filename(unquote(filename))
    path = _file_path(filename)

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    try:
        os.remove(path)
    except OSError as exc:
        logger.error("Failed to delete quick-download file %s: %s", path, exc)
        raise HTTPException(status_code=500, detail="Could not delete file")

    logger.info("Deleted quick-download file: %s", filename)
    return {"message": f"Deleted {filename}"}
