import logging
import shutil

from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models import AppSetting, SystemHealthLog
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)


async def check_system_health():
    """Scheduled task: check PO tokens, disk space, yt-dlp functionality."""
    logger.info("Running system health check")

    async with async_session() as db:
        # Check PO token server
        if settings.POT_SERVER_ENABLED:
            try:
                import httpx
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{settings.POT_SERVER_URL}/ping", timeout=5)
                    pot_healthy = resp.status_code == 200
            except Exception:
                pot_healthy = False

            db.add(SystemHealthLog(
                component="pot_server",
                status="healthy" if pot_healthy else "unhealthy",
                message="PO token server responding" if pot_healthy else "PO token server not responding",
            ))

            if not pot_healthy:
                await NotificationService.broadcast("health_alert", {
                    "component": "pot_server",
                    "status": "unhealthy",
                    "message": "PO token server is not responding. Downloads may fail.",
                })

        # Check disk space
        try:
            disk = shutil.disk_usage(settings.DOWNLOAD_DIR)
            free_gb = disk.free / (1024 ** 3)
            disk_status = "healthy" if free_gb > 5 else ("warning" if free_gb > 1 else "critical")

            db.add(SystemHealthLog(
                component="disk",
                status=disk_status,
                message=f"{free_gb:.1f} GB free",
            ))

            if disk_status == "critical":
                await NotificationService.broadcast("health_alert", {
                    "component": "disk",
                    "status": "critical",
                    "message": f"Only {free_gb:.1f} GB disk space remaining!",
                })
        except Exception as e:
            logger.error("Failed to check disk space: %s", e)

        # Check yt-dlp functionality (blocking call  - run in thread to avoid blocking event loop)
        import asyncio
        ytdlp = YtdlpService()
        success, message = await asyncio.to_thread(ytdlp.test_download_capability)

        db.add(SystemHealthLog(
            component="ytdlp",
            status="healthy" if success else "unhealthy",
            message=message,
        ))

        if success:
            # Auto-recover: if cookies were previously flagged expired but yt-dlp works now, clear the flag
            result = await db.execute(
                select(AppSetting).where(
                    AppSetting.key.in_(["cookies_expired", "queue_paused", "queue_pause_reason"])
                )
            )
            flags = {s.key: s for s in result.scalars().all()}
            expired_flag = flags.get("cookies_expired")
            if expired_flag and expired_flag.value == "true":
                expired_flag.value = "false"
                reason_flag = flags.get("queue_pause_reason")
                pause_flag = flags.get("queue_paused")
                if pause_flag and pause_flag.value == "true" and reason_flag and reason_flag.value == "cookies_expired":
                    pause_flag.value = "false"
                    reason_flag.value = ""
                logger.info("Auto-recovery: yt-dlp test passed, cookies_expired cleared, queue unpaused")
                await NotificationService.broadcast("cookies_refreshed", {
                    "message": "Cookies are working again. Queue resumed automatically.",
                })
        else:
            await NotificationService.broadcast("health_alert", {
                "component": "ytdlp",
                "status": "unhealthy",
                "message": f"yt-dlp test failed: {message}. Downloads may fail.",
            })

            # If the failure looks like an auth/bot issue, auto-pause queue
            if "Sign in" in message or "bot" in message.lower() or "cookies" in message.lower():
                from app.utils.cookie_utils import flag_cookies_expired
                await flag_cookies_expired(db)
                await NotificationService.broadcast("cookies_expired", {
                    "message": "Health check detected expired cookies. Queue paused. Please upload fresh cookies.",
                })

        await db.commit()
        logger.info("Health check complete")
