import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, DownloadLog, SystemHealthLog, Video
from app.schemas import DiagnosticReport, ErrorDiagnosis
from app.utils.error_codes import ErrorCode, ERROR_CATALOG, classify_error

logger = logging.getLogger(__name__)


class DiagnosticsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    def classify_download_error(self, error_str: str, stderr: str = "") -> ErrorDiagnosis:
        """Classify an error and return a full diagnosis."""
        combined = f"{error_str}\n{stderr}"
        code = classify_error(combined)
        info = ERROR_CATALOG.get(code, ERROR_CATALOG[ErrorCode.UNKNOWN])

        system_ctx = self._get_system_context_sync()

        return ErrorDiagnosis(
            code=info.code.value,
            summary=info.summary,
            explanation=info.explanation,
            suggested_fix=info.suggested_fix,
            retry_strategy=info.retry_strategy,
            severity=info.severity,
            raw_log=stderr[:5000] if stderr else error_str[:5000],
            system_context=system_ctx,
        )

    async def generate_report(self) -> DiagnosticReport:
        """Generate a full system diagnostic report."""
        from app.services.ytdlp_service import YtdlpService

        ytdlp = YtdlpService()

        # Gather stats
        channel_count = await self.db.scalar(select(func.count(Channel.id)))
        download_count = await self.db.scalar(
            select(func.count(Video.id)).where(Video.status == "completed")
        )
        failed_count = await self.db.scalar(
            select(func.count(Video.id)).where(Video.status == "failed")
        )

        # Recent errors
        result = await self.db.execute(
            select(DownloadLog)
            .where(DownloadLog.error_code.isnot(None))
            .order_by(DownloadLog.created_at.desc())
            .limit(20)
        )
        recent_errors = []
        for log in result.scalars().all():
            recent_errors.append({
                "id": log.id,
                "event": log.event,
                "error_code": log.error_code,
                "message": log.message,
                "created_at": log.created_at.isoformat() if log.created_at else None,
            })

        # Disk info
        disk = shutil.disk_usage(settings.DOWNLOAD_DIR)

        # PO token status
        pot_status = "unknown"
        pot_healthy = await self._check_pot_health()
        pot_status = "healthy" if pot_healthy else "unhealthy"

        return DiagnosticReport(
            generated_at=datetime.utcnow(),
            app_version=settings.APP_VERSION,
            ytdlp_version=ytdlp.get_version(),
            pot_status=pot_status,
            cookies_status="present" if settings.has_cookies else "not configured",
            api_key_configured=settings.has_youtube_api_key,
            disk_free_bytes=disk.free,
            disk_free_formatted=self._format_bytes(disk.free),
            total_channels=channel_count or 0,
            total_downloads=download_count or 0,
            total_failed=failed_count or 0,
            recent_errors=recent_errors,
            system_info={
                "config_dir": settings.CONFIG_DIR,
                "download_dir": settings.DOWNLOAD_DIR,
                "max_concurrent": settings.MAX_CONCURRENT_DOWNLOADS,
                "max_retries": settings.MAX_RETRIES,
                "delay_range": f"{settings.DOWNLOAD_DELAY_MIN}-{settings.DOWNLOAD_DELAY_MAX}s",
                "jitter": settings.JITTER_ENABLED,
                "ua_rotation": settings.USER_AGENT_ROTATION,
            },
        )

    async def generate_video_report(self, video_id: int) -> dict:
        """Generate a diagnostic report for a specific video."""
        result = await self.db.execute(select(Video).where(Video.id == video_id))
        video = result.scalar_one_or_none()
        if not video:
            return {"error": "Video not found"}

        # Get channel
        result = await self.db.execute(select(Channel).where(Channel.id == video.channel_id))
        channel = result.scalar_one_or_none()

        # Get all logs for this video
        result = await self.db.execute(
            select(DownloadLog)
            .where(DownloadLog.video_id == video_id)
            .order_by(DownloadLog.created_at.desc())
        )
        logs = result.scalars().all()

        # Build diagnosis if there's an error
        diagnosis = None
        if video.error_code:
            info = ERROR_CATALOG.get(
                ErrorCode(video.error_code) if video.error_code in ErrorCode.__members__ else ErrorCode.UNKNOWN,
                ERROR_CATALOG[ErrorCode.UNKNOWN],
            )
            diagnosis = {
                "code": info.code.value,
                "summary": info.summary,
                "explanation": info.explanation,
                "suggested_fix": info.suggested_fix,
            }

        return {
            "video": {
                "id": video.id,
                "video_id": video.video_id,
                "title": video.title,
                "status": video.status,
                "error_code": video.error_code,
                "error_message": video.error_message,
                "retry_count": video.retry_count,
            },
            "channel": {
                "name": channel.channel_name if channel else "Unknown",
                "health_status": channel.health_status if channel else "unknown",
            },
            "diagnosis": diagnosis,
            "logs": [
                {
                    "event": log.event,
                    "error_code": log.error_code,
                    "message": log.message,
                    "details": log.details,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                for log in logs
            ],
            "system_context": self._get_system_context_sync(),
            "copy_text": self._build_copy_text(video, channel, diagnosis, logs),
        }

    def _build_copy_text(self, video, channel, diagnosis, logs) -> str:
        """Build a formatted text block for copy-to-clipboard."""
        from app.services.ytdlp_service import YtdlpService
        ytdlp = YtdlpService()

        lines = [
            "=== ChannelHoarder - Diagnostic Report ===",
            f"Generated: {datetime.utcnow().isoformat()}",
            f"App Version: {settings.APP_VERSION}",
            f"yt-dlp Version: {ytdlp.get_version()}",
            "",
            "--- Video Info ---",
            f"Title: {video.title}",
            f"Video ID: {video.video_id}",
            f"Channel: {channel.channel_name if channel else 'Unknown'}",
            f"Status: {video.status}",
            f"Retry Count: {video.retry_count}",
            "",
        ]

        if diagnosis:
            lines.extend([
                "--- Diagnosis ---",
                f"Error Code: {diagnosis['code']}",
                f"Summary: {diagnosis['summary']}",
                f"Explanation: {diagnosis['explanation']}",
                f"Suggested Fix: {diagnosis['suggested_fix']}",
                "",
            ])

        if video.error_message:
            lines.extend([
                "--- Error Message ---",
                video.error_message[:2000],
                "",
            ])

        if logs:
            lines.append("--- Recent Logs ---")
            for log in logs[:10]:
                ts = log.created_at.isoformat() if log.created_at else "?"
                lines.append(f"[{ts}] {log.event}: {log.message or ''}")
            lines.append("")

        lines.extend([
            "--- System ---",
            f"PO Tokens: {'configured' if settings.POT_SERVER_ENABLED else 'disabled'}",
            f"Cookies: {'present' if settings.has_cookies else 'not configured'}",
            f"API Key: {'configured' if settings.has_youtube_api_key else 'not configured'}",
            f"Delay: {settings.DOWNLOAD_DELAY_MIN}-{settings.DOWNLOAD_DELAY_MAX}s",
            "=== End Report ===",
        ])

        return "\n".join(lines)

    async def _check_pot_health(self) -> bool:
        """Check if PO token server is responding."""
        if not settings.POT_SERVER_ENABLED:
            return False
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{settings.POT_SERVER_URL}/ping", timeout=5)
                return resp.status_code == 200
        except Exception:
            return False

    def _get_system_context_sync(self) -> dict:
        """Get system context synchronously (for use in error classification)."""
        from app.services.ytdlp_service import YtdlpService
        ytdlp = YtdlpService()

        try:
            disk = shutil.disk_usage(settings.DOWNLOAD_DIR)
            disk_free = self._format_bytes(disk.free)
        except Exception:
            disk_free = "unknown"

        return {
            "ytdlp_version": ytdlp.get_version(),
            "pot_enabled": settings.POT_SERVER_ENABLED,
            "cookies_present": settings.has_cookies,
            "api_key_configured": settings.has_youtube_api_key,
            "disk_free": disk_free,
        }

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Format bytes to human-readable string."""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.2f} {unit}"
            size /= 1024
        return f"{size:.2f} PB"
