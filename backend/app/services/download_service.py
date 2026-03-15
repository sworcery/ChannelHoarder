import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, Video, DownloadQueue, DownloadLog
from app.services.diagnostics_service import DiagnosticsService
from app.services.metadata_service import write_episode_nfo, write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService
from app.utils.error_codes import ErrorCode, ERROR_CATALOG, classify_error
from app.utils.rate_limiter import wait_for_rate_limit, mark_download_complete

logger = logging.getLogger(__name__)


class DownloadService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ytdlp = YtdlpService()
        self.diagnostics = DiagnosticsService(db)
        self.notification = NotificationService()

    async def download_video(self, video: Video, channel: Channel) -> bool:
        """Download a single video. Returns True on success."""
        video_url = f"https://www.youtube.com/watch?v={video.video_id}"

        # Update status
        video.status = "downloading"
        if video.queue_entry:
            video.queue_entry.started_at = datetime.now(timezone.utc)
        await self.db.commit()

        # Log start
        self.db.add(DownloadLog(video_id=video.id, event="started", message=f"Starting download: {video.title}"))
        await self.db.commit()

        # Broadcast start
        await self.notification.broadcast("download_progress", {
            "video_id": video.video_id,
            "title": video.title,
            "status": "downloading",
            "percent": 0,
        })

        # Build output path
        output_path = build_output_path(
            channel_name=channel.channel_name,
            video_title=video.title,
            video_id=video.video_id,
            upload_date=video.upload_date,
            season=video.season,
            episode=video.episode,
            naming_template=channel.naming_template,
        )

        # Ensure directory exists
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Progress callback
        async def _broadcast_progress(d: dict):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                speed = d.get("speed", 0) or 0
                eta = d.get("eta", 0) or 0

                if video.queue_entry:
                    video.queue_entry.progress_percent = percent
                    video.queue_entry.speed_bps = int(speed) if speed else None
                    video.queue_entry.eta_seconds = int(eta) if eta else None

                await self.notification.broadcast("download_progress", {
                    "video_id": video.video_id,
                    "title": video.title,
                    "status": "downloading",
                    "percent": round(percent, 1),
                    "speed": self._format_speed(speed),
                    "eta": self._format_eta(eta),
                })

        def progress_hook(d: dict):
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(_broadcast_progress(d), loop)
            except Exception:
                pass

        try:
            # Rate limit
            await wait_for_rate_limit()

            # Download in thread
            info = await asyncio.to_thread(
                self.ytdlp.download_video,
                video_url,
                output_path,
                quality=channel.quality,
                progress_hook=progress_hook,
            )

            mark_download_complete()

            # Verify output
            mp4_path = output_path + ".mp4"
            if not os.path.exists(mp4_path):
                # yt-dlp might use a different extension, find it
                parent = os.path.dirname(output_path)
                base = os.path.basename(output_path)
                for f in os.listdir(parent):
                    if f.startswith(base) and f.endswith(".mp4"):
                        mp4_path = os.path.join(parent, f)
                        break

            file_size = os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0

            # Write Plex metadata NFO files
            write_tvshow_nfo(
                channel_name=channel.channel_name,
                channel_id=channel.channel_id,
                channel_url=channel.channel_url,
                description=channel.description,
                thumbnail_url=channel.thumbnail_url,
            )
            write_episode_nfo(
                channel_name=channel.channel_name,
                video_title=video.title,
                video_id=video.video_id,
                description=video.description,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                duration=video.duration,
                thumbnail_url=video.thumbnail_url,
                video_file_path=mp4_path,
            )

            # Update video record
            video.status = "completed"
            video.file_path = mp4_path
            video.file_size = file_size
            video.downloaded_at = datetime.now(timezone.utc)
            video.quality_downloaded = channel.quality
            video.error_code = None
            video.error_message = None
            video.error_details = None

            # Update channel stats
            channel.downloaded_count = (
                await self.db.scalar(
                    select(func.count(Video.id))
                    .where(Video.channel_id == channel.id)
                    .where(Video.status == "completed")
                )
            ) or 0
            channel.health_status = "healthy"

            # Remove from queue
            if video.queue_entry:
                await self.db.delete(video.queue_entry)

            # Log success
            self.db.add(DownloadLog(
                video_id=video.id,
                event="completed",
                message=f"Downloaded successfully: {self._format_bytes(file_size)}",
            ))

            await self.db.commit()

            # Broadcast complete
            await self.notification.broadcast("download_complete", {
                "video_id": video.video_id,
                "title": video.title,
                "file_size": self._format_bytes(file_size),
            })

            logger.info("Downloaded: %s (%s)", video.title, self._format_bytes(file_size))
            return True

        except Exception as e:
            mark_download_complete()
            await self._handle_error(video, channel, e)
            return False

    async def _handle_error(self, video: Video, channel: Channel, error: Exception):
        """Handle a download error with full diagnostics."""
        error_str = str(error)
        diagnosis = self.diagnostics.classify_download_error(error_str)
        code = ErrorCode(diagnosis.code)

        logger.error("Download failed for %s: [%s] %s", video.title, code.value, error_str)

        # Store error info
        video.error_code = code.value
        video.error_message = diagnosis.summary
        video.error_details = json.dumps({
            "explanation": diagnosis.explanation,
            "suggested_fix": diagnosis.suggested_fix,
            "raw_log": diagnosis.raw_log,
            "system_context": diagnosis.system_context,
        })

        # Log the error
        self.db.add(DownloadLog(
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

        # Determine retry strategy
        info = ERROR_CATALOG.get(code, ERROR_CATALOG[ErrorCode.UNKNOWN])

        if code in (ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.VIDEO_PRIVATE, ErrorCode.VIDEO_REMOVED, ErrorCode.GEO_BLOCKED):
            video.status = "skipped"
            if video.queue_entry:
                await self.db.delete(video.queue_entry)

        elif code == ErrorCode.DISK_FULL:
            video.status = "failed"
            if video.queue_entry:
                await self.db.delete(video.queue_entry)
            # Broadcast critical alert
            await self.notification.broadcast("critical_alert", {
                "type": "disk_full",
                "message": "Disk is full. All downloads paused.",
            })

        elif info.retry_strategy != "none" and video.retry_count < settings.MAX_RETRIES:
            video.retry_count += 1
            video.status = "queued"
            if video.queue_entry:
                video.queue_entry.started_at = None
                video.queue_entry.progress_percent = 0
                video.queue_entry.speed_bps = None
                video.queue_entry.eta_seconds = None
                video.queue_entry.priority = max(video.queue_entry.priority - 1, -10)

        else:
            video.status = "failed"
            if video.queue_entry:
                await self.db.delete(video.queue_entry)

        # Update channel health
        channel.last_error_code = code.value
        failed_count = await self.db.scalar(
            select(func.count(Video.id))
            .where(Video.channel_id == channel.id)
            .where(Video.status == "failed")
        )
        if failed_count and failed_count > 3:
            channel.health_status = "unhealthy"
        elif code in (ErrorCode.AUTH_EXPIRED, ErrorCode.PO_TOKEN_FAILURE):
            channel.health_status = "warning"

        await self.db.commit()

        # Broadcast error
        await self.notification.broadcast("download_failed", {
            "video_id": video.video_id,
            "title": video.title,
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
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if not seconds:
            return "??:??"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"
