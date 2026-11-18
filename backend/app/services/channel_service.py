import asyncio
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import or_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting, Channel, Video, DownloadQueue
from app.schemas import ChannelCreate
from app.services.download_service import enqueue_video
from app.services.import_service import (
    import_matched_files, load_owned_paths, scan_folder_for_imports,
)
from app.services.metadata_service import write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.services.notification_service import NotificationService
from app.services.youtube_api_service import YouTubeAPIService
from app.services.ytdlp_service import ChannelListingError, YtdlpService, channel_avatar_url
from app.utils.file_utils import channel_folder_name, file_keys, sanitize_filename
from app.utils.error_codes import ErrorCode, classify_error

logger = logging.getLogger(__name__)

# Fetch failures that will never succeed by retrying, so they must not count
# toward the scan's failure breaker or be deferred to the next scan.
_NON_TRANSIENT_FETCH_CODES = frozenset({
    ErrorCode.VIDEO_PRIVATE,
    ErrorCode.VIDEO_REMOVED,
    ErrorCode.VIDEO_UNAVAILABLE,
    ErrorCode.GEO_BLOCKED,
    ErrorCode.LIVESTREAM_SCHEDULED,
})

# last_error_code values a deferral warning may replace: our own codes plus the
# informational per-video codes a single failed download leaves behind. Anything
# more severe (disk full, expired cookies, outdated yt-dlp) is left alone.
_DEGRADED_OVERWRITABLE = (
    None,
    ErrorCode.SCAN_FAILED.value,
    ErrorCode.METADATA_DEGRADED.value,
    ErrorCode.VIDEO_PRIVATE.value,
    ErrorCode.VIDEO_REMOVED.value,
    ErrorCode.VIDEO_UNAVAILABLE.value,
    ErrorCode.GEO_BLOCKED.value,
    ErrorCode.LIVESTREAM_SCHEDULED.value,
    ErrorCode.QUALITY_TOO_LOW.value,
)

# YouTube Shorts are <= 60s. This is the shorts-classification cutoff and is kept
# separate from Channel.min_video_duration, which is a download length filter - reusing
# the filter as the shorts threshold caused already-downloaded videos to be reclassified
# as shorts and auto-deleted when a large min_video_duration was set.
SHORTS_MAX_DURATION = 60

# YouTube allows Shorts up to 3 minutes. A longer video listed on the Shorts tab is
# a bad listing, not a Short, so it is never tagged from the tab.
SHORTS_TAB_MAX_DURATION = 185

_SHORTS_HASHTAG_RE = re.compile(r"(?<!\w)#shorts?(?!\w)", re.IGNORECASE)


def is_short(platform: str | None, url: str | None, title: str | None,
             duration: int | None, from_shorts_tab: bool = False) -> bool:
    """The one Shorts rule, used by the scan, the reclassify pass and detect & clean.

    Only YouTube has Shorts. There: the Shorts tab, a /shorts/ URL, a duration of
    60 s or less, or a #short / #shorts hashtag as a whole word (not #shortfilm).
    Every other platform: never a short.
    """
    if (platform or "youtube") != "youtube":
        return False
    if from_shorts_tab:
        return True
    if url and "/shorts/" in url:
        return True
    if duration and duration <= SHORTS_MAX_DURATION:
        return True
    return bool(title and _SHORTS_HASHTAG_RE.search(title))

# Track channels currently being scanned to prevent concurrent scan races
_scanning_channels: set[int] = set()

# Channels whose files are being moved. Scans, RSS checks and downloads leave them alone.
_moving_channels: set[int] = set()

# True while a Move All runs. Two moves of the same files at once could race between one
# task's finished copy and the other's equal-size replace, so they're refused.
_move_all_running = False


# Channels with a file-touching operation in flight (renumber, rename, import, delete...).
_working_channels: set[int] = set()

# Channels whose whole-channel claim was refused only because a download is running:
# {channel_id: time.monotonic()}. The dispatcher starts nothing new for them.
_draining_channels: dict[int, float] = {}
DRAINING_MAX_SECONDS = 30 * 60
DRAINING_MESSAGE = (
    "Finishing this channel's current download first; new downloads for it are held. "
    "Try again in a few minutes."
)

# When each channel's last failed scan attempt happened (last_scanned_at is not
# stamped on failure); the Scan Now cooldown reads it.
_last_scan_attempt: dict[int, datetime] = {}


class ScanFailed(ChannelListingError):
    """The scan could not read the channel's video list; the failure is already recorded."""


def set_move_all_running(value: bool) -> None:
    global _move_all_running
    _move_all_running = value


def _flag_reason(channel_id: int, from_move_all: bool = False) -> str | None:
    """Busy reason from the in-memory flags alone (no await, so it can be claimed atomically)."""
    if _move_all_running and not from_move_all:
        return "Move All is running; try again when it finishes"
    if channel_id in _moving_channels:
        return "A move of this channel's files is running; try again when it finishes"
    if channel_id in _scanning_channels:
        return "A scan of this channel is running; try again when it finishes"
    if channel_id in _working_channels:
        return "Another operation on this channel is running; try again when it finishes"
    return None


async def _download_reason(
    db: AsyncSession, channel_id: int, video_ids: list[int] | None = None,
) -> str | None:
    """A running download in the channel, or only among ``video_ids`` (row ids) when given:
    an operation on a few videos shouldn't wait for an unrelated download to finish."""
    from app.services.download_service import _active_downloads

    query = select(Video.video_id, Video.status).where(
        Video.channel_id == channel_id,
        or_(Video.status == "downloading", Video.video_id.in_(list(_active_downloads) or [""])),
    )
    if video_ids is not None:
        query = query.where(Video.id.in_(video_ids))
    rows = (await db.execute(query.limit(1))).all()
    if rows:
        if video_ids is not None:
            return "That video is downloading; try again when it finishes"
        return "This channel has a download running; try again when it finishes"
    return None


def _claim_set(kind: str) -> set[int]:
    return {"move": _moving_channels, "scan": _scanning_channels, "work": _working_channels}[kind]


async def try_claim(
    db: AsyncSession, channel_id: int, kind: str, from_move_all: bool = False,
    video_ids: list[int] | None = None, drain: bool = False,
) -> str | None:
    """Claim a channel for an operation: None on success, else the busy reason.

    drain: only for a user-initiated whole-channel action that will be retried. A
    refusal for a running download then holds the channel's new downloads (see
    _draining_channels). Background callers (retention, Move All, library fixes) leave
    it off, since they don't come back soon.

    The flags are checked and the claim added with no await in between, so two
    concurrent requests can't both pass. The download check runs after the claim
    (ignoring it); on busy the claim is released. kind: "move", "scan" or "work".
    video_ids: the operation touches only these videos (row ids), so only their
    downloads count. The caller must release_claim() when the operation ends.
    """
    reason = _flag_reason(channel_id, from_move_all)
    if reason:
        return reason
    claim = _claim_set(kind)
    claim.add(channel_id)
    try:
        reason = await _download_reason(db, channel_id, video_ids)
    except BaseException:
        claim.discard(channel_id)
        raise
    if reason:
        claim.discard(channel_id)
        if drain and video_ids is None:
            # Refused only because a download is running: hold new downloads for this
            # channel so the running one is the last, and a retry can then claim it.
            _draining_channels[channel_id] = time.monotonic()
            return DRAINING_MESSAGE
        return reason
    _draining_channels.pop(channel_id, None)
    return reason


def draining_channel_ids() -> set[int]:
    """Channels held back from starting new downloads; entries older than 30 minutes expire."""
    now = time.monotonic()
    for cid, since in list(_draining_channels.items()):
        if now - since > DRAINING_MAX_SECONDS:
            del _draining_channels[cid]
    return set(_draining_channels)


def release_claim(channel_id: int, kind: str) -> None:
    _claim_set(kind).discard(channel_id)


@asynccontextmanager
async def claim_channel(
    db: AsyncSession, channel_id: int, kind: str = "work", video_ids: list[int] | None = None,
):
    """Hold a channel's claim for the whole operation; 409 when it is busy."""
    from fastapi import HTTPException

    reason = await try_claim(db, channel_id, kind, video_ids=video_ids, drain=True)
    if reason:
        raise HTTPException(status_code=409, detail=reason)
    try:
        yield
    finally:
        release_claim(channel_id, kind)


async def channel_busy(
    db: AsyncSession, channel_id: int, from_move_all: bool = False,
) -> str | None:
    """Short user-facing reason a channel's files must not be touched now, or None.

    Busy means: being moved (alone or by Move All), being scanned, working, or downloading.
    from_move_all: the caller is the Move All task itself, so its own flag doesn't count.
    """
    reason = _flag_reason(channel_id, from_move_all)
    if reason:
        return reason
    return await _download_reason(db, channel_id)


async def other_recorded(db: AsyncSession, channel_id: int) -> tuple[set[str], set[str]]:
    """Identity keys (path, device:inode) and sidecar-stem keys of every file another
    channel's video records in this channel's folder. Those files must not be moved or
    renamed for this channel.

    Runs at the end of every scan, so it costs nothing unless another channel's folder
    setting resolves to this channel's folder (only old installs have such shared
    folders); then only those channels' files are looked at.
    """
    from app.utils.file_utils import channel_folder_name, file_keys, stem_key

    channels = (await db.execute(select(Channel))).scalars().all()
    me = next((c for c in channels if c.id == channel_id), None)
    if me is None:
        return set(), set()

    def _sharing() -> list[int]:
        mine = _folder_key(me.download_dir, channel_folder_name(me))
        return [c.id for c in channels if c.id != channel_id
                and _folder_key(c.download_dir, channel_folder_name(c)) == mine]

    sharing = await asyncio.to_thread(_sharing)
    if not sharing:
        return set(), set()
    paths = (await db.execute(
        select(Video.file_path).where(
            Video.channel_id.in_(sharing), Video.file_path.isnot(None), Video.file_path != ""
        )
    )).scalars().all()

    def _build() -> tuple[set[str], set[str]]:
        keys: set[str] = set()
        stems: set[str] = set()
        for p in paths:
            keys |= file_keys(p)
            stems.add(stem_key(p))
        return keys, stems

    return await asyncio.to_thread(_build)

# Placeholder titles yt-dlp returns for videos that can't be downloaded (private,
# deleted, or otherwise unavailable). These appear as real entries in flat playlist
# extraction and must be skipped so they don't get stored as videos.
_UNAVAILABLE_TITLES = {"[private video]", "[deleted video]", "[unavailable video]"}
_UNAVAILABLE_AVAILABILITY = {"private", "deleted"}


def _date_from_timestamp(ts) -> date | None:
    """UTC date of a Unix timestamp, matching how yt-dlp derives upload_date."""
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(ts, timezone.utc).date()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


DATE_LOOKUP_MAX_FAILURES = 3
DATE_LOOKUP_RETRY_AFTER = timedelta(days=7)
DATE_LOOKUP_GIVEN_UP_RETRY_AFTER = timedelta(days=30)
DATE_LOOKUP_KEY_PREFIX = "date_lookup_failures:"

# Only failures about the video itself count toward giving up. Auth, bot, rate-limit,
# network and yt-dlp errors say nothing about this video and clear up on their own.
_DATE_FAILURE_COUNTED_CODES = frozenset({
    ErrorCode.UNKNOWN,
    ErrorCode.FORMAT_UNAVAILABLE,
    ErrorCode.AGE_RESTRICTED,
})


def _date_lookup_blocked(record: dict | None, now: datetime) -> bool:
    """True when this video's date lookup should be skipped (deferred) this scan.

    The first failure is retried on the next scan (one blip is common); after two,
    retries wait a week; after three the video is given up on and retried every 30 days."""
    if not record:
        return False
    failures = int(record.get("n", 0))
    if failures < 2:
        return False
    try:
        last = datetime.fromisoformat(record["t"])
    except (KeyError, TypeError, ValueError):
        return False
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    wait = (DATE_LOOKUP_GIVEN_UP_RETRY_AFTER if failures >= DATE_LOOKUP_MAX_FAILURES
            else DATE_LOOKUP_RETRY_AFTER)
    return now - last < wait


async def clear_date_lookup_failures(db: AsyncSession) -> None:
    """Forget every channel's failed date lookups (Force Rescan, fresh cookies).
    Does not commit; the caller does."""
    from sqlalchemy import delete
    await db.execute(
        delete(AppSetting).where(AppSetting.key.like(DATE_LOOKUP_KEY_PREFIX + "%"))
    )


def is_unavailable_title(title: str | None) -> bool:
    """Return True if a title is one of yt-dlp's unavailable-video placeholders."""
    return (title or "").strip().lower() in _UNAVAILABLE_TITLES


def _is_unavailable_entry(entry: dict) -> bool:
    """Return True if a playlist entry is for a private/deleted/unavailable video."""
    if is_unavailable_title(entry.get("title")):
        return True
    availability = (entry.get("availability") or "").strip().lower()
    return availability in _UNAVAILABLE_AVAILABILITY


_ADD_CHANNEL_LOCK = asyncio.Lock()
_MAX_FOLDER_ATTEMPTS = 1000


def _folder_key(base_dir: str | None, folder: str) -> str:
    return os.path.realpath(os.path.join(base_dir or settings.DOWNLOAD_DIR, folder)).casefold()


async def pick_channel_folder(
    db: AsyncSession, channel_name: str, platform: str, download_dir: str | None,
) -> str | None:
    """Choose the folder name for a channel being added.

    Returns None-equivalent behaviour (plain sanitized name) unless another channel
    already resolves to that same folder under the same root; then the platform is
    added: "Name (YouTube)", then "Name (YouTube 2)" and so on.
    """
    taken = await taken_folder_keys(db)
    return pick_free_folder(taken, channel_name, platform, download_dir)


def pick_free_folder(
    taken: set[str], channel_name: str, platform: str, download_dir: str | None,
) -> str:
    """First of "Name", "Name (Platform)", "Name (Platform 2)"... not in taken."""
    from app.utils.platform_utils import get_platform_label

    plain = sanitize_filename(channel_name)
    if _folder_key(download_dir, plain) not in taken:
        return plain
    label = get_platform_label(platform)
    for n in range(1, _MAX_FOLDER_ATTEMPTS + 1):
        suffix = f" ({label})" if n == 1 else f" ({label} {n})"
        # Trim the name, not the suffix, so every candidate stays distinct
        candidate = sanitize_filename(plain[: 200 - len(suffix)].rstrip(". ") + suffix)
        if _folder_key(download_dir, candidate) not in taken:
            return candidate
    raise ValueError(f"Could not find a free folder name for '{channel_name}'")


async def taken_folder_keys(db: AsyncSession, exclude_id: int | None = None) -> set[str]:
    """Folders other channels use: the ones on record, plus any folder holding a file
    another channel's video points to (e.g. after a move that failed part-way, before
    that channel's folder setting caught up)."""
    result = await db.execute(select(Channel))
    keys = {
        _folder_key(c.download_dir, channel_folder_name(c))
        for c in result.scalars().all() if c.id != exclude_id
    }
    q = select(Video.file_path).where(Video.file_path.is_not(None), Video.file_path != "")
    if exclude_id is not None:
        q = q.where(Video.channel_id != exclude_id)
    paths = (await db.execute(q)).scalars().all()

    def _ancestor_keys() -> set[str]:
        out: set[str] = set()
        for p in paths:
            d = os.path.dirname(p)
            for _ in range(3):  # season folder, channel folder, one more for nested templates
                parent = os.path.dirname(d)
                if not d or parent == d:
                    break
                out.add(os.path.realpath(d).casefold())
                d = parent
        return out

    return keys | await asyncio.to_thread(_ancestor_keys)


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
        try:
            info = await asyncio.to_thread(self.ytdlp.get_channel_info, data.url, platform)
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Could not find channel for: {data.url} ({e})")
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

        # Fallback: extract channel name from URL path when yt-dlp couldn't
        if channel_name == "Unknown" and platform != "youtube":
            from urllib.parse import urlparse
            path = urlparse(data.url).path.rstrip("/")
            slug = path.rsplit("/", 1)[-1] if "/" in path else path
            if slug and slug not in ("c", "user", "@"):
                channel_name = slug.lstrip("@")
                if ":" in channel_name and platform == "odysee":
                    channel_name = channel_name.split(":")[0]
                logger.info("Derived channel name from URL: %s", channel_name)

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
        # Rumble has no thumbnails list; use the banner scraped from the page.
        if not banner_url:
            banner_url = info.get("banner_url")

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
            thumbnail_url = channel_avatar_url(info)

        channel = Channel(
            channel_id=channel_id,
            channel_name=channel_name,
            channel_url=channel_url,
            platform=platform,
            thumbnail_url=thumbnail_url,
            banner_url=banner_url,
            description=info.get("description"),
            quality=data.quality,
            quality_cutoff=data.quality_cutoff,
            preferred_codec=data.preferred_codec,
            min_video_duration=data.min_video_duration,
            download_from_date=data.download_from_date,
            title_filter=data.title_filter,
            title_filter_is_regex=data.title_filter_is_regex,
            title_filter_mode=data.title_filter_mode,
            naming_template=data.naming_template,
            download_dir=data.download_dir,
            enabled=data.enabled,
            auto_download=data.auto_download,
            health_status="healthy",
            # Scan immediately on first tick so the user sees videos appear quickly
            next_scan_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )

        # Picking the folder and inserting must be one step, or two adds at the
        # same moment can both take the plain folder.
        async with _ADD_CHANNEL_LOCK:
            channel.folder_name = await pick_channel_folder(
                self.db, channel_name, platform, data.download_dir)
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
            thumbnail_url=thumbnail_url,
            base_dir=data.download_dir,
            platform=platform,
            folder_name=channel.folder_name,
        )

        logger.info("Added channel: %s (%s)", channel_name, channel_id)
        return channel

    async def refresh_channel_metadata(self, channel: Channel) -> Channel:
        """Re-fetch thumbnail, banner, and description from the platform."""
        # Prefer YouTube Data API for thumbnail (more reliable than yt-dlp tab extraction)
        api_ok = False
        if channel.platform == "youtube" and settings.has_youtube_api_key:
            try:
                from app.services.youtube_api_service import YouTubeAPIService
                api_svc = YouTubeAPIService()
                api_thumb = await api_svc.get_channel_thumbnail(channel.channel_id)
                if api_thumb:
                    channel.thumbnail_url = api_thumb
                    api_ok = True
            except Exception as e:
                logger.warning("Could not fetch thumbnail via API: %s", e)

        # Also try yt-dlp for banner and description, on the channel's own platform
        info = None
        info_error: Optional[Exception] = None
        try:
            info = await asyncio.to_thread(
                self.ytdlp.get_channel_info, channel.channel_url, getattr(channel, "platform", "youtube")
            )
        except Exception as e:
            info_error = e
        if not info and not api_ok:
            # Nothing was refreshed: say so instead of reporting success
            raise ValueError(f"Could not read the channel's metadata from {channel.platform}: "
                             f"{info_error or 'no data returned'}")

        if info:
            if not channel.thumbnail_url:
                channel.thumbnail_url = channel_avatar_url(info)
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

        if channel.thumbnail_url:
            try:
                await self._write_channel_nfo(channel)
            except Exception as e:
                logger.warning("Could not write poster for %s: %s", channel.channel_name, e)

        logger.info("Refreshed metadata for: %s", channel.channel_name)
        return channel

    async def _write_channel_nfo(self, channel: Channel) -> None:
        """Rewrite tvshow.nfo and download poster.jpg if the channel folder lacks one."""
        await asyncio.to_thread(
            write_tvshow_nfo,
            channel_name=channel.channel_name,
            channel_id=channel.channel_id,
            channel_url=channel.channel_url,
            description=channel.description,
            thumbnail_url=channel.thumbnail_url,
            base_dir=channel.download_dir,
            platform=channel.platform,
            folder_name=channel_folder_name(channel),
        )

    async def _backfill_channel_art(self, channel: Channel) -> None:
        """Fill in a missing avatar or poster.jpg. Retried every scan until both exist."""
        poster = os.path.join(
            channel.download_dir or settings.DOWNLOAD_DIR,
            channel_folder_name(channel), "poster.jpg",
        )
        if channel.thumbnail_url and os.path.exists(poster):
            return
        try:
            if channel.thumbnail_url:
                await self._write_channel_nfo(channel)
            else:
                # Channels added while yt-dlp returned no avatar; also writes the poster
                await self.refresh_channel_metadata(channel)
        except Exception as e:
            logger.warning("Could not backfill channel art for %s: %s", channel.channel_name, e)
            # A failed commit inside the refresh would otherwise break the rest of the scan
            await self.db.rollback()
            await self.db.refresh(channel)

    async def scan_channel(self, channel: Channel) -> int:
        """Scan a channel for new videos. Returns count of newly discovered videos."""
        from app.utils.platform_utils import supports_api, supports_rss, is_playlist_url

        # Prevent concurrent scans on the same channel
        if channel.id in _scanning_channels:
            logger.info("Skipping scan for %s - already in progress", channel.channel_name)
            return 0
        if channel.id in _moving_channels or channel.id in _working_channels:
            logger.info("Skipping scan for %s - its files are being moved or changed", channel.channel_name)
            return 0
        _scanning_channels.add(channel.id)
        try:
            return await self._scan_channel_inner(channel)
        finally:
            _scanning_channels.discard(channel.id)

    async def fetch_video_list(self, channel: Channel) -> list[dict]:
        """Fetch the channel's current video listing ([] when it can't be read)."""
        from app.utils.platform_utils import supports_api, is_playlist_url

        platform = getattr(channel, "platform", "youtube")
        is_playlist = is_playlist_url(channel.channel_url)
        # Try YouTube Data API first (only for channels that support it, not playlists), fall back to yt-dlp
        # yt-dlp path fetches all tabs (videos/shorts/streams) separately for better classification
        if self.yt_api and supports_api(platform) and not is_playlist:
            try:
                video_list = await self.yt_api.get_channel_videos(channel.channel_id)
                await self._tag_shorts_from_tab(channel, video_list, platform)
                return video_list
            except Exception as e:
                logger.warning("YouTube API failed for %s, falling back to yt-dlp: %s", channel.channel_name, e)
        # Playlists and non-API platforms always use yt-dlp
        return await asyncio.to_thread(
            self.ytdlp.get_channel_video_list_all_tabs, channel.channel_url, platform
        )

    async def _record_scan_failure(self, channel: Channel, error: Exception) -> None:
        """A listing that could not be read is a failed scan, not a healthy empty one.
        last_scanned_at stays as it was; next_scan_at still advances so a dead
        channel is retried on the normal schedule, not every tick."""
        logger.error("Scan failed for %s: could not read its video list: %s", channel.channel_name, error)
        channel_name = channel.channel_name
        channel_pk = channel.id
        first_failure = channel.last_error_code != ErrorCode.SCAN_FAILED.value
        if channel.last_error_code in _DEGRADED_OVERWRITABLE:
            channel.health_status = "unhealthy"
            channel.last_error_code = ErrorCode.SCAN_FAILED.value
        channel.next_scan_at = await self._compute_next_scan_at()
        await self.db.commit()
        _last_scan_attempt[channel_pk] = datetime.now(timezone.utc)
        message = f"Scan of {channel_name} failed: {error}"
        if first_failure:
            # Once per outage: a dead channel must not push a webhook every scan
            await NotificationService.broadcast("scan_failed", {
                "channel": channel_name,
                "message": message,
            })
        raise ScanFailed(message)

    async def _scan_channel_inner(self, channel: Channel, video_list: Optional[list[dict]] = None) -> int:
        """Internal scan implementation. A prefetched listing skips the fetch."""
        from app.utils.platform_utils import supports_api, supports_rss, is_playlist_url
        logger.info("Scanning channel: %s", channel.channel_name)

        # Batch-read all settings once at scan start (avoids per-video DB queries)
        import json
        self._settings_cache: dict = {}
        self._settings_cache_loaded = False
        try:
            result = await self.db.execute(
                select(AppSetting).where(~AppSetting.key.like(DATE_LOOKUP_KEY_PREFIX + "%"))
            )
            for s in result.scalars().all():
                try:
                    self._settings_cache[s.key] = json.loads(s.value)
                except (json.JSONDecodeError, TypeError):
                    self._settings_cache[s.key] = s.value
            self._settings_cache_loaded = True
        except Exception:
            # If this fails the cache is empty, which would make feature flags like
            # shorts_enabled default to False - and the reclassify pass deletes files
            # for excluded categories. Log it and let reclassify skip deletions.
            logger.exception(
                "Failed to load settings cache for %s; feature flags will use safe defaults",
                channel.channel_name,
            )

        platform = getattr(channel, "platform", "youtube")
        is_playlist = is_playlist_url(channel.channel_url)

        if platform == "youtube" and not is_playlist:
            await self._backfill_channel_art(channel)

        if video_list is None:
            try:
                video_list = await self.fetch_video_list(channel)
            except Exception as e:
                await self._record_scan_failure(channel, e)  # always raises ScanFailed

        failed_tabs = list(getattr(video_list, "failed_tabs", None) or [])
        if failed_tabs:
            logger.warning("Listing for %s is partial; these tabs failed: %s",
                           channel.channel_name, ", ".join(failed_tabs))

        logger.info("Video list returned %d entries for %s (url: %s)",
                    len(video_list) if video_list else 0, channel.channel_name, channel.channel_url)

        if not video_list:
            logger.warning("No videos found for channel: %s", channel.channel_name)
            stored = await self.db.scalar(
                select(func.count(Video.id)).where(Video.channel_id == channel.id)
            ) or 0
            channel.last_scanned_at = datetime.now(timezone.utc)
            channel.next_scan_at = await self._compute_next_scan_at()
            if stored > 0:
                # Videos we know about vanished from the listing: more likely a
                # broken listing than an emptied channel. Nothing is touched.
                logger.warning("%s has %d stored videos but its listing is empty",
                               channel.channel_name, stored)
                if channel.last_error_code in _DEGRADED_OVERWRITABLE:
                    channel.health_status = "warning"
                    channel.last_error_code = ErrorCode.METADATA_DEGRADED.value
            elif channel.last_error_code == ErrorCode.SCAN_FAILED.value:
                channel.last_error_code = None
                channel.health_status = "healthy"
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

        # Backfill source_url for existing videos missing it (fixes Odysee/Rumble URLs).
        # Entries whose URL the full-extraction fallback set to the page URL also
        # replace a stored URL: that path used to save the expiring media URL.
        if existing_ids:
            url_map = {}
            replace_ids = set()
            for entry in video_list:
                vid_id = entry.get("id") or entry.get("video_id", "")
                src = entry.get("url") or entry.get("webpage_url")
                if vid_id and src and vid_id in existing_ids:
                    url_map[vid_id] = src
                    if entry.get("_full_extraction"):
                        replace_ids.add(vid_id)
            if url_map:
                result = await self.db.execute(
                    select(Video).where(
                        Video.channel_id == channel.id,
                        Video.video_id.in_(url_map.keys()),
                        or_(Video.source_url.is_(None), Video.video_id.in_(replace_ids)),
                    )
                )
                for video in result.scalars().all():
                    if video.source_url != url_map[video.video_id]:
                        video.source_url = url_map[video.video_id]
                        logger.debug("Backfilled source_url for %s", video.video_id)
            # Release the write before the RSS and per-video network calls below: SQLite
            # has one writer, so an open write would stall downloads and the UI.
            await self.db.commit()

        # First pass: identify new video IDs
        new_entries = []
        skipped_unavailable = 0
        for entry in video_list:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if not vid_id or vid_id in existing_ids:
                continue
            if _is_unavailable_entry(entry):
                skipped_unavailable += 1
                continue
            new_entries.append(entry)

        if skipped_unavailable:
            logger.info("Skipped %d private/deleted/unavailable video(s) for %s",
                        skipped_unavailable, channel.channel_name)

        # Fetch upload dates from RSS feed (free, no auth, covers ~15 recent videos  - YouTube only)
        rss_dates = await asyncio.to_thread(
            self.ytdlp.get_rss_upload_dates, channel.channel_id, platform, is_playlist
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
        date_failures = await self._load_date_failures(channel)
        failures_changed = False
        counted_ids: set[str] = set()
        now_utc = datetime.now(timezone.utc)

        for entry in new_entries:
            vid_id = entry.get("id") or entry.get("video_id", "")
            upload_date = (
                self._parse_upload_date(entry.get("release_date"))
                or self._parse_upload_date(entry.get("upload_date"))
            )
            # Odysee listings carry only timestamps. Not used on YouTube, where a
            # listing's release_timestamp is an upcoming premiere's scheduled start.
            if not upload_date and platform != "youtube":
                upload_date = (
                    _date_from_timestamp(entry.get("release_timestamp"))
                    or _date_from_timestamp(entry.get("timestamp"))
                )
            title = entry.get("title", "Untitled")
            description = entry.get("description")
            duration = entry.get("duration")
            thumbnail = entry.get("thumbnail")
            source_tab = entry.get("_source_tab", "videos")
            live_status = entry.get("live_status")
            source_url = entry.get("url") or entry.get("webpage_url")

            date_unresolved_transient = False
            date_unresolved_permanent = False

            if not upload_date and vid_id in rss_dates:
                upload_date = self._parse_upload_date(rss_dates[vid_id])

            if not upload_date:
                if skip_metadata_fetch:
                    # The breaker already tripped this scan, so no fetch is attempted.
                    # We genuinely don't know this video's date - don't invent one.
                    date_unresolved_transient = True
                elif _date_lookup_blocked(date_failures.get(vid_id), now_utc):
                    # Failed repeatedly: not fetched every scan (weekly, then monthly),
                    # but still deferred so the warning stays visible.
                    date_unresolved_transient = True
                else:
                    logger.info("Fetching metadata for %s to get upload date", vid_id)
                    full_info, fetch_error = await asyncio.to_thread(
                        self.ytdlp.get_video_info_or_error, vid_id, platform, source_url
                    )
                    if full_info:
                        # Reachable: whatever else happened, this isn't a block. Reset the
                        # breaker so a source that simply doesn't publish dates can't trip it.
                        consecutive_metadata_failures = 0
                        fetched_date = (
                            self._parse_upload_date(full_info.get("release_date"))
                            or self._parse_upload_date(full_info.get("upload_date"))
                        )
                        if fetched_date:
                            upload_date = fetched_date
                        # No date on a successful fetch means the source doesn't publish
                        # one. That is not transient, so the today-fallback below applies.
                        title = full_info.get("title") or title
                        description = full_info.get("description") or description
                        duration = full_info.get("duration") or duration
                        thumbnail = full_info.get("thumbnail") or thumbnail
                        live_status = full_info.get("live_status") or live_status
                    else:
                        fetch_code = classify_error(fetch_error or "")
                        if fetch_code in _NON_TRANSIENT_FETCH_CODES:
                            # This video cannot be fetched by anyone (private, removed,
                            # geo-blocked, premiere not started). It is not a block, so it
                            # must not trip the breaker or be deferred. It stays eligible for
                            # the Data API date lookup below; if that can't date it either,
                            # it is dropped from this scan.
                            logger.info("Skipping %s: %s", vid_id, fetch_code.value)
                            date_unresolved_permanent = True
                        else:
                            consecutive_metadata_failures += 1
                            date_unresolved_transient = True
                            if fetch_code in _DATE_FAILURE_COUNTED_CODES and vid_id not in counted_ids:
                                counted_ids.add(vid_id)  # a repeated listing entry counts once
                                prev = date_failures.get(vid_id) or {}
                                date_failures[vid_id] = {
                                    "n": int(prev.get("n", 0)) + 1, "t": now_utc.isoformat(),
                                }
                                failures_changed = True

                    if consecutive_metadata_failures >= max_consecutive_failures:
                        logger.warning(
                            "Skipping per-video metadata fetch after %d consecutive failures "
                            "(likely bot detection). Remaining videos without a known date "
                            "will be deferred to the next scan.",
                            consecutive_metadata_failures,
                        )
                        skip_metadata_fetch = True

            enriched_entries.append({
                "vid_id": vid_id, "title": title, "description": description,
                "upload_date": upload_date, "duration": duration, "thumbnail": thumbnail,
                "source_tab": source_tab, "live_status": live_status,
                "source_url": source_url,
                "date_unresolved_transient": date_unresolved_transient,
                "date_unresolved_permanent": date_unresolved_permanent,
            })

        # Batch-resolve missing dates via YouTube API if available
        missing_date_ids = [e["vid_id"] for e in enriched_entries if e["upload_date"] is None]
        if missing_date_ids and self.yt_api and platform == "youtube":
            logger.info("Batch-fetching %d missing upload dates via YouTube API", len(missing_date_ids))
            api_dates = await self.yt_api.get_video_dates(missing_date_ids)
            for entry in enriched_entries:
                if entry["upload_date"] is None and entry["vid_id"] in api_dates:
                    raw = api_dates[entry["vid_id"]]
                    if raw:
                        entry["upload_date"] = self._parse_upload_date(raw)

        # Forget lookup failures for videos that now have a date or left the listing.
        listed_ids = {e["vid_id"] for e in enriched_entries if e["upload_date"] is None}
        for vid in [v for v in date_failures if v not in listed_ids]:
            del date_failures[vid]
            failures_changed = True
        if failures_changed:
            await self._save_date_failures(channel, date_failures)

        # Defer, don't fabricate. A video whose date is unknown because the metadata
        # fetch failed or was skipped (expired cookies / bot detection / network) is
        # left out of the database entirely. Stamping it with today's date would put
        # a years-old video in the current season with a current episode number, and
        # that corruption is permanent. Because it is never inserted, the next scan
        # sees it as new again and retries it with fresh metadata.
        deferred_entries = [
            e for e in enriched_entries
            if e["upload_date"] is None and e["date_unresolved_transient"]
        ]
        dropped_entries = [
            e for e in enriched_entries
            if e["upload_date"] is None and e["date_unresolved_permanent"]
        ]
        deferred_count = 0
        skipped_count = 0
        if deferred_entries or dropped_entries:
            enriched_entries = [
                e for e in enriched_entries
                if not (e["upload_date"] is None
                        and (e["date_unresolved_transient"] or e["date_unresolved_permanent"]))
            ]
            # A listing can repeat an entry; count distinct IDs, and don't count one
            # that another occurrence of the same video already resolved.
            kept_ids = {e["vid_id"] for e in enriched_entries}
            deferred_count = len({e["vid_id"] for e in deferred_entries} - kept_ids)
            skipped_count = len(
                {e["vid_id"] for e in dropped_entries} - kept_ids
                - {e["vid_id"] for e in deferred_entries}
            )

        if deferred_count:
            logger.warning(
                "Deferred %d video(s) for %s: upload date could not be determined "
                "(metadata fetch failed or was skipped). They are not saved with a "
                "placeholder date and will be retried on the next scan.",
                deferred_count, channel.channel_name,
            )

        if skipped_count:
            logger.info(
                "Skipped %d permanently unavailable video(s) for %s",
                skipped_count, channel.channel_name,
            )

        # Final fallback: assign today's date to anything still missing
        from datetime import date as date_cls
        today = date_cls.today()
        for entry in enriched_entries:
            if entry["upload_date"] is None:
                entry["upload_date"] = today
                logger.warning("Could not determine upload date for %s, defaulting to today", entry["vid_id"])

        # Sort by upload date (oldest first) so episode numbers are chronological
        enriched_entries.sort(key=lambda e: e["upload_date"])

        # Pre-compile title filter regex once (avoids recompiling per video)
        title_filter_re = None
        if channel.title_filter and channel.title_filter_is_regex:
            try:
                title_filter_re = re.compile(channel.title_filter, re.IGNORECASE)
            except re.error:
                logger.warning("Invalid regex in title filter for %s: %s",
                               channel.channel_name, channel.title_filter)

        # Third pass: assign episode numbers and insert into DB
        # Shorts are excluded from episode numbering
        new_count = 0
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
            is_livestream_entry = (source_tab == "streams")
            is_short_entry = False

            if not is_livestream_entry:
                is_short_entry = is_short(
                    getattr(channel, "platform", "youtube"), entry.get("source_url"),
                    title, duration, source_tab == "shorts",
                )
                if not is_short_entry and live_status in ("is_live", "was_live", "is_upcoming", "post_live"):
                    is_livestream_entry = True

            # Only assign episode numbers to regular videos (shorts/livestreams get 0)
            if is_short_entry or is_livestream_entry:
                episode = 0
            else:
                season_episode_counts.setdefault(season, 0)
                season_episode_counts[season] += 1
                episode = season_episode_counts[season]

            source_url = entry.get("source_url")

            video = Video(
                video_id=vid_id,
                source_url=source_url,
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
                    existing_video.retention_removed_at = None
                    existing_video.is_short = False
                    existing_video.file_path = None
                    existing_video.file_size = None
                    existing_video.quality_downloaded = None
                    existing_video.error_code = None
                    existing_video.error_message = None
                    existing_video.error_details = None
                    existing_video.retry_count = 0
                    existing_video.source_url = source_url
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
                # The savepoint rollback already detaches a pending instance, and `video`
                # may be an existing persistent row we reused rather than a new one.
                # expunge() on an object the session no longer holds raises
                # "Instance ... is not present in this Session", which aborted the whole
                # scan for the channel instead of just skipping this duplicate.
                if video in self.db:
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

            # Check per-channel start-date filter
            if channel.download_from_date and upload_date < channel.download_from_date:
                video.status = "skipped"
                video.monitored = False
                logger.info("Skipped old video: %s (%s) - %s before %s start date",
                            vid_id, title, upload_date, channel.download_from_date)
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

            # Check per-channel title filter (keyword or regex)
            if channel.title_filter and title:
                title_matched = False
                if channel.title_filter_is_regex:
                    if title_filter_re:
                        title_matched = bool(title_filter_re.search(title))
                else:
                    keywords = [k.strip() for k in channel.title_filter.split(",") if k.strip()]
                    title_lower = title.lower()
                    title_matched = any(kw.lower() in title_lower for kw in keywords)
                filter_mode = getattr(channel, "title_filter_mode", "include")
                should_skip = (not title_matched) if filter_mode == "include" else title_matched
                if should_skip:
                    video.status = "skipped"
                    video.monitored = False
                    logger.info("Skipped by title filter (%s): %s (%s)", filter_mode, vid_id, title)
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
                    await enqueue_video(self.db, video)
                else:
                    video.status = "pending"

            new_count += 1

        channel.last_scanned_at = datetime.now(timezone.utc)
        channel.next_scan_at = await self._compute_next_scan_at()
        channel.total_videos = len(existing_ids) + new_count
        if new_count > 0:
            channel.health_status = "healthy"
        if channel.last_error_code == ErrorCode.SCAN_FAILED.value and not failed_tabs:
            # The listing reads again: the earlier failure is over.
            channel.last_error_code = None
            channel.health_status = "healthy"
        if deferred_count > 0 or failed_tabs:
            # Videos were intentionally left out, so this scan is not "healthy" even
            # if other videos landed. Tell the user why and that it self-corrects, but
            # never paper over a more specific error (disk full, auth) already recorded.
            if channel.last_error_code in _DEGRADED_OVERWRITABLE:
                channel.health_status = "warning"
                channel.last_error_code = ErrorCode.METADATA_DEGRADED.value
        elif channel.last_error_code == ErrorCode.METADATA_DEGRADED.value:
            # A previous degraded scan has now resolved cleanly.
            channel.last_error_code = None
            channel.health_status = "healthy"

        await self.db.commit()
        logger.info("Found %d new videos for %s (%d deferred, %d skipped)",
                    new_count, channel.channel_name, deferred_count, skipped_count)

        # Phase B2: Correct fallback dates on existing videos via YouTube API
        dates_corrected = 0
        if self.yt_api and platform == "youtube":
            dates_corrected = await self._correct_fallback_dates(channel)
            if dates_corrected > 0:
                logger.info("Corrected %d fallback upload dates for %s", dates_corrected, channel.channel_name)

        # Phase C: Renumber episodes if Phase A reclassified anything or dates were corrected
        if reclassified_count > 0 or dates_corrected > 0:
            from app.utils.renumber import renumber_channel_episodes
            result = await self.db.execute(
                select(Video)
                .where(Video.channel_id == channel.id)
                .order_by(Video.upload_date.asc(), Video.id.asc())
            )
            all_videos = result.scalars().all()
            from app.services.naming_service import resolve_naming_template
            template = await resolve_naming_template(self.db, channel.naming_template)
            protected, _ = await other_recorded(self.db, channel.id)
            renamed = await asyncio.to_thread(
                renumber_channel_episodes, all_videos, channel, template, protected_keys=protected
            )
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

    @staticmethod
    def _date_failures_key(channel: Channel) -> str:
        return f"{DATE_LOOKUP_KEY_PREFIX}{channel.id}"

    async def _load_date_failures(self, channel: Channel) -> dict[str, dict]:
        """{video_id: {"n": failed lookups, "t": ISO time of the last one}} for this channel."""
        import json
        row = await self.db.get(AppSetting, self._date_failures_key(channel))
        if row is None:
            return {}
        try:
            data = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            return {}
        return data if isinstance(data, dict) else {}

    async def _save_date_failures(self, channel: Channel, failures: dict[str, dict]) -> None:
        import json
        key = self._date_failures_key(channel)
        row = await self.db.get(AppSetting, key)
        if not failures:
            if row is not None:
                await self.db.delete(row)
        elif row is None:
            self.db.add(AppSetting(key=key, value=json.dumps(failures)))
        else:
            row.value = json.dumps(failures)
        await self.db.commit()

    async def _correct_fallback_dates(self, channel: Channel) -> int:
        """Fix videos whose upload_date was set to their discovered_at date (fallback).

        This happens when yt-dlp couldn't determine the real date during scan.
        Uses the YouTube Data API to batch-resolve correct dates.
        """
        from datetime import date as date_cls

        result = await self.db.execute(
            select(Video).where(
                Video.channel_id == channel.id,
                func.date(Video.discovered_at) == Video.upload_date,
            )
        )
        suspect_videos = result.scalars().all()
        if not suspect_videos:
            return 0

        video_ids = [v.video_id for v in suspect_videos]
        logger.info("Found %d videos with suspected fallback dates, resolving via API", len(video_ids))
        api_dates = await self.yt_api.get_video_dates(video_ids)

        corrected = 0
        for video in suspect_videos:
            raw_date = api_dates.get(video.video_id)
            if not raw_date:
                continue
            parsed = self._parse_upload_date(raw_date)
            if parsed and parsed != video.upload_date:
                video.upload_date = parsed
                video.season = parsed.year
                corrected += 1

        if corrected > 0:
            await self.db.commit()

        return corrected

    async def resolve_fallback_dates(self, channel: Channel) -> tuple[list[dict], int, bool, bool]:
        """Preview-only resolver for a channel's upload-date fallback suspects.

        Finds videos whose upload_date still equals their discovered_at date (the
        stamped-today fallback) and re-resolves the real date without writing
        anything. Tries the free RSS feed first, then the YouTube Data API (if
        configured), then falls back to a per-video yt-dlp fetch behind the same
        3-strike breaker the scan uses. Returns (changes, checked, stopped_early,
        capped).

        Releases the session's read transaction with a rollback before any network
        call, so any uncommitted work the caller has pending is discarded. Call it
        on a clean session.
        """
        from datetime import date
        from app.utils.platform_utils import is_playlist_url

        max_suspects = 250
        result = await self.db.execute(
            select(Video).where(
                Video.channel_id == channel.id,
                func.date(Video.discovered_at) == Video.upload_date,
            ).order_by(Video.id.asc()).limit(max_suspects + 1)
        )
        rows = result.scalars().all()
        if not rows:
            return [], 0, False, False

        capped = len(rows) > max_suspects
        rows = rows[:max_suspects]

        # Snapshot everything the rest of this method needs before any network
        # call, then release the read transaction - the calls below can be slow
        # and there is no reason to hold a DB transaction open through them.
        snapshots = [
            {
                "id": v.id, "video_id": v.video_id, "title": v.title,
                "upload_date": v.upload_date, "season": v.season, "episode": v.episode,
                "file_path": v.file_path, "is_short": v.is_short, "is_livestream": v.is_livestream,
                "source_url": v.source_url,
            }
            for v in rows
        ]
        await self.db.rollback()
        await self.db.refresh(channel)

        platform = channel.platform
        is_playlist = is_playlist_url(channel.channel_url)

        changes: list[dict] = []
        checked = 0
        stopped_early = False

        def _record_change(snap: dict, new_date: date | None) -> None:
            if new_date and new_date != snap["upload_date"]:
                changes.append({
                    "video_id": snap["id"],
                    "source_id": snap["video_id"],
                    "title": snap["title"],
                    "old_date": str(snap["upload_date"]),
                    "new_date": str(new_date),
                    "old_episode": f"S{snap['season']}E{snap['episode']:03d}",
                    "has_file": bool(snap["file_path"] and os.path.exists(snap["file_path"])),
                })

        # 1. RSS feed (free, no auth, covers ~15 recent videos - YouTube only)
        rss_dates = await asyncio.to_thread(
            self.ytdlp.get_rss_upload_dates, channel.channel_id, platform, is_playlist
        )
        remaining = []
        for snap in snapshots:
            raw = rss_dates.get(snap["video_id"])
            new_date = self._parse_upload_date(raw) if raw else None
            if new_date:
                checked += 1
                _record_change(snap, new_date)
            else:
                remaining.append(snap)

        # 2. YouTube Data API batch lookup for whatever RSS didn't cover
        if remaining and self.yt_api and platform == "youtube":
            api_dates = await self.yt_api.get_video_dates([s["video_id"] for s in remaining])
            still_remaining = []
            for snap in remaining:
                raw = api_dates.get(snap["video_id"])
                new_date = self._parse_upload_date(raw) if raw else None
                if new_date:
                    checked += 1
                    _record_change(snap, new_date)
                else:
                    still_remaining.append(snap)
            remaining = still_remaining

        # 3. Per-video yt-dlp fallback for whatever is still undated, behind the
        # same breaker the scan uses - so an API quota failure degrades instead
        # of silently reporting no changes.
        consecutive_failures = 0
        for snap in remaining:
            info, err = await asyncio.to_thread(
                self.ytdlp.get_video_info_or_error, snap["video_id"], platform, snap["source_url"]
            )
            if info:
                consecutive_failures = 0
                checked += 1
                new_date = (
                    self._parse_upload_date(info.get("release_date"))
                    or self._parse_upload_date(info.get("upload_date"))
                )
                _record_change(snap, new_date)
            else:
                code = classify_error(err or "")
                if code in _NON_TRANSIENT_FETCH_CODES:
                    continue
                checked += 1
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    stopped_early = True
                    break

        return changes, checked, stopped_early, capped

    async def _tag_shorts_from_tab(self, channel: Channel, video_list: list[dict], platform: str) -> None:
        """Tag Data API entries that are on the channel's Shorts tab.

        The Data API doesn't say which tab a video is on, so without this the
        duration fallback misses Shorts over 60s (YouTube allows up to 3 minutes).
        Only videos too long for that fallback and short enough to be a Short are
        tagged, and the tab is only fetched when there are any.
        """
        candidates = [
            e for e in video_list
            if e.get("duration") and SHORTS_MAX_DURATION < e["duration"] <= SHORTS_TAB_MAX_DURATION
        ]
        if not candidates:
            return
        try:
            shorts = await asyncio.to_thread(
                self.ytdlp.get_channel_video_list, channel.channel_url, platform, "shorts"
            )
        except Exception as e:
            logger.warning("Could not read the Shorts tab for %s: %s", channel.channel_name, e)
            return
        short_ids = {e.get("id") for e in shorts if e.get("id")}
        tagged = 0
        for entry in candidates:
            if entry.get("id") in short_ids:
                entry["_source_tab"] = "shorts"
                tagged += 1
        if tagged:
            logger.info("Tagged %d Shorts from the Shorts tab for %s", tagged, channel.channel_name)

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

        # If the settings cache failed to load, the *_enabled flags below would
        # default to False and this pass would DELETE files for any video it flips to
        # short/livestream. Skip reclassification entirely rather than risk that.
        if hasattr(self, "_settings_cache_loaded") and not self._settings_cache_loaded:
            logger.warning(
                "Skipping reclassification for %s: settings did not load, cannot safely "
                "decide shorts/livestream deletions", channel.channel_name,
            )
            return 0

        # Build tab map from the fetched video list
        tab_map: dict[str, str] = {}
        live_status_map: dict[str, str | None] = {}
        for entry in video_list:
            vid_id = entry.get("id") or entry.get("video_id", "")
            if vid_id:
                tab_map[vid_id] = entry.get("_source_tab", "videos")
                live_status_map[vid_id] = entry.get("live_status")

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
            # URL and duration are None on purpose (see below): this pass can delete files.
            should_be_short = is_short(
                getattr(channel, "platform", "youtube"), None,
                None, None, source_tab == "shorts",
            )
            should_be_livestream = source_tab == "streams"

            # Secondary heuristics (only apply if not already classified by tab).
            # Deliberately NOT using a duration cutoff here: this path deletes the
            # downloaded files of anything it flips to a short, so it only acts on
            # reliable signals (the shorts tab above, an explicit #shorts title, or
            # live status) - never on duration alone, which would delete legitimate
            # short-but-regular videos.
            if not should_be_short and not should_be_livestream:
                if is_short(getattr(channel, "platform", "youtube"), None, video.title, None):
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
            await asyncio.to_thread(delete_video_files, video.file_path)

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
        from app.utils.scan_window import compute_next_scan_at

        start_hour = await self._scan_window_int("scan_window_start_hour", None)
        end_hour = await self._scan_window_int("scan_window_end_hour", None)
        min_offset_hours = await self._scan_window_int("scan_min_interval_hours", 12)

        return compute_next_scan_at(
            start_hour=start_hour,
            end_hour=end_hour,
            min_offset_hours=min_offset_hours,
        )

    async def _scan_window_int(self, key: str, default):
        """Read an int AppSetting. During a scan the full settings cache is
        authoritative (no query); outside a scan, fall back to a single DB read."""
        import json
        cache = getattr(self, "_settings_cache", None)
        if cache is not None:
            val = cache.get(key)
            try:
                return int(val) if val is not None else default
            except (TypeError, ValueError):
                return default
        try:
            result = await self.db.execute(select(AppSetting).where(AppSetting.key == key))
            setting = result.scalar_one_or_none()
            if setting:
                return int(json.loads(setting.value))
        except Exception as e:
            logger.warning("Failed to read scan-window setting '%s', using default %s: %s", key, default, e)
        return default

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
        except Exception as e:
            logger.warning("Failed to read setting '%s', using default %s: %s", key, default, e)
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

        # Resolve once: per-channel override, else the global default template.
        from app.services.naming_service import resolve_naming_template
        effective_template = await resolve_naming_template(self.db, channel.naming_template)

        protected, _ = await other_recorded(self.db, channel.id)

        renamed_count = 0
        for video in videos:
            old_path = Path(video.file_path)
            if not old_path.exists():
                continue
            if protected and protected & await asyncio.to_thread(file_keys, str(old_path)):
                continue  # another channel's video records this file too

            # Build expected path from current naming template
            expected_base = build_output_path(
                channel_name=channel.channel_name,
                video_title=video.title,
                video_id=video.video_id,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                naming_template=effective_template,
                base_dir=channel.download_dir,
                folder_name=channel_folder_name(channel),
            )
            expected_path = Path(expected_base + old_path.suffix)

            if old_path == expected_path:
                continue

            try:
                from app.utils.file_utils import move_video_files
                await asyncio.to_thread(move_video_files, str(old_path), str(expected_path))
                video.file_path = str(expected_path)
                renamed_count += 1
                logger.info("Renamed: %s -> %s", old_path, expected_path)

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
            try:
                await self.db.commit()
            except Exception:
                # The files are already moved to their new paths on disk, but the DB
                # still records the old paths. Surface enough context to fix it by hand.
                logger.error(
                    "Renamed %d file(s) on disk for %s but the DB update failed - the "
                    "database still records the OLD paths; manual correction needed",
                    renamed_count, channel.channel_name, exc_info=True,
                )
                raise
        return renamed_count

    async def _auto_import_existing(self, channel: Channel) -> int:
        """Check the channel's download directory for files that match un-downloaded videos."""
        base_dir = channel.download_dir or settings.DOWNLOAD_DIR
        channel_dir = Path(base_dir) / channel_folder_name(channel)

        if not channel_dir.is_dir():
            return 0

        # Scan all subdirectories (Season folders, root, etc.)
        dirs_to_scan = [channel_dir] + [d for d in channel_dir.iterdir() if d.is_dir()]

        total_imported = 0
        owned_paths = await load_owned_paths(self.db)
        for scan_dir in dirs_to_scan:
            try:
                matches = await scan_folder_for_imports(
                    self.db, channel.id, str(scan_dir), threshold=0.75,
                    exact_id_only=True, owned_paths=owned_paths,
                )
                if matches:
                    result = await import_matched_files(
                        self.db, channel.id, matches,
                    )
                    total_imported += result["imported"]
                    if result.get("errors"):
                        logger.warning(
                            "Auto-import in %s had %d error(s): %s",
                            scan_dir, len(result["errors"]), "; ".join(result["errors"]),
                        )
            except Exception as e:
                logger.warning("Auto-import scan of %s failed: %s", scan_dir, e)

        return total_imported

    async def delete_channel_files(self, channel: Channel):
        """Delete this channel's tracked video files and their sidecars.

        Folders are only removed once empty, and the channel's tvshow.nfo and
        artwork only when no other channel uses the same folder. Another
        channel's files (or anything untracked) are never touched.
        """
        from app.utils.file_utils import (
            delete_video_files, remove_channel_metadata, remove_empty_dirs,
        )

        base = channel.download_dir or settings.DOWNLOAD_DIR
        channel_dir = Path(base) / channel_folder_name(channel)

        result = await self.db.execute(
            select(Video.file_path).where(
                Video.channel_id == channel.id, Video.file_path.isnot(None)
            )
        )
        paths = [p for (p,) in result.all() if p]
        # A file another channel's video also records is not ours to delete
        if paths:
            used = await self.db.execute(
                select(Video.file_path).where(
                    Video.channel_id != channel.id, Video.file_path.in_(paths)
                )
            )
            claimed = {os.path.normpath(p) for (p,) in used.all()}
            paths = [p for p in paths if os.path.normpath(p) not in claimed]

        key = _folder_key(channel.download_dir, channel_folder_name(channel))
        others = await self.db.execute(select(Channel).where(Channel.id != channel.id))
        shared = any(
            _folder_key(o.download_dir, channel_folder_name(o)) == key
            for o in others.scalars().all()
        )

        def _work() -> None:
            for p in paths:
                delete_video_files(p)
            if not shared:
                remove_channel_metadata(str(channel_dir))
            # Folders that held the deleted files, deepest first, then the channel folder
            dirs = {os.path.dirname(p) for p in paths}
            for d in sorted(dirs, key=len, reverse=True):
                try:
                    if Path(d).resolve().is_relative_to(channel_dir.resolve()):
                        remove_empty_dirs(d)
                except OSError:
                    pass
            if not shared:
                remove_empty_dirs(str(channel_dir))

        await asyncio.to_thread(_work)
        logger.info("Deleted files for channel: %s at %s", channel.channel_name, channel_dir)

    @staticmethod
    def _parse_upload_date(date_str: str | None):
        from app.utils.file_utils import parse_upload_date
        return parse_upload_date(date_str)
