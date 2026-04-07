import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import AppSetting, Channel, Video, DownloadQueue, DownloadLog
from app.services.diagnostics_service import DiagnosticsService
from app.services.metadata_service import write_episode_nfo, write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService
from app.utils.error_codes import ErrorCode, ERROR_CATALOG, classify_error
from app.utils.platform_utils import build_video_url
from app.utils.rate_limiter import wait_for_rate_limit, mark_download_complete

logger = logging.getLogger(__name__)


class DownloadService:
    """Manages video downloads with short-lived DB sessions.

    The key architectural constraint: the DB connection must NOT be held open
    during the actual yt-dlp download (which can take 15+ minutes). SQLite
    only allows one writer at a time; a long-held connection starves dashboard
    queries and API requests. Instead we use three brief sessions:

    1. Pre-download: mark video as downloading, gather metadata
    2. Download: yt-dlp runs with NO open DB session
    3. Post-download: record success or failure
    """

    def __init__(self):
        self.ytdlp = YtdlpService()
        self.notification = NotificationService()

    async def download_video(self, video_id: int, channel_id: int, queue_id: int) -> bool:
        """Download a single video. Returns True on success."""

        # ── Phase 1: mark as downloading and gather plain data ───────────
        async with async_session() as db:
            video = await db.get(Video, video_id)
            channel = await db.get(Channel, channel_id)
            queue_entry = await db.get(DownloadQueue, queue_id)

            if not video or not channel or not queue_entry:
                logger.error("Download task: missing video/channel/queue for video_id=%d", video_id)
                return False

            video_url = build_video_url(channel.platform, video.video_id)
            video.status = "downloading"
            if not queue_entry.started_at:
                queue_entry.started_at = datetime.now(timezone.utc)
            await db.commit()

            db.add(DownloadLog(video_id=video.id, event="started", message=f"Starting download: {video.title}"))
            await db.commit()

            await self.notification.broadcast("download_progress", {
                "video_id": video.video_id,
                "title": video.title,
                "status": "downloading",
                "percent": 0,
            })

            # Snapshot all ORM data we need into plain Python values so we
            # can close the session before the long-running download.
            vdata = _VideoData(
                pk=video.id,
                video_id=video.video_id,
                title=video.title,
                description=video.description,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                duration=video.duration,
                thumbnail_url=video.thumbnail_url,
            )
            cdata = _ChannelData(
                pk=channel.id,
                channel_id=channel.channel_id,
                channel_name=channel.channel_name,
                channel_url=channel.channel_url,
                platform=channel.platform,
                description=channel.description,
                thumbnail_url=channel.thumbnail_url,
                quality=channel.quality,
                naming_template=channel.naming_template,
                download_dir=channel.download_dir,
            )

            output_path = build_output_path(
                channel_name=cdata.channel_name,
                video_title=vdata.title,
                video_id=vdata.video_id,
                upload_date=vdata.upload_date,
                season=vdata.season,
                episode=vdata.episode,
                naming_template=cdata.naming_template,
                base_dir=cdata.download_dir,
            )
        # ← session released

        # ── Phase 2: actual download  - no DB session held ────────────────
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        _loop = asyncio.get_running_loop()
        import time
        _download_start = time.monotonic()

        async def _broadcast_progress(d: dict):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                speed = d.get("speed", 0) or 0
                eta = d.get("eta", 0) or 0
                elapsed = time.monotonic() - _download_start

                await self.notification.broadcast("download_progress", {
                    "video_id": vdata.video_id,
                    "title": vdata.title,
                    "status": "downloading",
                    "percent": round(percent, 1),
                    "speed": self._format_speed(speed),
                    "eta": self._format_eta(eta),
                    "downloaded_bytes": int(downloaded),
                    "total_bytes": int(total),
                    "speed_bytes": round(speed, 1),
                    "eta_seconds": int(eta),
                    "elapsed_seconds": int(elapsed),
                })

        def progress_hook(d: dict):
            try:
                asyncio.run_coroutine_threadsafe(_broadcast_progress(d), _loop)
            except Exception:
                pass

        try:
            await wait_for_rate_limit()

            logger.info(
                "Starting download: %s (video_id=%s, quality=%s, output=%s, cookies=%s, pot=%s)",
                vdata.title, vdata.video_id, cdata.quality, output_path,
                settings.has_cookies, settings.POT_SERVER_ENABLED,
            )

            try:
                info = await asyncio.wait_for(
                    asyncio.to_thread(
                        self.ytdlp.download_video,
                        video_url,
                        output_path,
                        quality=cdata.quality,
                        progress_hook=progress_hook,
                        platform=cdata.platform,
                    ),
                    timeout=900,
                )
            except asyncio.TimeoutError:
                raise Exception("Download timed out after 15 minutes  - YouTube may be throttling or blocking requests")

            mark_download_complete()

            # Verify output file
            mp4_path = output_path + ".mp4"
            if not os.path.exists(mp4_path):
                parent = os.path.dirname(output_path)
                base = os.path.basename(output_path)
                for f in os.listdir(parent):
                    if f.startswith(base) and f.endswith(".mp4"):
                        mp4_path = os.path.join(parent, f)
                        break

            file_size = os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0

            # Download integrity check
            if file_size == 0:
                raise Exception(f"Downloaded file is empty (0 bytes): {mp4_path}")

            # Write Plex metadata NFO files
            write_tvshow_nfo(
                channel_name=cdata.channel_name,
                channel_id=cdata.channel_id,
                channel_url=cdata.channel_url,
                description=cdata.description,
                thumbnail_url=cdata.thumbnail_url,
                base_dir=cdata.download_dir,
                platform=cdata.platform,
            )
            write_episode_nfo(
                channel_name=cdata.channel_name,
                video_title=vdata.title,
                video_id=vdata.video_id,
                description=vdata.description,
                upload_date=vdata.upload_date,
                season=vdata.season,
                episode=vdata.episode,
                duration=vdata.duration,
                thumbnail_url=vdata.thumbnail_url,
                video_file_path=mp4_path,
                platform=cdata.platform,
            )

        except Exception as e:
            mark_download_complete()
            # ── Phase 3 (error): record failure ──────────────────────────
            await self._record_failure(video_id, channel_id, queue_id, e, vdata)
            return False

        # ── Phase 3 (success): record completion ─────────────────────────
        async with async_session() as db:
            video = await db.get(Video, video_id)
            channel = await db.get(Channel, channel_id)
            queue_entry = await db.get(DownloadQueue, queue_id)

            if not video:
                return False

            video.status = "completed"
            video.file_path = mp4_path
            video.file_size = file_size
            video.downloaded_at = datetime.now(timezone.utc)
            video.quality_downloaded = cdata.quality
            video.error_code = None
            video.error_message = None
            video.error_details = None

            if channel:
                channel.downloaded_count = (
                    await db.scalar(
                        select(func.count(Video.id))
                        .where(Video.channel_id == channel.id)
                        .where(Video.status == "completed")
                    )
                ) or 0
                channel.health_status = "healthy"

            if queue_entry:
                await db.delete(queue_entry)

            db.add(DownloadLog(
                video_id=video.id,
                event="completed",
                message=f"Downloaded successfully: {self._format_bytes(file_size)}",
            ))

            # Track last successful auth time (for cookie health dashboard)
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == "last_successful_auth")
            )
            auth_ts = result.scalar_one_or_none()
            now_str = datetime.now(timezone.utc).isoformat()
            if auth_ts:
                auth_ts.value = now_str
            else:
                db.add(AppSetting(key="last_successful_auth", value=now_str))

            await db.commit()

        await self.notification.broadcast("download_complete", {
            "video_id": vdata.video_id,
            "title": vdata.title,
            "file_size": self._format_bytes(file_size),
        })

        logger.info("Downloaded: %s (%s)", vdata.title, self._format_bytes(file_size))
        return True

    async def _record_failure(
        self,
        video_id: int,
        channel_id: int,
        queue_id: int,
        error: Exception,
        vdata: "_VideoData",
    ):
        """Record a download error with diagnostics (opens its own session)."""
        error_str = str(error)

        async with async_session() as db:
            diagnostics = DiagnosticsService(db)
            diagnosis = diagnostics.classify_download_error(error_str)
            code = ErrorCode(diagnosis.code)

            logger.error("Download failed for %s: [%s] %s", vdata.title, code.value, error_str)

            video = await db.get(Video, video_id)
            channel = await db.get(Channel, channel_id)
            queue_entry = await db.get(DownloadQueue, queue_id)

            if not video or not channel:
                return

            video.error_code = code.value
            video.error_message = diagnosis.summary
            video.error_details = json.dumps({
                "explanation": diagnosis.explanation,
                "suggested_fix": diagnosis.suggested_fix,
                "raw_log": diagnosis.raw_log,
                "system_context": diagnosis.system_context,
            })

            db.add(DownloadLog(
                video_id=video.id,
                event="failed",
                error_code=code.value,
                message=diagnosis.summary,
                details=json.dumps({
                    "explanation": diagnosis.explanation,
                    "suggested_fix": diagnosis.suggested_fix,
                    "error": error_str[:2000],
                }),
            ))

            info = ERROR_CATALOG.get(code, ERROR_CATALOG[ErrorCode.UNKNOWN])

            if code in (ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.VIDEO_PRIVATE, ErrorCode.VIDEO_REMOVED, ErrorCode.GEO_BLOCKED):
                video.status = "skipped"
                if queue_entry:
                    await db.delete(queue_entry)

            elif code == ErrorCode.DISK_FULL:
                video.status = "failed"
                if queue_entry:
                    await db.delete(queue_entry)
                await self.notification.broadcast("critical_alert", {
                    "type": "disk_full",
                    "message": "Disk is full. All downloads paused.",
                })

            elif info.retry_strategy != "none" and video.retry_count < settings.MAX_RETRIES:
                video.retry_count += 1
                video.status = "queued"
                if queue_entry:
                    queue_entry.started_at = None
                    queue_entry.progress_percent = 0
                    queue_entry.speed_bps = None
                    queue_entry.eta_seconds = None
                    queue_entry.priority = max(queue_entry.priority - 1, -10)

            else:
                video.status = "failed"
                if queue_entry:
                    await db.delete(queue_entry)

            channel.last_error_code = code.value
            failed_count = await db.scalar(
                select(func.count(Video.id))
                .where(Video.channel_id == channel.id)
                .where(Video.status == "failed")
            )
            if failed_count and failed_count > 3:
                channel.health_status = "unhealthy"
            elif code in (ErrorCode.AUTH_EXPIRED, ErrorCode.PO_TOKEN_FAILURE):
                channel.health_status = "warning"

            # Flag cookies as expired and auto-pause queue
            if code == ErrorCode.AUTH_EXPIRED:
                from app.utils.cookie_utils import flag_cookies_expired
                await flag_cookies_expired(db)

            await db.commit()

        # Broadcast failure + cookie expiration events
        if code == ErrorCode.AUTH_EXPIRED:
            await self.notification.broadcast("cookies_expired", {
                "message": "YouTube cookies have expired. Queue paused. Re-export and upload fresh cookies.",
            })

        await self.notification.broadcast("download_failed", {
            "video_id": vdata.video_id,
            "title": vdata.title,
            "error_code": code.value,
            "summary": diagnosis.summary,
            "suggested_fix": diagnosis.suggested_fix,
        })

    @staticmethod
    def _format_speed(speed: float) -> str:
        if not speed:
            return "0 B/s"
        for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
            if speed < 1024:
                return f"{speed:.1f} {unit}"
            speed /= 1024
        return f"{speed:.1f} TB/s"

    @staticmethod
    def _format_bytes(size: int) -> str:
        from app.services.storage_service import format_bytes
        return format_bytes(size)

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if not seconds:
            return "??:??"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


# ── Plain data containers (no ORM / session dependency) ──────────────────

class _VideoData:
    __slots__ = (
        "pk", "video_id", "title", "description", "upload_date",
        "season", "episode", "duration", "thumbnail_url",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _ChannelData:
    __slots__ = (
        "pk", "channel_id", "channel_name", "channel_url", "platform",
        "description", "thumbnail_url", "quality", "naming_template",
        "download_dir",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
