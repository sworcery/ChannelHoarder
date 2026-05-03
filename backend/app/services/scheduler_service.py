import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(self):
        self.scheduler = AsyncIOScheduler()

    async def start(self):
        from app.tasks.scan_channels import scan_due_channels
        from app.tasks.process_queue import process_download_queue
        from app.tasks.health_check import check_system_health
        from app.tasks.ytdlp_update import check_ytdlp_update

        # Channel scan tick (every 10 minutes; each channel scans on its own
        # random schedule within the configured daily window)
        self.scheduler.add_job(
            scan_due_channels,
            IntervalTrigger(minutes=10),
            id="scan_due_channels",
            replace_existing=True,
            name="Scan channels whose next_scan_at has arrived",
        )

        # Queue processor (every 30 seconds)
        self.scheduler.add_job(
            process_download_queue,
            IntervalTrigger(seconds=30),
            id="process_queue",
            replace_existing=True,
            name="Process download queue",
        )

        # System health check (every 6 hours)
        self.scheduler.add_job(
            check_system_health,
            IntervalTrigger(hours=6),
            id="health_check",
            replace_existing=True,
            name="Check system health",
        )

        # yt-dlp update check (daily at 4 AM)
        self.scheduler.add_job(
            check_ytdlp_update,
            CronTrigger(hour=4, minute=0),
            id="ytdlp_update",
            replace_existing=True,
            name="Check for yt-dlp updates",
        )

        # Quality upgrade check (daily at 5 AM)
        from app.tasks.quality_upgrade import check_quality_upgrades
        self.scheduler.add_job(
            check_quality_upgrades,
            CronTrigger(hour=5, minute=0),
            id="quality_upgrade",
            replace_existing=True,
            name="Check for quality upgrades",
        )

        # PO token server watchdog (every 5 minutes)
        from app.tasks.pot_watchdog import check_pot_server
        self.scheduler.add_job(
            check_pot_server,
            IntervalTrigger(minutes=5),
            id="pot_watchdog",
            replace_existing=True,
            name="PO token server watchdog",
        )

        # Cookie file watcher (every 60 seconds)
        from app.tasks.cookie_watcher import watch_cookie_file
        self.scheduler.add_job(
            watch_cookie_file,
            IntervalTrigger(seconds=60),
            id="cookie_watcher",
            replace_existing=True,
            name="Watch cookies.txt for external updates",
        )

        # Quick download cleanup (every 6 hours)
        from app.tasks.quick_download_cleanup import cleanup_quick_downloads
        self.scheduler.add_job(
            cleanup_quick_downloads,
            IntervalTrigger(hours=6),
            id="quick_download_cleanup",
            replace_existing=True,
            name="Clean up expired quick-download files",
        )

        self.scheduler.start()
        logger.info("Scheduler started with %d jobs", len(self.scheduler.get_jobs()))

    async def shutdown(self):
        self.scheduler.shutdown(wait=True)
        logger.info("Scheduler shut down")

    def reschedule_scan(self, new_cron: str):
        """No-op retained for backwards compat. Scan timing is now per-channel."""
        logger.info("Scan cron update ignored (window-based scheduling): %s", new_cron)
