import asyncio
import logging
import subprocess  # noqa: F401 — used via asyncio.to_thread(subprocess.run)
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy.orm import joinedload

from app.config import settings
from app.utils.file_utils import escape_like
from app.deps import get_db
from app.models import Channel, DownloadLog, Video
from app.schemas import DiagnosticReport, DownloadLogResponse
from app.services.diagnostics_service import DiagnosticsService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(select(func.count()).select_from(Channel))
        return {"status": "healthy", "version": settings.APP_VERSION}
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "version": settings.APP_VERSION},
        )


@router.get("/ytdlp/version")
async def get_ytdlp_version():
    ytdlp = YtdlpService()
    return {"version": ytdlp.get_version()}


@router.post("/ytdlp/update")
async def update_ytdlp():
    import asyncio
    ytdlp = YtdlpService()
    success, message = await asyncio.to_thread(ytdlp.update)
    version = await asyncio.to_thread(ytdlp.get_version)
    return {"success": success, "message": message, "version": version}


@router.get("/diagnostics", response_model=DiagnosticReport)
async def get_diagnostics(db: AsyncSession = Depends(get_db)):
    service = DiagnosticsService(db)
    return await service.generate_report()


@router.get("/diagnostics/{video_id}")
async def get_video_diagnostics(video_id: int, db: AsyncSession = Depends(get_db)):
    service = DiagnosticsService(db)
    report = await service.generate_video_report(video_id)
    if "error" in report:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=report["error"])
    return report


@router.get("/logs")
async def get_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    error_code: Optional[str] = None,
    channel_id: Optional[int] = None,
    event: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(DownloadLog).options(
        joinedload(DownloadLog.video).joinedload(Video.channel)
    ).order_by(DownloadLog.created_at.desc())

    if error_code:
        query = query.where(DownloadLog.error_code == error_code)
    if event:
        query = query.where(DownloadLog.event == event)
    if search:
        query = query.where(DownloadLog.message.ilike(f"%{escape_like(search)}%"))
    if channel_id:
        video_ids = select(Video.id).where(Video.channel_id == channel_id)
        query = query.where(DownloadLog.video_id.in_(video_ids))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset(skip).limit(limit))
    logs = result.scalars().unique().all()

    items = [
        DownloadLogResponse(
            id=log.id,
            video_id=log.video_id,
            event=log.event,
            error_code=log.error_code,
            message=log.message,
            details=log.details,
            created_at=log.created_at,
            video_title=log.video.title if log.video else None,
            channel_name=log.video.channel.channel_name if log.video and log.video.channel else None,
        )
        for log in logs
    ]

    return {"items": items, "total": total or 0, "skip": skip, "limit": limit}


@router.get("/pot-server-log")
async def get_pot_server_log():
    """Read PO token server log and check if process is alive."""
    import subprocess
    from pathlib import Path

    log_path = Path(settings.CONFIG_DIR) / "pot-server.log"
    result = {"log_exists": log_path.exists()}

    if log_path.exists():
        content = log_path.read_text(errors="replace")
        lines = content.strip().split("\n")
        result["total_lines"] = len(lines)
        result["last_50_lines"] = lines[-50:]
    else:
        result["last_50_lines"] = ["(no log file found)"]

    # Check if server process is running
    try:
        ps = await asyncio.to_thread(subprocess.run, ["pgrep", "-f", "main.js.*4416"], capture_output=True, text=True)
        result["server_pid"] = ps.stdout.strip() if ps.returncode == 0 else None
    except Exception:
        result["server_pid"] = "unknown"

    # Quick connectivity test
    try:
        import httpx
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{settings.POT_SERVER_URL}/ping", timeout=5)
            result["ping"] = f"status {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        result["ping"] = f"FAILED: {e}"

    return result


@router.post("/test-download")
async def test_download():
    """Test download capability. Uses the bgutil plugin for per-video PO tokens."""
    import asyncio
    import io
    import contextlib
    import yt_dlp

    ytdlp = YtdlpService()
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"  # "Me at the zoo" (19s)

    diagnostics = {
        "cookies_present": settings.has_cookies,
        "cookies_size": settings.cookies_path.stat().st_size if settings.has_cookies else 0,
        "pot_server_enabled": settings.POT_SERVER_ENABLED,
        "ytdlp_version": ytdlp.get_version(),
    }

    # Validate cookie format
    if settings.has_cookies:
        try:
            content = settings.cookies_path.read_text(errors="replace")
            lines = content.strip().split("\n")
            has_header = any(l.strip().startswith("# Netscape HTTP Cookie File") or
                           l.strip().startswith("# HTTP Cookie File") for l in lines[:5])
            data_lines = [l for l in lines if l.strip() and not l.strip().startswith("#")]
            from app.utils.platform_utils import get_cookie_domains
            youtube_cookie_domains = get_cookie_domains("youtube")
            yt_entries = [l for l in data_lines if any(d in l for d in youtube_cookie_domains)]
            diagnostics["cookies_format"] = "Netscape" if has_header else "UNKNOWN (missing Netscape header)"
            diagnostics["cookies_youtube_entries"] = len(yt_entries)
        except Exception as e:
            diagnostics["cookies_validation"] = f"FAILED — {e}"

    # Check PO token server (ping only — don't call /get_pot to avoid starving the plugin)
    if settings.POT_SERVER_ENABLED:
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{settings.POT_SERVER_URL}/ping", timeout=5)
            diagnostics["pot_server_ping"] = f"OK (status {resp.status_code}): {resp.text[:200]}"
        except Exception as e:
            diagnostics["pot_server_ping"] = f"FAILED — {e}"

        # Check plugin
        try:
            import importlib
            importlib.import_module("yt_dlp_plugins.extractor.getpot_bgutil")
            diagnostics["pot_plugin_installed"] = "OK"
        except ImportError:
            diagnostics["pot_plugin_installed"] = "MISSING"

    diagnostics["strategy_results"] = {}

    # Helper to test a strategy
    async def _test_verbose(label: str, opts: dict) -> str:
        test_opts = dict(opts)
        test_opts.update({
            "skip_download": True, "ignoreerrors": False,
            "quiet": False, "no_warnings": False, "verbose": True,
        })
        capture = io.StringIO()
        try:
            with contextlib.redirect_stderr(capture):
                with yt_dlp.YoutubeDL(test_opts) as ydl:
                    info = await asyncio.to_thread(ydl.extract_info, test_url, False)
                    if info and info.get("formats"):
                        diagnostics["strategy_results"][label] = f"OK — {len(info['formats'])} formats"
                    else:
                        diagnostics["strategy_results"][label] = "FAILED — no info"
        except Exception as e:
            err = str(e)
            if "Sign in to confirm" in err:
                diagnostics["strategy_results"][label] = "FAILED — bot detected"
            else:
                diagnostics["strategy_results"][label] = f"FAILED — {err[:150]}"
        return capture.getvalue()

    # Each strategy gets its own _base_opts (with temp cookie copy); clean up after each
    all_opts = []

    # Strategy 1: Current config (web client + plugin) — full verbose capture
    opts1 = ytdlp._base_opts()
    all_opts.append(opts1)
    verbose_output = await _test_verbose("web_current_config", opts1)
    all_lines = [l.strip() for l in verbose_output.split("\n") if l.strip()]
    diagnostics["verbose_all_last_60"] = all_lines[-60:]

    # Strategy 2: Try mweb client (looser bot detection per yt-dlp wiki)
    mweb_opts = ytdlp._base_opts()
    all_opts.append(mweb_opts)
    mweb_opts["extractor_args"]["youtube"]["player_client"] = ["mweb"]
    await _test_verbose("mweb_client", mweb_opts)

    # Strategy 3: Try web_creator client
    creator_opts = ytdlp._base_opts()
    all_opts.append(creator_opts)
    creator_opts["extractor_args"]["youtube"]["player_client"] = ["web_creator"]
    await _test_verbose("web_creator_client", creator_opts)

    # Strategy 4: No cookies
    no_cookie_opts = ytdlp._base_opts()
    all_opts.append(no_cookie_opts)
    no_cookie_opts.pop("cookiefile", None)
    await _test_verbose("web_no_cookies", no_cookie_opts)

    # Clean up all temp cookie files
    for opts in all_opts:
        ytdlp._cleanup_cookie_tmp(opts)

    return diagnostics


@router.post("/scan-all", status_code=202)
async def trigger_scan_all(db: AsyncSession = Depends(get_db)):
    from app.services.channel_service import ChannelService

    result = await db.execute(select(Channel).where(Channel.enabled == True))
    channels = result.scalars().all()

    total_new = 0
    errors = []
    for channel in channels:
        try:
            service = ChannelService(db)
            new_count = await service.scan_channel(channel)
            total_new += new_count
        except Exception as e:
            errors.append({"channel": channel.channel_name, "error": str(e)})

    return {
        "message": f"Scanned {len(channels)} channels. Found {total_new} new videos.",
        "channels_scanned": len(channels),
        "new_videos": total_new,
        "errors": errors,
    }
