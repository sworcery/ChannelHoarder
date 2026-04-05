import logging

from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)


async def check_ytdlp_update():
    """Scheduled task: check for and apply yt-dlp updates."""
    ytdlp = YtdlpService()
    old_version = ytdlp.get_version()

    logger.info("Checking for yt-dlp updates (current: %s)", old_version)

    success, message = ytdlp.update()
    new_version = ytdlp.get_version()

    if success and new_version != old_version:
        logger.info("yt-dlp updated: %s -> %s", old_version, new_version)
        await NotificationService.broadcast("ytdlp_updated", {
            "old_version": old_version,
            "new_version": new_version,
        })
    elif success:
        logger.info("yt-dlp is already up to date (%s)", old_version)
    else:
        logger.warning("yt-dlp update failed: %s", message)
