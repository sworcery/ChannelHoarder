import asyncio
import logging
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, Video
from app.services.channel_service import ChannelService
from app.utils.error_codes import ErrorCode, classify_error


class _FakeYtApi:
    """get_channel_videos must raise so the scan takes the yt-dlp listing path."""

    def __init__(self, dates=None):
        self.dates = dates or {}

    async def get_channel_videos(self, channel_id):
        raise RuntimeError("force yt-dlp listing path")

    async def get_video_dates(self, video_ids):
        return {vid: self.dates[vid] for vid in video_ids if vid in self.dates}


def _make_channel_kwargs(platform, seed_channel):
    kwargs = {
        "channel_id": "UC_test", "channel_name": "Test Channel",
        "channel_url": "https://www.youtube.com/@test", "platform": platform,
        "auto_download": False, "health_status": "unknown",
    }
    if seed_channel:
        kwargs.update(seed_channel)
    return kwargs


def _wire_service(service, calls, entries, video_info, video_errors, rss):
    service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: list(entries)
    service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: dict(rss or {})

    def _get_video_info_or_error(vid_id, platform="youtube"):
        calls.append(vid_id)
        info = video_info.get(vid_id)
        if info is not None:
            return info, None
        err = (video_errors or {}).get(vid_id, "Sign in to confirm you're not a bot")
        if isinstance(err, list):
            err = err.pop(0) if err else "Sign in to confirm you're not a bot"
        return None, err

    service.ytdlp.get_video_info_or_error = _get_video_info_or_error

    async def _noop(*args, **kwargs):
        return 0

    service._auto_import_existing = _noop
    service._rename_existing_files = _noop


async def _run_scan(entries, video_info, *, rss=None, yt_api=None, video_errors=None,
                     platform="youtube", seed_channel=None):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    calls = []
    try:
        async with maker() as db:
            channel = Channel(**_make_channel_kwargs(platform, seed_channel))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            service = ChannelService(db)
            service.yt_api = yt_api
            _wire_service(service, calls, entries, video_info, video_errors, rss)

            new_count = await service.scan_channel(channel)
            rows = (await db.execute(
                select(Video).where(Video.channel_id == channel.id)
                .order_by(Video.upload_date, Video.id)
            )).scalars().all()
            return {
                "new_count": new_count,
                "videos": [(v.video_id, v.upload_date, v.season, v.episode) for v in rows],
                "health": channel.health_status,
                "error_code": channel.last_error_code,
                "total_videos": channel.total_videos,
                "info_calls": calls,
            }
    finally:
        await engine.dispose()


def _scan(entries, video_info, **kw):
    return asyncio.run(_run_scan(entries, video_info, **kw))


async def _run_scans(rounds, *, yt_api=None, platform="youtube", seed_channel=None):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    results = []
    try:
        async with maker() as db:
            channel = Channel(**_make_channel_kwargs(platform, seed_channel))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            service = ChannelService(db)
            service.yt_api = yt_api

            for round_spec in rounds:
                calls = []
                _wire_service(
                    service, calls,
                    round_spec.get("entries", []),
                    round_spec.get("video_info", {}),
                    round_spec.get("video_errors"),
                    round_spec.get("rss"),
                )
                new_count = await service.scan_channel(channel)
                rows = (await db.execute(
                    select(Video).where(Video.channel_id == channel.id)
                    .order_by(Video.upload_date, Video.id)
                )).scalars().all()
                results.append({
                    "new_count": new_count,
                    "videos": [(v.video_id, v.upload_date, v.season, v.episode) for v in rows],
                    "health": channel.health_status,
                    "error_code": channel.last_error_code,
                    "info_calls": calls,
                })
    finally:
        await engine.dispose()
    return results


def _scans(rounds, **kw):
    return asyncio.run(_run_scans(rounds, **kw))


def _entry(vid, **kw):
    base = {"id": vid, "title": f"Video {vid}", "duration": 600,
            "_source_tab": "videos", "url": f"https://youtu.be/{vid}"}
    base.update(kw)
    return base


class TestTransientFailureDeferral:
    def test_transient_failures_are_deferred_not_inserted(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.services.channel_service")
        entries = [_entry("v1"), _entry("v2"), _entry("v3"), _entry("v4")]
        result = _scan(entries, {})
        assert result["videos"] == []
        assert result["new_count"] == 0
        assert result["total_videos"] == 0
        assert result["info_calls"] == ["v1", "v2", "v3"]
        assert result["health"] == "warning"
        assert result["error_code"] == "METADATA_DEGRADED"
        assert "Deferred 4 video(s)" in caplog.text

    def test_deferred_video_is_rediscovered_next_scan(self):
        rounds = _scans([
            {"entries": [_entry("v1")], "video_info": {}},
            {"entries": [_entry("v1")], "video_info": {"v1": {"upload_date": "20190314", "title": "Real"}}},
        ])
        assert rounds[0]["videos"] == []
        assert rounds[1]["videos"] == [("v1", date(2019, 3, 14), 2019, 1)]
        assert rounds[1]["info_calls"] == ["v1"]
        assert rounds[1]["health"] == "healthy"
        assert rounds[1]["error_code"] is None


class TestLegitimatelyDatelessSource:
    def test_successful_fetch_without_date_still_uses_today(self):
        entries = [_entry("a1"), _entry("a2"), _entry("a3")]
        video_info = {vid: {"title": f"Title {vid}"} for vid in ("a1", "a2", "a3")}
        result = _scan(entries, video_info)
        today = date.today()
        assert result["new_count"] == 3
        assert result["health"] == "healthy"
        assert result["error_code"] is None
        for vid_id, upload_date, season, episode in result["videos"]:
            assert upload_date == today
            assert season == today.year


class TestBreakerCountsOnlyRealFailures:
    def test_dateless_but_reachable_never_trips_breaker(self):
        vids = ["d1", "d2", "d3", "d4", "d5"]
        entries = [_entry(vid) for vid in vids]
        video_info = {vid: {"title": f"Title {vid}"} for vid in vids}
        result = _scan(entries, video_info)
        assert result["info_calls"] == vids
        assert result["new_count"] == 5

    def test_success_resets_the_failure_streak(self):
        vids = ["m1", "m2", "m3", "m4", "m5"]
        entries = [_entry(vid) for vid in vids]
        video_info = {"m3": {"upload_date": "20200101"}}
        result = _scan(entries, video_info)
        assert result["info_calls"] == vids
        assert result["videos"] == [("m3", date(2020, 1, 1), 2020, 1)]
        assert result["health"] == "warning"


class TestMixedScanOrdering:
    def test_dated_entries_still_number_correctly_alongside_deferrals(self):
        entries = [
            _entry("k1", upload_date="20200606"),
            _entry("t1"), _entry("t2"), _entry("t3"), _entry("t4"),
            _entry("k2", upload_date="20200101"),
            _entry("k3", upload_date="20210505"),
        ]
        result = _scan(entries, {})
        assert result["videos"] == [
            ("k2", date(2020, 1, 1), 2020, 1),
            ("k1", date(2020, 6, 6), 2020, 2),
            ("k3", date(2021, 5, 5), 2021, 1),
        ]
        assert result["new_count"] == 3
        today = date.today()
        assert all(v[1] != today for v in result["videos"])
        assert result["health"] == "warning"
        assert result["error_code"] == "METADATA_DEGRADED"


class TestApiRescue:
    def test_api_resolved_entry_is_inserted_not_deferred(self):
        entries = [_entry("r1"), _entry("r2")]
        result = _scan(entries, {}, yt_api=_FakeYtApi({"r1": "2018-07-04"}))
        assert result["videos"] == [("r1", date(2018, 7, 4), 2018, 1)]
        assert result["new_count"] == 1
        assert result["health"] == "warning"


class TestEdgeCases:
    def test_duplicate_listing_entry_is_counted_once(self, caplog):
        caplog.set_level(logging.WARNING, logger="app.services.channel_service")
        result = _scan([_entry("dup"), _entry("dup")], {})
        assert result["videos"] == []
        assert result["health"] == "warning"
        assert "Deferred 1 video(s)" in caplog.text

    def test_duplicate_where_one_occurrence_resolves_is_not_counted_deferred(self):
        entries = [_entry("dup"), _entry("dup", upload_date="20170301")]
        result = _scan(entries, {})
        assert result["videos"] == [("dup", date(2017, 3, 1), 2017, 1)]
        assert result["health"] == "healthy"
        assert result["error_code"] is None

    def test_duplicate_transient_and_permanent_counted_once(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.channel_service")
        entries = [_entry("dup"), _entry("dup")]
        video_errors = {"dup": ["Sign in to confirm you're not a bot", "Private video"]}
        result = _scan(entries, {}, video_errors=video_errors)
        assert result["videos"] == []
        assert "(1 deferred, 0 skipped)" in caplog.text


class TestPermanentFailures:
    def test_dead_videos_at_head_do_not_stall_channel(self):
        entries = [_entry("bad1"), _entry("bad2"), _entry("bad3"), _entry("good1"), _entry("good2")]
        video_errors = {
            "bad1": "Private video",
            "bad2": "This video has been removed by the uploader",
            "bad3": "Video unavailable",
        }
        video_info = {"good1": {"upload_date": "20200101"}, "good2": {"upload_date": "20200202"}}
        result = _scan(entries, video_info, video_errors=video_errors)
        assert result["info_calls"] == ["bad1", "bad2", "bad3", "good1", "good2"]
        assert result["videos"] == [
            ("good1", date(2020, 1, 1), 2020, 1),
            ("good2", date(2020, 2, 2), 2020, 2),
        ]
        assert result["new_count"] == 2
        assert result["health"] == "healthy"
        assert result["error_code"] is None

    def test_members_only_and_geo_and_premiere_are_skipped(self):
        entries = [_entry("m1"), _entry("g1"), _entry("p1")]
        video_errors = {
            "m1": "Join this channel to get access to members-only content",
            "g1": "The uploader has not made this video available in your country",
            "p1": "This live event will begin in 3 hours",
        }
        result = _scan(entries, {}, video_errors=video_errors, seed_channel={"health_status": "healthy"})
        assert result["videos"] == []
        assert result["health"] == "healthy"
        assert result["error_code"] is None
        assert result["info_calls"] == ["m1", "g1", "p1"]

    def test_transient_errors_still_defer(self):
        entries = [_entry("t1"), _entry("t2"), _entry("t3"), _entry("t4")]
        video_errors = {
            "t1": "Sign in to confirm you're not a bot",
            "t2": "HTTP Error 429: Too Many Requests",
            "t3": "Connection reset by peer",
            "t4": "curl_cffi network cooldown",
        }
        result = _scan(entries, {}, video_errors=video_errors)
        assert result["videos"] == []
        assert result["info_calls"] == ["t1", "t2", "t3"]
        assert result["health"] == "warning"
        assert result["error_code"] == "METADATA_DEGRADED"

    def test_permanent_failures_do_not_count_toward_breaker(self):
        entries = [_entry("p1"), _entry("t1"), _entry("p2"), _entry("t2"),
                   _entry("p3"), _entry("t3"), _entry("t4")]
        video_errors = {
            "p1": "Private video", "p2": "Private video", "p3": "Private video",
            "t1": "Sign in to confirm you're not a bot",
            "t2": "Sign in to confirm you're not a bot",
            "t3": "Sign in to confirm you're not a bot",
            "t4": "Sign in to confirm you're not a bot",
        }
        result = _scan(entries, {}, video_errors=video_errors)
        assert result["info_calls"] == ["p1", "t1", "p2", "t2", "p3", "t3"]
        assert "t4" not in result["info_calls"]
        assert result["videos"] == []
        assert result["health"] == "warning"

    def test_api_key_still_records_permanently_unfetchable_video(self):
        entries = [_entry("geo1"), _entry("good1")]
        video_errors = {"geo1": "The uploader has not made this video available in your country"}
        video_info = {"good1": {"upload_date": "20200101"}}
        result = _scan(entries, video_info, video_errors=video_errors,
                       yt_api=_FakeYtApi({"geo1": "2017-05-05"}))
        assert result["videos"] == [
            ("geo1", date(2017, 5, 5), 2017, 1),
            ("good1", date(2020, 1, 1), 2020, 1),
        ]
        assert result["new_count"] == 2
        assert result["health"] == "healthy"
        assert result["error_code"] is None

    def test_without_api_key_permanently_unfetchable_video_is_dropped_silently(self, caplog):
        caplog.set_level(logging.INFO, logger="app.services.channel_service")
        entries = [_entry("geo1"), _entry("good1")]
        video_errors = {"geo1": "The uploader has not made this video available in your country"}
        video_info = {"good1": {"upload_date": "20200101"}}
        result = _scan(entries, video_info, video_errors=video_errors, yt_api=None)
        assert result["videos"] == [("good1", date(2020, 1, 1), 2020, 1)]
        assert result["new_count"] == 1
        assert result["health"] == "healthy"
        assert result["error_code"] is None
        assert "Skipped 1 permanently unavailable video(s)" in caplog.text
        assert "Deferred" not in caplog.text


class TestClassifyError:
    def test_members_only_maps_to_private(self):
        assert classify_error("Join this channel to get access to members-only content like this video") is ErrorCode.VIDEO_PRIVATE
        assert classify_error("Private video") is ErrorCode.VIDEO_PRIVATE

    def test_geo_message_variant_maps_to_geo_blocked(self):
        assert classify_error("The uploader has not made this video available in your country") is ErrorCode.GEO_BLOCKED

    def test_bot_wall_maps_to_auth_expired(self):
        assert classify_error("Sign in to confirm you're not a bot") is ErrorCode.AUTH_EXPIRED

    def test_private_message_with_sign_in_text_still_maps_to_private(self):
        assert classify_error("Private video. Sign in if you've been granted access to this video") is ErrorCode.VIDEO_PRIVATE


class TestNonYouTube:
    def test_rumble_dateless_but_reachable_gets_today(self):
        vids = ["ru1", "ru2", "ru3", "ru4"]
        entries = [_entry(vid) for vid in vids]
        video_info = {vid: {"title": f"Title {vid}"} for vid in vids}
        result = _scan(entries, video_info, platform="rumble")
        today = date.today()
        assert result["info_calls"] == vids
        assert result["new_count"] == 4
        assert result["health"] == "healthy"
        for vid_id, upload_date, season, episode in result["videos"]:
            assert upload_date == today

    def test_rumble_fetch_failures_defer(self):
        vids = ["rf1", "rf2", "rf3", "rf4"]
        entries = [_entry(vid) for vid in vids]
        video_errors = {vid: "Connection reset by peer" for vid in vids}
        result = _scan(entries, {}, video_errors=video_errors, platform="rumble")
        assert result["info_calls"] == vids[:3]
        assert result["videos"] == []
        assert result["health"] == "warning"


class TestHealthPrecision:
    def test_foreign_error_code_survives_clean_scan(self):
        entries = [_entry("f1", upload_date="20200101"), _entry("f2", upload_date="20200202")]
        result = _scan(entries, {}, seed_channel={"last_error_code": "AUTH_EXPIRED", "health_status": "warning"})
        assert result["error_code"] == "AUTH_EXPIRED"

    def test_deferral_does_not_overwrite_disk_full(self):
        entries = [_entry("x1")]
        result = _scan(
            entries, {},
            seed_channel={"last_error_code": "DISK_FULL", "health_status": "unhealthy"},
        )
        assert result["error_code"] == "DISK_FULL"
        assert result["health"] == "unhealthy"

    def test_deferral_replaces_stale_scan_failed(self):
        entries = [_entry("x2")]
        result = _scan(
            entries, {},
            seed_channel={"last_error_code": "SCAN_FAILED", "health_status": "warning"},
        )
        assert result["error_code"] == "METADATA_DEGRADED"
        assert result["health"] == "warning"

    def test_deferral_overrides_stale_informational_code(self):
        entries = [_entry("x3")]
        result = _scan(
            entries, {},
            seed_channel={"last_error_code": "VIDEO_PRIVATE", "health_status": "healthy"},
        )
        assert result["error_code"] == "METADATA_DEGRADED"
        assert result["health"] == "warning"

    def test_deferral_still_does_not_override_auth_expired(self):
        entries = [_entry("x4")]
        result = _scan(
            entries, {},
            seed_channel={"last_error_code": "AUTH_EXPIRED", "health_status": "warning"},
        )
        assert result["error_code"] == "AUTH_EXPIRED"
