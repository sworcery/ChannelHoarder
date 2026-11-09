"""Opt-in RSS check: scan a YouTube channel only when its RSS feed shows a
video we don't have. Adds scans on top of the interval scans; never replaces them."""

import asyncio
import logging
import math
import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import async_session
from app.models import Channel, Video
from app.services.channel_service import ChannelService, _scanning_channels
from app.services.settings_service import get_setting
from app.services.ytdlp_service import YtdlpService
from app.utils.platform_utils import is_playlist_url, supports_rss
from app.utils.scan_window import in_scan_window

logger = logging.getLogger(__name__)

# YouTube stops serving feeds to an IP after ~750 requests a day. Keep all feed
# requests (these checks plus the one every scan makes) under this.
_MAX_FEEDS_PER_DAY = 600
_PAUSE_MINUTES = 60
_BROKEN_FEED_RETRY = timedelta(hours=24)
_HANDLED_CAP = 200

_next_check: dict[int, datetime] = {}
_handled: dict[int, dict[str, None]] = {}  # insertion-ordered set per channel
_working: set[int] = set()  # channels whose feed has loaded since its last failure
_failures_in_a_row = 0
_paused_until: datetime | None = None
_last_minutes: int | None = None
_last_spacing: int | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _pause_between_feeds():
    await asyncio.sleep(random.uniform(1, 3))


def _fetch(channel_id: str, is_playlist: bool):
    return YtdlpService.fetch_rss_feed(channel_id, is_playlist)


async def _scan(channel_pk: int):
    """Run a normal scan with a fresh session."""
    async with async_session() as db:
        channel = await db.get(Channel, channel_pk)
        if channel:
            await ChannelService(db).scan_channel(channel)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


async def _read_settings() -> tuple[int, int | None, int | None, int]:
    async with async_session() as db:
        minutes = await get_setting(db, "rss_check_minutes", 0)
        start = await get_setting(db, "scan_window_start_hour", None)
        end = await get_setting(db, "scan_window_end_hour", None)
        interval_hours = await get_setting(db, "scan_min_interval_hours", 12)
    return (
        _as_int(minutes, 0),
        _as_int(start, None),
        _as_int(end, None),
        max(1, _as_int(interval_hours, 12)),
    )


def _spacing(minutes: int, n_channels: int, interval_hours: int) -> int:
    """Minutes between checks of one channel. Every interval scan also loads the
    channel's feed, so those requests come off the daily budget first; whatever is
    left still allows at least one check per channel a day."""
    scan_feeds = n_channels * 24 / interval_hours
    budget = max(_MAX_FEEDS_PER_DAY - scan_feeds, n_channels)
    return max(minutes, math.ceil(n_channels * 1440 / budget))


def _remember(channel_pk: int, ids) -> None:
    handled = _handled.setdefault(channel_pk, {})
    for i in ids:
        handled[i] = None
    while len(handled) > _HANDLED_CAP:
        del handled[next(iter(handled))]


async def rss_check_tick():
    global _paused_until, _last_minutes, _last_spacing, _failures_in_a_row
    minutes, start_hour, end_hour, interval_hours = await _read_settings()
    if minutes != _last_minutes:
        # Turned on, off or changed: forget the schedule so checks spread out again.
        _next_check.clear()
        _last_minutes = minutes
    if minutes <= 0:
        return
    now = _now()
    if _paused_until is not None:
        if now < _paused_until:
            return
        _paused_until = None
        _next_check.clear()  # spread checks out again rather than all at once
    # Like interval scans, RSS-triggered scans stay inside the scan window.
    if not in_scan_window(start_hour, end_hour):
        return

    async with async_session() as db:
        result = await db.execute(
            select(Channel.id, Channel.channel_id, Channel.channel_name, Channel.channel_url, Channel.platform)
            .where(Channel.enabled == True)  # noqa: E712
            .where(Channel.channel_id != "__standalone__")
        )
        channels = [r for r in result.all() if supports_rss(r[4] or "youtube")]
    if not channels:
        return

    spacing = _spacing(minutes, len(channels), interval_hours)
    if spacing > minutes and spacing != _last_spacing:
        logger.info("RSS check spacing raised from %d to %d minutes to stay under YouTube's daily feed limit "
                    "(%d channels)", minutes, spacing, len(channels))
    _last_spacing = spacing

    live = {c[0] for c in channels}
    for pk in list(_next_check):
        if pk not in live:
            _next_check.pop(pk, None)
            _handled.pop(pk, None)
            _working.discard(pk)
    for pk in live:
        if pk not in _next_check:
            _next_check[pk] = now + timedelta(seconds=random.uniform(0, spacing * 60))

    due = sorted((c for c in channels if _next_check[c[0]] <= now), key=lambda c: _next_check[c[0]])
    fetched = 0
    for pk, feed_id, name, url, _platform in due:
        if pk in _scanning_channels:
            continue  # a scan is reading this channel right now; check it next tick
        if fetched:
            await _pause_between_feeds()
        _next_check[pk] = _now() + timedelta(minutes=spacing)
        fetched += 1
        feed, reason = await asyncio.to_thread(_fetch, feed_id, is_playlist_url(url))
        if feed is None:
            _failures_in_a_row += 1
            # A feed that loaded before failing now, or two failures in a row, looks
            # like YouTube's per-IP limit: pause everything. A feed that has never
            # loaded is more likely broken for this channel alone, so only that
            # channel waits, and the others keep being checked.
            if pk in _working or _failures_in_a_row >= 2:
                # If it is this feed alone that died for good, its next failure
                # then gets the 24h retry instead of another pause.
                _working.discard(pk)
                _failures_in_a_row = 0
                _paused_until = _now() + timedelta(minutes=_PAUSE_MINUTES)
                logger.warning("YouTube RSS feeds are failing (%s), likely the daily per-IP limit; "
                               "pausing RSS checks for 60 minutes. Interval scans continue.", reason)
                return
            _next_check[pk] = _now() + _BROKEN_FEED_RETRY
            logger.info("RSS feed for %s failed (%s); trying it again in 24 hours", name, reason)
            continue
        _failures_in_a_row = 0
        _working.add(pk)

        async with async_session() as db:
            result = await db.execute(select(Video.video_id).where(Video.channel_id == pk))
            known = set(result.scalars().all())
        new_ids = [v for v in feed if v not in known and v not in _handled.get(pk, {})]
        if not new_ids:
            continue
        if pk in _scanning_channels:
            _next_check[pk] = _now()  # a scan started meanwhile; look again next tick
            continue
        logger.info("RSS check: %d new video(s) in feed for %s, scanning", len(new_ids), name)
        try:
            await _scan(pk)
        except Exception as e:
            logger.error("RSS-triggered scan failed for %s: %s", name, e)
        _remember(pk, new_ids)
