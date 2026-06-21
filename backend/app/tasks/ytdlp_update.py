import asyncio
import logging
import os
import signal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting, DownloadQueue, SystemHealthLog
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)


async def _set_flag(db: AsyncSession, key: str, value: str) -> None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    flag = result.scalar_one_or_none()
    if flag:
        flag.value = value
    else:
        db.add(AppSetting(key=key, value=value))


async def _get_flag(db: AsyncSession, key: str) -> str | None:
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    flag = result.scalar_one_or_none()
    return flag.value if flag else None


async def check_and_apply_ytdlp_update(db: AsyncSession) -> None:
    """Daily self-heal step: compare the running yt-dlp against PyPI's latest,
    pip-upgrade if behind, and apply the new version via a graceful restart.

    The running process keeps the old yt-dlp module loaded after a pip upgrade,
    so the only reliable way to apply it is a restart. When AUTO_RESTART is on,
    this exits the process while the queue is idle; the container's restart policy
    brings it back on the new version. A loop guard prevents repeated restarts if
    an update somehow fails to take effect.
    """
    if not settings.YTDLP_AUTO_UPDATE:
        return

    ytdlp = YtdlpService()
    current = ytdlp.get_version()
    latest = await asyncio.to_thread(ytdlp.get_latest_pypi_version)

    if not latest:
        logger.info("yt-dlp version check skipped (PyPI unreachable); running %s", current)
        return

    if not ytdlp.is_outdated(current, latest):
        # Up to date - clear any stale pending/loop-guard flags from a prior update.
        await _set_flag(db, "ytdlp_update_pending", "")
        await _set_flag(db, "ytdlp_restart_attempted", "")
        db.add(SystemHealthLog(component="ytdlp_version", status="healthy",
                               message=f"yt-dlp up to date ({current})"))
        await db.commit()
        return

    # Loop guard: if we already restarted for this exact target and we are STILL
    # behind, the update isn't taking effect - stop retrying and ask for help.
    if await _get_flag(db, "ytdlp_restart_attempted") == latest:
        msg = (f"yt-dlp still {current} after an auto-update+restart targeting {latest}; "
               f"automatic update is not taking effect, manual update needed.")
        logger.error(msg)
        db.add(SystemHealthLog(component="ytdlp_version", status="unhealthy", message=msg))
        await db.commit()
        await NotificationService.broadcast("health_alert", {
            "component": "ytdlp", "status": "unhealthy", "message": msg})
        return

    logger.info("yt-dlp outdated (%s -> %s); upgrading", current, latest)
    success, output = await asyncio.to_thread(ytdlp.update)
    if not success:
        msg = f"yt-dlp auto-update to {latest} failed: {str(output)[:200]}"
        logger.warning(msg)
        db.add(SystemHealthLog(component="ytdlp_version", status="unhealthy", message=msg))
        await db.commit()
        await NotificationService.broadcast("health_alert", {
            "component": "ytdlp", "status": "unhealthy", "message": msg})
        return

    # pip upgraded on disk; the running process is still on the old version.
    await _set_flag(db, "ytdlp_update_pending", latest)
    db.add(SystemHealthLog(component="ytdlp_version", status="warning",
                           message=f"yt-dlp updated on disk {current} -> {latest}; restart to apply"))
    await db.commit()
    await NotificationService.broadcast("ytdlp_updated", {
        "old_version": current, "new_version": latest})

    if not settings.YTDLP_AUTO_RESTART:
        logger.info("Auto-restart disabled; yt-dlp %s will apply on next restart", latest)
        return

    # Only restart when idle so we don't interrupt an in-progress download.
    active = await db.scalar(
        select(func.count()).select_from(DownloadQueue).where(DownloadQueue.started_at.isnot(None))
    )
    if active:
        logger.info("Deferring restart to apply yt-dlp %s: %d active download(s)", latest, active)
        return

    # Mark that we are restarting for this target (loop guard), then exit so the
    # container restart policy brings us back on the new version.
    await _set_flag(db, "ytdlp_restart_attempted", latest)
    await db.commit()
    logger.warning("Restarting to apply yt-dlp %s (was %s)", latest, current)
    await NotificationService.broadcast("health_alert", {
        "component": "ytdlp", "status": "info",
        "message": f"Restarting to apply yt-dlp {latest}."})
    os.kill(os.getpid(), signal.SIGTERM)
