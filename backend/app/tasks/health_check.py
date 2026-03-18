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

        # Check yt-dlp functionality
        ytdlp = YtdlpService()
        success, message = ytdlp.test_download_capability()

        db.add(SystemHealthLog(
            component="ytdlp",
            status="healthy" if success else "unhealthy",
            message=message,
        ))

        if not success:
            await NotificationService.broadcast("health_alert", {
                "component": "ytdlp",
                "status": "unhealthy",
                "message": f"yt-dlp test failed: {message}. Downloads may fail.",
            })

            # If the failure looks like an auth/bot issue, auto-pause queue
            if "Sign in" in message or "bot" in message.lower() or "cookies" in message.lower():
                # Pause the queue
                result = await db.execute(
                    select(AppSetting).where(AppSetting.key == "queue_paused")
                )
                flag = result.scalar_one_or_none()
                if flag:
                    flag.value = "true"
                else:
                    db.add(AppSetting(key="queue_paused", value="true"))

                # Flag cookies as expired
                result = await db.execute(
                    select(AppSetting).where(AppSetting.key == "cookies_expired")
                )
                ce_flag = result.scalar_one_or_none()
                if ce_flag:
                    ce_flag.value = "true"
                else:
                    db.add(AppSetting(key="cookies_expired", value="true"))
                # Delete the local cookies file so the watcher can re-import from /cookies
                settings.cookies_path.unlink(missing_ok=True)

                logger.warning("Health check: auth failure — queue auto-paused, cookies deleted, flagged expired")
                await NotificationService.broadcast("cookies_expired", {
                    "message": "Health check detected expired cookies. Queue paused. Please upload fresh cookies.",
                })

        await db.commit()
        logger.info("Health check complete")
