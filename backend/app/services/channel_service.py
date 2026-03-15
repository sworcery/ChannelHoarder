import asyncio
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Channel, Video, DownloadQueue
from app.schemas import ChannelCreate
from app.services.youtube_api_service import YouTubeAPIService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)


class ChannelService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ytdlp = YtdlpService()
        self.yt_api = YouTubeAPIService() if settings.has_youtube_api_key else None

    async def add_channel(self, data: ChannelCreate) -> Channel:
        # Resolve channel info via yt-dlp
        info = await asyncio.to_thread(self.ytdlp.get_channel_info, data.url)
        if not info:
            raise ValueError(f"Could not find YouTube channel for: {data.url}")

        channel_id = info.get("channel_id") or info.get("id", "")
        channel_name = info.get("channel") or info.get("uploader") or info.get("title", "Unknown")
        channel_url = info.get("channel_url") or info.get("webpage_url") or data.url

        # Check if already subscribed
        existing = await self.db.execute(
            select(Channel).where(Channel.channel_id == channel_id)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Channel '{channel_name}' is already subscribed")

        channel = Channel(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_url=channel_url,
            thumbnail_url=info.get("thumbnail"),
            description=info.get("description"),
            quality=data.quality,
            naming_template=data.naming_template,
            enabled=data.enabled,
            health_status="healthy",
        )

        self.db.add(channel)
        await self.db.commit()
        await self.db.refresh(channel)

        logger.info("Added channel: %s (%s)", channel_name, channel_id)
        return channel

    async def scan_channel(self, channel: Channel) -> int:
        """Scan a channel for new videos. Returns count of newly discovered videos."""
        logger.info("Scanning channel: %s", channel.channel_name)

        # Try YouTube Data API first, fall back to yt-dlp
        if self.yt_api:
            try:
                video_list = await self.yt_api.get_channel_videos(channel.channel_id)
            except Exception as e:
                logger.warning("YouTube API failed for %s, falling back to yt-dlp: %s", channel.channel_name, e)
                video_list = await asyncio.to_thread(
                    self.ytdlp.get_channel_video_list, channel.channel_url
                )
        else:
            video_list = await asyncio.to_thread(
                self.ytdlp.get_channel_video_list, channel.channel_url
            )

        if not video_list:
            logger.warning("No videos found for channel: %s", channel.channel_name)
            channel.last_scanned_at = datetime.now(timezone.utc)
            await self.db.commit()
            return 0

        # Get existing video IDs for this channel
        result = await self.db.execute(
            select(Video.video_id).where(Video.channel_id == channel.id)
        )
        existing_ids = {row[0] for row in result.all()}

        new_count = 0
        for entry in video_list:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if not vid_id or vid_id in existing_ids:
                continue

            upload_date = self._parse_upload_date(entry.get("upload_date"))
            if not upload_date:
                # Flat extraction often omits upload_date; default to today
                from datetime import date
                upload_date = date.today()

            season = upload_date.year

            # Calculate episode number: count existing videos in same channel+season + 1
            episode_count = await self.db.execute(
                select(func.count(Video.id))
                .where(Video.channel_id == channel.id)
                .where(Video.season == season)
            )
            episode = episode_count.scalar() + 1

            video = Video(
                video_id=vid_id,
                channel_id=channel.id,
                title=entry.get("title", "Untitled"),
                description=entry.get("description"),
                upload_date=upload_date,
                duration=entry.get("duration"),
                thumbnail_url=entry.get("thumbnail"),
                season=season,
                episode=episode,
                status="pending",
            )

            self.db.add(video)
            await self.db.flush()

            # Auto-queue for download
            self.db.add(DownloadQueue(video_id=video.id))
            video.status = "queued"

            new_count += 1

        channel.last_scanned_at = datetime.now(timezone.utc)
        channel.total_videos = len(existing_ids) + new_count
        if new_count > 0:
            channel.health_status = "healthy"

        await self.db.commit()
        logger.info("Found %d new videos for %s", new_count, channel.channel_name)
        return new_count

    async def delete_channel_files(self, channel: Channel):
        """Delete all downloaded files for a channel."""
        channel_dir = Path(settings.DOWNLOAD_DIR) / self._safe_dirname(channel.channel_name)
        if channel_dir.exists():
            await asyncio.to_thread(shutil.rmtree, str(channel_dir))
            logger.info("Deleted files for channel: %s at %s", channel.channel_name, channel_dir)

    @staticmethod
    def _parse_upload_date(date_str: str | None):
        if not date_str:
            return None
        try:
            from datetime import date
            if len(date_str) == 8:
                return date(int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8]))
            return date.fromisoformat(date_str[:10])
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _safe_dirname(name: str) -> str:
        """Make a safe directory name."""
        unsafe = '<>:"/\\|?*'
        result = name
        for char in unsafe:
            result = result.replace(char, "_")
        return result.strip(". ")
