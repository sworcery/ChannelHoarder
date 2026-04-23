import asyncio
import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
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

# Track channels currently being scanned to prevent concurrent scan races
_scanning_channels: set[int] = set()


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

        # Get thumbnail - prefer YouTube Data API (more reliable), fall back to yt-dlp
        thumbnail_url = None
        if platform == "youtube" and settings.has_youtube_api_key:
            try:
                from app.services.youtube_api_service import YouTubeAPIService
                api_svc = YouTubeAPIService()
                thumbnail_url = await api_svc.get_channel_thumbnail(channel_id)
            except Exception as e:
                logger.warning("Could not fetch thumbnail via API: %s", e)
        if not thumbnail_url:
            thumbnail_url = info.get("thumbnail")

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
            # Scan immediately on first tick so the user sees videos appear quickly
            next_scan_at=datetime.now(timezone.utc).replace(tzinfo=None),
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
        # Prefer YouTube Data API for thumbnail (more reliable than yt-dlp tab extraction)
        if channel.platform == "youtube" and settings.has_youtube_api_key:
            try:
                from app.services.youtube_api_service import YouTubeAPIService
                api_svc = YouTubeAPIService()
                api_thumb = await api_svc.get_channel_thumbnail(channel.channel_id)
                if api_thumb:
                    channel.thumbnail_url = api_thumb
            except Exception as e:
                logger.warning("Could not fetch thumbnail via API: %s", e)

        # Also try yt-dlp for banner and description
        info = await asyncio.to_thread(self.ytdlp.get_channel_info, channel.channel_url)

        if info:
            if not channel.thumbnail_url:
                channel.thumbnail_url = info.get("thumbnail") or channel.thumbnail_url
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
        from app.utils.platform_utils import supports_api, supports_rss, is_playlist_url

        # Prevent concurrent scans on the same channel
        if channel.id in _scanning_channels:
            logger.info("Skipping scan for %s - already in progress", channel.channel_name)
            return 0
        _scanning_channels.add(channel.id)
        try:
            return await self._scan_channel_inner(channel)
        finally:
            _scanning_channels.discard(channel.id)

    async def _scan_channel_inner(self, channel: Channel) -> int:
        """Internal scan implementation."""
        from app.utils.platform_utils import supports_api, supports_rss, is_playlist_url
        logger.info("Scanning channel: %s", channel.channel_name)

        # Batch-read all settings once at scan start (avoids per-video DB queries)
        import json
        self._settings_cache: dict = {}
        try:
            result = await self.db.execute(select(AppSetting))
            for s in result.scalars().all():
                try:
                    self._settings_cache[s.key] = json.loads(s.value)
                except (json.JSONDecodeError, TypeError):
                    self._settings_cache[s.key] = s.value
        except Exception:
            pass

        platform = getattr(channel, "platform", "youtube")
        is_playlist = is_playlist_url(channel.channel_url)

        # Try YouTube Data API first (only for channels that support it, not playlists), fall back to yt-dlp
        # yt-dlp path fetches all tabs (videos/shorts/streams) separately for better classification
        if self.yt_api and supports_api(platform) and not is_playlist:
            try:
                video_list = await self.yt_api.get_channel_videos(channel.channel_id)
            except Exception as e:
                logger.warning("YouTube API failed for %s, falling back to yt-dlp: %s", channel.channel_name, e)
                video_list = await asyncio.to_thread(
                    self.ytdlp.get_channel_video_list_all_tabs, channel.channel_url, platform
                )
        else:
            # Playlists and non-API platforms always use yt-dlp
            video_list = await asyncio.to_thread(
                self.ytdlp.get_channel_video_list_all_tabs, channel.channel_url, platform
            )

        logger.info("Video list returned %d entries for %s (url: %s)",
                    len(video_list) if video_list else 0, channel.channel_name, channel.channel_url)

        if not video_list:
            logger.warning("No videos found for channel: %s", channel.channel_name)
            channel.last_scanned_at = datetime.now(timezone.utc)
            channel.next_scan_at = await self._compute_next_scan_at()
            await self.db.commit()
            return 0

        # Get existing video IDs for this channel
        result = await self.db.execute(
            select(Video.video_id).where(Video.channel_id == channel.id)
        )
        existing_ids = {row[0] for row in result.all()}

        # Phase A: Reclassify existing videos based on current tab data
        # This catches videos that were misclassified on a previous scan (e.g. shorts
        # that were scanned before tab-based detection, or videos the creator moved
        # between tabs). Also auto-cleans up files for newly-identified shorts/
        # livestreams when the user hasn't enabled those categories.
        reclassified_count = await self._reclassify_existing_videos(channel, video_list)

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
            source_tab = entry.get("_source_tab", "videos")
            live_status = entry.get("live_status")

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
                    live_status = full_info.get("live_status") or live_status
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
                "source_tab": source_tab, "live_status": live_status,
            })

        # Sort by upload date (oldest first) so episode numbers are chronological
        enriched_entries.sort(key=lambda e: e["upload_date"])

        # Third pass: assign episode numbers and insert into DB
        # Shorts are excluded from episode numbering
        new_count = 0
        shorts_threshold = channel.min_video_duration if channel.min_video_duration else 30
        for entry in enriched_entries:
            vid_id = entry["vid_id"]
            upload_date = entry["upload_date"]
            season = upload_date.year

            title = entry["title"]
            description = entry["description"]
            duration = entry["duration"]
            thumbnail = entry["thumbnail"]
            source_tab = entry.get("source_tab", "videos")
            live_status = entry.get("live_status")

            # Pre-detect shorts and livestreams before assigning episode numbers
            # Primary signal: YouTube channel tab (definitive)
            is_short_entry = (source_tab == "shorts")
            is_livestream_entry = (source_tab == "streams")

            # Fallback heuristics for API-based or single-tab scans
            if not is_short_entry and not is_livestream_entry:
                if duration and duration <= shorts_threshold:
                    is_short_entry = True
                elif title and ("#shorts" in title.lower() or "#short" in title.lower()):
                    is_short_entry = True
                elif live_status in ("is_live", "was_live", "is_upcoming", "post_live"):
                    is_livestream_entry = True

            # Only assign episode numbers to regular videos (shorts/livestreams get 0)
            if is_short_entry or is_livestream_entry:
                episode = 0
            else:
                season_episode_counts.setdefault(season, 0)
                season_episode_counts[season] += 1
                episode = season_episode_counts[season]

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

            # Re-check existence right before insert to handle concurrent scans
            # and orphan records from previously deleted channels
            existing_check = await self.db.execute(
                select(Video).where(Video.video_id == vid_id).limit(1)
            )
            existing_video = existing_check.scalar_one_or_none()
            if existing_video is not None:
                if existing_video.channel_id == channel.id:
                    # Same channel, concurrent scan already inserted it
                    logger.debug("Skipping video %s (already exists for this channel)", vid_id)
                    continue
                # Orphan record from a deleted channel -- check if old channel still exists
                old_channel_check = await self.db.execute(
                    select(Channel.id).where(Channel.id == existing_video.channel_id).limit(1)
                )
                if old_channel_check.scalar_one_or_none() is None:
                    # Orphan: old channel was deleted, claim this video for the new channel
                    logger.info("Claiming orphan video %s (old channel_id=%d no longer exists)",
                                vid_id, existing_video.channel_id)
                    existing_video.channel_id = channel.id
                    existing_video.title = title
                    existing_video.description = description
                    existing_video.upload_date = upload_date
                    existing_video.duration = duration
                    existing_video.thumbnail_url = thumbnail
                    existing_video.season = season
                    existing_video.episode = episode
                    existing_video.status = "pending"
                    existing_video.monitored = True
                    existing_video.is_short = False
                    existing_video.file_path = None
                    existing_video.file_size = None
                    existing_video.quality_downloaded = None
                    existing_video.error_code = None
                    existing_video.error_message = None
                    existing_video.error_details = None
                    existing_video.retry_count = 0
                    video = existing_video  # Use the existing record for shorts/queue logic below
                else:
                    # Video belongs to a different, active channel -- skip
                    logger.debug("Skipping video %s (belongs to channel_id=%d)", vid_id, existing_video.channel_id)
                    continue

            try:
                async with self.db.begin_nested():
                    self.db.add(video)
                    await self.db.flush()
            except IntegrityError:
                self.db.expunge(video)
                logger.debug("Skipping duplicate video %s after IntegrityError", vid_id)
                continue

            # Set the short/livestream flags (already detected before episode numbering)
            video.is_short = is_short_entry
            video.is_livestream = is_livestream_entry

            # Filter shorts based on global + channel settings
            if is_short_entry:
                shorts_globally_enabled = bool(self._settings_cache.get("shorts_enabled", False))
                if not shorts_globally_enabled or not channel.include_shorts:
                    video.status = "skipped"
                    video.monitored = False
                    logger.info("Skipped short: %s (%s)  - %ds", vid_id, title, duration or 0)
                    new_count += 1
                    continue

            # Filter livestreams based on global + channel settings
            if is_livestream_entry:
                livestreams_globally_enabled = bool(self._settings_cache.get("livestreams_enabled", False))
                if not livestreams_globally_enabled or not channel.include_livestreams:
                    video.status = "skipped"
                    video.monitored = False
                    logger.info("Skipped livestream: %s (%s)", vid_id, title)
                    new_count += 1
                    continue

            # Check per-channel minimum duration filter
            if channel.min_video_duration and duration and duration < channel.min_video_duration:
                video.status = "skipped"
                video.monitored = False
                logger.info("Skipped short video: %s (%s) - %ds < %ds minimum",
                            vid_id, title, duration, channel.min_video_duration)
                new_count += 1
                continue

            # Check livestream / long video filter
            max_dur_val = self._settings_cache.get("max_video_duration")
            max_dur = int(max_dur_val) if max_dur_val else None
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
        channel.next_scan_at = await self._compute_next_scan_at()
        channel.total_videos = len(existing_ids) + new_count
        if new_count > 0:
            channel.health_status = "healthy"

        await self.db.commit()
        logger.info("Found %d new videos for %s", new_count, channel.channel_name)

        # Phase C: Renumber episodes if Phase A reclassified anything
        if reclassified_count > 0:
            from app.utils.renumber import renumber_channel_episodes
            result = await self.db.execute(
                select(Video)
                .where(Video.channel_id == channel.id)
                .order_by(Video.upload_date.asc(), Video.id.asc())
            )
            all_videos = result.scalars().all()
            renamed = renumber_channel_episodes(all_videos, channel)
            await self.db.commit()
            if renamed > 0:
                logger.info("Renumbered %d episodes for %s after reclassification", renamed, channel.channel_name)

        # Auto-scan download directory for existing video files that match pending videos
        imported = await self._auto_import_existing(channel)
        if imported > 0:
            logger.info("Auto-imported %d existing files for %s", imported, channel.channel_name)

        # Rename any completed videos whose file paths don't match current naming template
        renamed = await self._rename_existing_files(channel)
        if renamed > 0:
            logger.info("Renamed %d files for %s to match naming template", renamed, channel.channel_name)

        return new_count

    async def _reclassify_existing_videos(self, channel: Channel, video_list: list[dict]) -> int:
        """Re-check tab classification for existing videos and auto-clean disabled categories.

        For each existing video:
        1. Determine if it should be flagged as short/livestream using the same signals
           used for new videos (tab source first, then heuristics).
        2. If the classification flipped from False -> True and the filter is disabled,
           delete the downloaded files and reset status to skipped.
        3. Update the database flags.

        Returns the number of videos whose classification changed.
        """
        import os

        # Build tab map from the fetched video list
        tab_map: dict[str, str] = {}
        live_status_map: dict[str, str | None] = {}
        for entry in video_list:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if vid_id:
                tab_map[vid_id] = entry.get("_source_tab", "videos")
                live_status_map[vid_id] = entry.get("live_status")

        shorts_threshold = channel.min_video_duration if channel.min_video_duration else 30
        # Read settings from cache if available, otherwise fall back to DB
        shorts_globally_enabled = bool(self._settings_cache.get("shorts_enabled", False)) if hasattr(self, '_settings_cache') else await self._get_setting_bool("shorts_enabled", False)
        livestreams_globally_enabled = bool(self._settings_cache.get("livestreams_enabled", False)) if hasattr(self, '_settings_cache') else await self._get_setting_bool("livestreams_enabled", False)
        shorts_allowed = shorts_globally_enabled and channel.include_shorts
        livestreams_allowed = livestreams_globally_enabled and channel.include_livestreams

        # Load all existing videos for this channel
        result = await self.db.execute(
            select(Video).where(Video.channel_id == channel.id)
        )
        videos = result.scalars().all()

        changed = 0
        for video in videos:
            # Never reclassify a video that's actively downloading
            if video.status == "downloading":
                continue

            # Determine correct classification
            source_tab = tab_map.get(video.video_id)
            live_status = live_status_map.get(video.video_id)

            # Primary: tab source
            should_be_short = source_tab == "shorts"
            should_be_livestream = source_tab == "streams"

            # Secondary heuristics (only apply if not already classified by tab)
            if not should_be_short and not should_be_livestream:
                if video.title and ("#shorts" in video.title.lower() or "#short" in video.title.lower()):
                    should_be_short = True
                elif video.duration and video.duration <= shorts_threshold:
                    should_be_short = True
                elif live_status in ("is_live", "was_live", "is_upcoming", "post_live"):
                    should_be_livestream = True

            # Check if classification flipped
            short_flipped = should_be_short and not video.is_short
            livestream_flipped = should_be_livestream and not video.is_livestream

            if not short_flipped and not livestream_flipped:
                continue

            # Apply reclassification
            if short_flipped:
                video.is_short = True
                # Auto-clean if shorts aren't allowed
                if not shorts_allowed:
                    await self._auto_delete_video_files(video)
                    logger.info("Reclassified short and cleaned up: %s (%s)", video.video_id, video.title)

            if livestream_flipped:
                video.is_livestream = True
                # Auto-clean if livestreams aren't allowed
                if not livestreams_allowed:
                    await self._auto_delete_video_files(video)
                    logger.info("Reclassified livestream and cleaned up: %s (%s)", video.video_id, video.title)

            # Reset episode to 0 for excluded categories
            if should_be_short or should_be_livestream:
                video.episode = 0

            changed += 1

        if changed > 0:
            await self.db.commit()
            logger.info("Reclassified %d videos for %s", changed, channel.channel_name)

        return changed

    async def _auto_delete_video_files(self, video: Video):
        """Delete downloaded files for a video and reset its fields to skipped state."""
        from app.utils.file_utils import delete_video_files

        if video.file_path:
            delete_video_files(video.file_path)

        # Remove from queue if present
        queue_result = await self.db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == video.id)
        )
        queue_entry = queue_result.scalar_one_or_none()
        if queue_entry:
            await self.db.delete(queue_entry)

        video.status = "skipped"
        video.monitored = False
        video.file_path = None
        video.file_size = None

    async def _compute_next_scan_at(self) -> datetime:
        """Compute the next scan timestamp for a channel based on configured window."""
        import json
        from app.utils.scan_window import compute_next_scan_at

        start_hour = None
        end_hour = None
        min_offset_hours = 12
        try:
            result = await self.db.execute(
                select(AppSetting).where(AppSetting.key == "scan_window_start_hour")
            )
            setting = result.scalar_one_or_none()
            if setting:
                start_hour = int(json.loads(setting.value))

            result = await self.db.execute(
                select(AppSetting).where(AppSetting.key == "scan_window_end_hour")
            )
            setting = result.scalar_one_or_none()
            if setting:
                end_hour = int(json.loads(setting.value))

            result = await self.db.execute(
                select(AppSetting).where(AppSetting.key == "scan_min_interval_hours")
            )
            setting = result.scalar_one_or_none()
            if setting:
                min_offset_hours = int(json.loads(setting.value))
        except Exception:
            pass

        return compute_next_scan_at(
            start_hour=start_hour,
            end_hour=end_hour,
            min_offset_hours=min_offset_hours,
        )

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

                # Also move accompanying files (.nfo, subtitles) if present
                for suffix in [".nfo", ".en.vtt", ".en.srt"]:
                    old_extra = old_path.with_suffix(suffix)
                    if old_extra.exists():
                        new_extra = expected_path.with_suffix(suffix)
                        await asyncio.to_thread(shutil.move, str(old_extra), str(new_extra))

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
