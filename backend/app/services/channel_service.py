import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting, Channel, Video, DownloadQueue
from app.schemas import ChannelCreate
from app.services.import_service import scan_folder_for_imports, import_matched_files
from app.services.metadata_service import write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.services.notification_service import NotificationService
from app.services.youtube_api_service import YouTubeAPIService
from app.services.ytdlp_service import YtdlpService
from app.utils.file_utils import sanitize_filename

logger = logging.getLogger(__name__)


class ChannelService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.ytdlp = YtdlpService()
        self.yt_api = YouTubeAPIService() if settings.has_youtube_api_key else None

    async def add_channel(self, data: ChannelCreate) -> Channel:
        from app.utils.platform_utils import detect_platform, get_tab_suffixes, is_playlist_url

        # Auto-detect platform from URL
        platform = detect_platform(data.url)
        is_playlist = is_playlist_url(data.url)

        # Resolve channel info via yt-dlp
        info = await asyncio.to_thread(self.ytdlp.get_channel_info, data.url)
        if not info:
            raise ValueError(f"Could not find channel for: {data.url}")

        if is_playlist:
            # Playlists use different keys than channels
            channel_id = info.get("id", "")
            channel_name = info.get("title") or info.get("channel") or "Unknown Playlist"
            channel_url = info.get("webpage_url") or data.url
        else:
            channel_id = info.get("channel_id") or info.get("id", "")
            channel_name = info.get("channel") or info.get("uploader") or info.get("title", "Unknown")
            # Prefer channel_url (base channel URL) over webpage_url (may include /featured etc.)
            channel_url = info.get("channel_url") or info.get("webpage_url") or data.url
            # Strip platform-specific tab suffixes so scanning can append /videos reliably
            for suffix in get_tab_suffixes(platform):
                if channel_url.endswith(suffix):
                    channel_url = channel_url[:-len(suffix)]
                    break

        # Check if already subscribed
        existing = await self.db.execute(
            select(Channel).where(Channel.channel_id == channel_id)
        )
        if existing.scalar_one_or_none():
            raise ValueError(f"Channel '{channel_name}' is already subscribed")

        # Extract banner URL from thumbnails list (widest image)
        banner_url = None
        thumbnails = info.get("thumbnails") or []
        for thumb in sorted(thumbnails, key=lambda t: t.get("width", 0), reverse=True):
            w = thumb.get("width", 0)
            h = thumb.get("height", 0)
            # Banner images are wide (aspect ratio > 2:1, width >= 1200)
            if w >= 1200 and h > 0 and w / h > 2:
                banner_url = thumb.get("url")
                break

        # Get thumbnail - try yt-dlp first, fall back to YouTube Data API
        thumbnail_url = info.get("thumbnail")
        if not thumbnail_url and platform == "youtube" and settings.has_youtube_api_key:
            try:
                from app.services.youtube_api_service import YouTubeAPIService
                api_svc = YouTubeAPIService()
                thumbnail_url = await api_svc.get_channel_thumbnail(channel_id)
            except Exception as e:
                logger.warning("Could not fetch thumbnail via API: %s", e)

        channel = Channel(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_url=channel_url,
            platform=platform,
            thumbnail_url=thumbnail_url,
            banner_url=banner_url,
            description=info.get("description"),
            quality=data.quality,
            naming_template=data.naming_template,
            download_dir=data.download_dir,
            enabled=data.enabled,
            auto_download=data.auto_download,
            health_status="healthy",
        )

        self.db.add(channel)
        await self.db.commit()
        await self.db.refresh(channel)

        # Generate Plex metadata (tvshow.nfo + poster.jpg)
        await asyncio.to_thread(
            write_tvshow_nfo,
            channel_name=channel_name,
            channel_id=channel_id,
            channel_url=channel_url,
            description=info.get("description"),
            thumbnail_url=info.get("thumbnail"),
            base_dir=data.download_dir,
            platform=platform,
        )

        logger.info("Added channel: %s (%s)", channel_name, channel_id)
        return channel

    async def refresh_channel_metadata(self, channel: Channel) -> Channel:
        """Re-fetch thumbnail, banner, and description from the platform."""
        info = await asyncio.to_thread(self.ytdlp.get_channel_info, channel.channel_url)

        if info:
            channel.thumbnail_url = info.get("thumbnail") or channel.thumbnail_url

        # Fall back to YouTube Data API for thumbnail if yt-dlp didn't return one
        if not channel.thumbnail_url and channel.platform == "youtube" and settings.has_youtube_api_key:
            try:
                from app.services.youtube_api_service import YouTubeAPIService
                api_svc = YouTubeAPIService()
                api_thumb = await api_svc.get_channel_thumbnail(channel.channel_id)
                if api_thumb:
                    channel.thumbnail_url = api_thumb
            except Exception as e:
                logger.warning("Could not fetch thumbnail via API: %s", e)

        if not info:
            raise ValueError("Could not fetch channel metadata")
        channel.description = info.get("description") or channel.description

        # Extract banner URL from thumbnails list (widest image)
        thumbnails = info.get("thumbnails") or []
        for thumb in sorted(thumbnails, key=lambda t: t.get("width", 0), reverse=True):
            w = thumb.get("width", 0)
            h = thumb.get("height", 0)
            if w >= 1200 and h > 0 and w / h > 2:
                channel.banner_url = thumb.get("url")
                break

        await self.db.commit()
        await self.db.refresh(channel)
        logger.info("Refreshed metadata for: %s", channel.channel_name)
        return channel

    async def scan_channel(self, channel: Channel) -> int:
        """Scan a channel for new videos. Returns count of newly discovered videos."""
        from app.utils.platform_utils import supports_api, supports_rss
        logger.info("Scanning channel: %s", channel.channel_name)

        platform = getattr(channel, "platform", "youtube")

        # Try YouTube Data API first (only for platforms that support it), fall back to yt-dlp
        if self.yt_api and supports_api(platform):
            try:
                video_list = await self.yt_api.get_channel_videos(channel.channel_id)
            except Exception as e:
                logger.warning("YouTube API failed for %s, falling back to yt-dlp: %s", channel.channel_name, e)
                video_list = await asyncio.to_thread(
                    self.ytdlp.get_channel_video_list, channel.channel_url, platform
                )
        else:
            video_list = await asyncio.to_thread(
                self.ytdlp.get_channel_video_list, channel.channel_url, platform
            )

        logger.info("Video list returned %d entries for %s (url: %s)",
                    len(video_list) if video_list else 0, channel.channel_name, channel.channel_url)

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

        # First pass: identify new video IDs
        new_entries = []
        for entry in video_list:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if not vid_id or vid_id in existing_ids:
                continue
            new_entries.append(entry)

        # Fetch upload dates from RSS feed (free, no auth, covers ~15 recent videos  - YouTube only)
        rss_dates = await asyncio.to_thread(
            self.ytdlp.get_rss_upload_dates, channel.channel_id, platform
        )

        # Pre-fetch episode counts per season to avoid per-video COUNT queries
        season_counts_result = await self.db.execute(
            select(Video.season, func.count(Video.id))
            .where(Video.channel_id == channel.id)
            .group_by(Video.season)
        )
        season_episode_counts = {row[0]: row[1] for row in season_counts_result.all()}

        # Second pass: resolve upload dates for all new entries
        enriched_entries = []
        consecutive_metadata_failures = 0
        max_consecutive_failures = 3
        skip_metadata_fetch = False

        for entry in new_entries:
            vid_id = entry.get("id") or entry.get("video_id", "")
            upload_date = (
                self._parse_upload_date(entry.get("release_date"))
                or self._parse_upload_date(entry.get("upload_date"))
            )
            title = entry.get("title", "Untitled")
            description = entry.get("description")
            duration = entry.get("duration")
            thumbnail = entry.get("thumbnail")

            if not upload_date and vid_id in rss_dates:
                upload_date = self._parse_upload_date(rss_dates[vid_id])

            if not upload_date and not skip_metadata_fetch:
                logger.info("Fetching metadata for %s to get upload date", vid_id)
                full_info = await asyncio.to_thread(self.ytdlp.get_video_info, vid_id, platform)
                if full_info:
                    fetched_date = (
                        self._parse_upload_date(full_info.get("release_date"))
                        or self._parse_upload_date(full_info.get("upload_date"))
                    )
                    if fetched_date:
                        upload_date = fetched_date
                        consecutive_metadata_failures = 0
                    else:
                        consecutive_metadata_failures += 1
                    title = full_info.get("title") or title
                    description = full_info.get("description") or description
                    duration = full_info.get("duration") or duration
                    thumbnail = full_info.get("thumbnail") or thumbnail
                else:
                    consecutive_metadata_failures += 1

                if consecutive_metadata_failures >= max_consecutive_failures:
                    logger.warning(
                        "Skipping per-video metadata fetch after %d consecutive failures "
                        "(likely bot detection). Remaining videos will use fallback dates.",
                        consecutive_metadata_failures,
                    )
                    skip_metadata_fetch = True

            if not upload_date:
                from datetime import date as date_cls
                upload_date = date_cls.today()
                logger.warning("Could not determine upload date for %s, defaulting to today", vid_id)

            enriched_entries.append({
                "vid_id": vid_id, "title": title, "description": description,
                "upload_date": upload_date, "duration": duration, "thumbnail": thumbnail,
            })

        # Sort by upload date (oldest first) so episode numbers are chronological
        enriched_entries.sort(key=lambda e: e["upload_date"])

        # Third pass: assign episode numbers and insert into DB
        new_count = 0
        for entry in enriched_entries:
            vid_id = entry["vid_id"]
            upload_date = entry["upload_date"]
            season = upload_date.year

            # Calculate episode number from pre-fetched counts, increment locally
            season_episode_counts.setdefault(season, 0)
            season_episode_counts[season] += 1
            episode = season_episode_counts[season]

            title = entry["title"]
            description = entry["description"]
            duration = entry["duration"]
            thumbnail = entry["thumbnail"]

            video = Video(
                video_id=vid_id,
                channel_id=channel.id,
                title=title,
                description=description,
                upload_date=upload_date,
                duration=duration,
                thumbnail_url=thumbnail,
                season=season,
                episode=episode,
                status="pending",
            )

            self.db.add(video)
            await self.db.flush()

            # Detect YouTube Shorts (duration <= 60s)
            is_short = False
            if duration and duration <= 60:
                is_short = True
            video.is_short = is_short

            # Filter shorts based on global + channel settings
            if is_short:
                shorts_globally_enabled = await self._get_setting_bool("shorts_enabled", False)
                if not shorts_globally_enabled or not channel.include_shorts:
                    video.status = "skipped"
                    logger.info("Skipped short: %s (%s)  - %ds", vid_id, title, duration or 0)
                    new_count += 1
                    continue

            # Check livestream / long video filter
            max_dur = await self._get_max_duration()
            if max_dur and max_dur > 0 and duration and duration > max_dur:
                video.status = "pending_review"
                logger.info(
                    "Video %s (%s) exceeds max duration (%ds > %ds)  - needs review",
                    vid_id, title, duration, max_dur,
                )
                hours = duration // 3600
                mins = (duration % 3600) // 60
                await NotificationService.broadcast("review_required", {
                    "video_id": vid_id,
                    "title": title,
                    "channel": channel.channel_name,
                    "duration": f"{hours}h {mins}m" if hours else f"{mins}m",
                    "message": f"Long video detected: {title} ({hours}h {mins}m). Queue manually if desired.",
                })
            else:
                # Auto-queue for download (if enabled for this channel)
                if channel.auto_download:
                    self.db.add(DownloadQueue(video_id=video.id))
                    video.status = "queued"
                else:
                    video.status = "pending"

            new_count += 1

        channel.last_scanned_at = datetime.now(timezone.utc)
        channel.total_videos = len(existing_ids) + new_count
        if new_count > 0:
            channel.health_status = "healthy"

        await self.db.commit()
        logger.info("Found %d new videos for %s", new_count, channel.channel_name)

        # Auto-scan download directory for existing video files that match pending videos
        imported = await self._auto_import_existing(channel)
        if imported > 0:
            logger.info("Auto-imported %d existing files for %s", imported, channel.channel_name)

        # Rename any completed videos whose file paths don't match current naming template
        renamed = await self._rename_existing_files(channel)
        if renamed > 0:
            logger.info("Renamed %d files for %s to match naming template", renamed, channel.channel_name)

        return new_count

    async def _get_max_duration(self) -> int | None:
        """Read max_video_duration from AppSettings. Returns seconds or None."""
        try:
            result = await self.db.execute(
                select(AppSetting).where(AppSetting.key == "max_video_duration")
            )
            setting = result.scalar_one_or_none()
            if setting:
                import json
                val = json.loads(setting.value)
                return int(val) if val else None
        except Exception:
            pass
        return None

    async def _get_setting_bool(self, key: str, default: bool = False) -> bool:
        """Read a boolean AppSetting."""
        try:
            result = await self.db.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            setting = result.scalar_one_or_none()
            if setting:
                import json
                val = json.loads(setting.value)
                return bool(val)
        except Exception:
            pass
        return default

    async def _rename_existing_files(self, channel: Channel) -> int:
        """Rename completed video files to match the current naming template."""
        result = await self.db.execute(
            select(Video)
            .where(Video.channel_id == channel.id)
            .where(Video.status == "completed")
            .where(Video.file_path.isnot(None))
        )
        videos = result.scalars().all()

        renamed_count = 0
        for video in videos:
            old_path = Path(video.file_path)
            if not old_path.exists():
                continue

            # Build expected path from current naming template
            expected_base = build_output_path(
                channel_name=channel.channel_name,
                video_title=video.title,
                video_id=video.video_id,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                naming_template=channel.naming_template,
                base_dir=channel.download_dir,
            )
            expected_path = Path(expected_base + old_path.suffix)

            if old_path == expected_path:
                continue

            try:
                os.makedirs(expected_path.parent, exist_ok=True)
                await asyncio.to_thread(shutil.move, str(old_path), str(expected_path))
                video.file_path = str(expected_path)
                renamed_count += 1
                logger.info("Renamed: %s -> %s", old_path, expected_path)

                # Also move accompanying .nfo file if present
                old_nfo = old_path.with_suffix(".nfo")
                if old_nfo.exists():
                    new_nfo = expected_path.with_suffix(".nfo")
                    await asyncio.to_thread(shutil.move, str(old_nfo), str(new_nfo))

                # Clean up empty parent directories
                try:
                    old_parent = old_path.parent
                    while old_parent != Path(channel.download_dir or settings.DOWNLOAD_DIR):
                        if old_parent.exists() and not any(old_parent.iterdir()):
                            old_parent.rmdir()
                            old_parent = old_parent.parent
                        else:
                            break
                except Exception:
                    pass
            except Exception as e:
                logger.warning("Failed to rename %s: %s", old_path, e)

        if renamed_count > 0:
            await self.db.commit()
        return renamed_count

    async def _auto_import_existing(self, channel: Channel) -> int:
        """Check the channel's download directory for files that match un-downloaded videos."""
        base_dir = channel.download_dir or settings.DOWNLOAD_DIR
        channel_dir = Path(base_dir) / sanitize_filename(channel.channel_name)

        if not channel_dir.is_dir():
            return 0

        # Scan all subdirectories (Season folders, root, etc.)
        dirs_to_scan = [channel_dir] + [d for d in channel_dir.iterdir() if d.is_dir()]

        total_imported = 0
        for scan_dir in dirs_to_scan:
            try:
                matches = await scan_folder_for_imports(
                    self.db, channel.id, str(scan_dir), threshold=0.75,
                )
                if matches:
                    result = await import_matched_files(
                        self.db, channel.id, matches,
                    )
                    total_imported += result["imported"]
            except Exception as e:
                logger.warning("Auto-import scan of %s failed: %s", scan_dir, e)

        return total_imported

    async def delete_channel_files(self, channel: Channel):
        """Delete all downloaded files for a channel."""
        base = channel.download_dir or settings.DOWNLOAD_DIR
        channel_dir = Path(base) / sanitize_filename(channel.channel_name)
        if channel_dir.exists():
            await asyncio.to_thread(shutil.rmtree, str(channel_dir))
            logger.info("Deleted files for channel: %s at %s", channel.channel_name, channel_dir)

    @staticmethod
    def _parse_upload_date(date_str: str | None):
        from app.utils.file_utils import parse_upload_date
        return parse_upload_date(date_str)
