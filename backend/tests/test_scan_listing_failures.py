"""A listing that fails must look failed, not healthy (no network: yt-dlp is mocked)."""
from datetime import date, datetime, timezone
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.routers.channels import refresh_metadata
from app.routers.channels import force_rescan, trigger_scan
from app.services.channel_service import (
    ChannelService, ScanFailed, _last_scan_attempt, _scanning_channels,
)
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import (
    ChannelListingError, VideoListing, YtdlpService,
)
from app.utils.platform_utils import get_channel_videos_url

LAST = datetime(2026, 1, 1, tzinfo=timezone.utc)


async def _maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    _scanning_channels.clear()

    async def _noop(*a, **k):
        return 0

    monkeypatch.setattr(ChannelService, "_backfill_channel_art", _noop)
    monkeypatch.setattr(ChannelService, "_auto_import_existing", _noop)
    monkeypatch.setattr(ChannelService, "_rename_existing_files", _noop)
    monkeypatch.setattr(YtdlpService, "get_rss_upload_dates", lambda self, *a, **k: {})
    yield
    _scanning_channels.clear()


async def _seed(db, tmp_path, platform="youtube", url="https://www.youtube.com/@x", with_video=True):
    ch = Channel(
        channel_id="UC_x", channel_name="Chan", channel_url=url, platform=platform,
        auto_download=False, health_status="healthy", download_dir=str(tmp_path),
        thumbnail_url="https://example.com/a.jpg", last_scanned_at=LAST.replace(tzinfo=None),
    )
    db.add(ch)
    await db.flush()
    if with_video:
        db.add(Video(channel_id=ch.id, video_id="old_video01", title="Old", status="downloaded",
                     upload_date=date(2026, 1, 1), season=2026, episode=1))
    await db.commit()
    return ch


def _listing(monkeypatch, result):
    async def fake(self, channel):
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(ChannelService, "fetch_video_list", fake)


async def _ids(db, channel_id):
    res = await db.execute(select(Video.video_id).where(Video.channel_id == channel_id))
    return set(res.scalars().all())


def _entry(vid):
    return {"id": vid, "title": f"Video {vid}", "upload_date": "20260901", "duration": 600}


async def test_extraction_failure_marks_scan_failed(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, ChannelListingError("videos tab: HTTP 403"))
            before_next = ch.next_scan_at
            with pytest.raises(ScanFailed):
                await ChannelService(db)._scan_channel_inner(ch)
            await db.refresh(ch)
            assert ch.last_scanned_at.replace(tzinfo=None) == LAST.replace(tzinfo=None)
            assert ch.last_error_code == "SCAN_FAILED"
            assert ch.health_status == "unhealthy"
            assert ch.next_scan_at is not None and ch.next_scan_at != before_next
            assert await _ids(db, ch.id) == {"old_video01"}
    finally:
        await eng.dispose()


async def test_partial_listing_scans_and_warns(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, VideoListing([_entry("new_video01")], failed_tabs=["shorts"]))
            assert await ChannelService(db)._scan_channel_inner(ch) == 1
            await db.refresh(ch)
            assert ch.health_status == "warning"
            assert ch.last_error_code == "METADATA_DEGRADED"
            # the old video was not in the partial list and is still there
            assert await _ids(db, ch.id) == {"old_video01", "new_video01"}
    finally:
        await eng.dispose()


async def test_empty_listing_with_stored_videos_warns(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, [])
            assert await ChannelService(db)._scan_channel_inner(ch) == 0
            await db.refresh(ch)
            assert ch.health_status == "warning"
            assert ch.last_error_code == "METADATA_DEGRADED"
            assert await _ids(db, ch.id) == {"old_video01"}
    finally:
        await eng.dispose()


async def test_empty_listing_without_videos_stays_quiet(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path, with_video=False)
            _listing(monkeypatch, [])
            await ChannelService(db)._scan_channel_inner(ch)
            await db.refresh(ch)
            assert ch.health_status == "healthy"
            assert ch.last_error_code is None
    finally:
        await eng.dispose()


async def test_recovery_clears_scan_failed(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            ch.health_status, ch.last_error_code = "unhealthy", "SCAN_FAILED"
            await db.commit()
            _listing(monkeypatch, [_entry("old_video01")])
            await ChannelService(db)._scan_channel_inner(ch)
            await db.refresh(ch)
            assert ch.health_status == "healthy"
            assert ch.last_error_code is None
    finally:
        await eng.dispose()


def _ydl(monkeypatch, behaviour):
    """Replace yt_dlp.YoutubeDL with a fake whose extract_info(url) is behaviour(url, opts)."""
    import app.services.ytdlp_service as mod

    class Fake:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return behaviour(url, self.opts)

    monkeypatch.setattr(mod.yt_dlp, "YoutubeDL", Fake)


def test_yt_dlp_none_result_raises(monkeypatch):
    _ydl(monkeypatch, lambda url, opts: None)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list("https://www.youtube.com/@x", "youtube", "videos")


def test_missing_shorts_tab_is_not_a_failure(monkeypatch):
    def behaviour(url, opts):
        if url.endswith("/shorts"):
            opts["logger"].error("ERROR: This channel does not have a shorts tab")
            return None
        return {"entries": [{"id": "abc"}]}

    _ydl(monkeypatch, behaviour)
    # shorts/streams absent: the listing is still complete
    result = YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")
    assert [e["id"] for e in result] == ["abc"]
    assert result.failed_tabs == []


def test_one_failed_tab_is_partial_and_all_failed_raises(monkeypatch):
    def partial(url, opts):
        if url.endswith("/shorts"):
            opts["logger"].error("ERROR: HTTP Error 429")
            return None
        return {"entries": [{"id": "abc"}]}

    _ydl(monkeypatch, partial)
    result = YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")
    assert [e["id"] for e in result] == ["abc"]
    assert result.failed_tabs == ["shorts"]

    _ydl(monkeypatch, lambda url, opts: None)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")


def test_channel_does_not_exist_on_all_tabs_raises(monkeypatch):
    def gone(url, opts):
        opts["logger"].error("ERROR: [youtube:tab] UCx: The channel/playlist does not exist and the URL is a redirect")
        return None

    _ydl(monkeypatch, gone)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")

    def gone2(url, opts):
        opts["logger"].error("ERROR: [youtube:tab] @x: This channel does not exist.")
        return None

    _ydl(monkeypatch, gone2)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")


def test_missing_videos_tab_does_not_warn(monkeypatch):
    def behaviour(url, opts):
        if url.endswith("/videos"):
            opts["logger"].error("ERROR: [youtube:tab] UCx: This channel does not have a videos tab")
            return None
        return {"entries": [{"id": "s1"}]} if url.endswith("/shorts") else {"entries": []}

    _ydl(monkeypatch, behaviour)
    result = YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")
    assert [e["id"] for e in result] == ["s1"]
    assert result.failed_tabs == []


def test_failed_tab_with_empty_merged_list_raises(monkeypatch):
    def behaviour(url, opts):
        if url.endswith("/videos"):
            opts["logger"].error("ERROR: HTTP Error 429")
            return None
        return {"entries": []}

    _ydl(monkeypatch, behaviour)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list_all_tabs("https://www.youtube.com/@x", "youtube")


def test_non_youtube_all_none_entries_fall_back_to_full(monkeypatch):
    def behaviour(url, opts):
        if opts.get("extract_flat"):
            return {"entries": [None, None]}
        return {"entries": [{"id": "v1", "webpage_url": "https://vimeo.com/1"}]}

    _ydl(monkeypatch, behaviour)
    result = YtdlpService().get_channel_video_list("https://vimeo.com/someuser", "vimeo")
    assert [e["id"] for e in result] == ["v1"]

    # ...and raises when the fallback also gets nothing
    _ydl(monkeypatch, lambda url, opts: {"entries": [None]} if opts.get("extract_flat") else None)
    with pytest.raises(ChannelListingError):
        YtdlpService().get_channel_video_list("https://vimeo.com/someuser", "vimeo")


async def test_scan_now_returns_502_on_failure(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _last_scan_attempt.pop(ch.id, None)
            ch.last_scanned_at = None
            await db.commit()
            _listing(monkeypatch, ChannelListingError("videos tab: gone"))
            with pytest.raises(HTTPException) as exc:
                await trigger_scan(ch.id, db)
            assert exc.value.status_code == 502
            assert "gone" in exc.value.detail
            # the failed attempt now counts toward the cooldown
            with pytest.raises(HTTPException) as exc:
                await trigger_scan(ch.id, db)
            assert exc.value.status_code == 429
    finally:
        _last_scan_attempt.clear()
        await eng.dispose()


async def test_scan_failed_broadcast_only_once(monkeypatch, tmp_path):
    sent = []

    async def fake_broadcast(cls, event, payload):
        sent.append(event)

    monkeypatch.setattr(NotificationService, "broadcast", classmethod(fake_broadcast))
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, ChannelListingError("videos tab: gone"))
            for _ in range(2):
                with pytest.raises(ScanFailed):
                    await ChannelService(db)._scan_channel_inner(ch)
            assert sent == ["scan_failed"]
    finally:
        await eng.dispose()


async def test_failure_keeps_a_more_specific_error_code(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            ch.last_error_code, ch.health_status = "DISK_FULL", "unhealthy"
            await db.commit()
            _listing(monkeypatch, ChannelListingError("x"))
            with pytest.raises(ScanFailed):
                await ChannelService(db)._scan_channel_inner(ch)
            await db.refresh(ch)
            assert ch.last_error_code == "DISK_FULL"
    finally:
        await eng.dispose()


async def test_force_rescan_refuses_partial_listing(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            db.add(Video(channel_id=ch.id, video_id="stuck_vid001", title="Stuck", status="pending",
                         upload_date=date(2026, 2, 1), season=2026, episode=2))
            await db.commit()
            _listing(monkeypatch, VideoListing([_entry("new_video01")], failed_tabs=["shorts"]))
            with pytest.raises(HTTPException) as exc:
                await force_rescan(ch.id, db)
            assert exc.value.status_code == 502
            assert await _ids(db, ch.id) == {"old_video01", "stuck_vid001"}
    finally:
        await eng.dispose()


@pytest.mark.parametrize("stored,expected", [
    ("https://vimeo.com/channels/staffpicks", "https://vimeo.com/channels/staffpicks"),
    ("https://vimeo.com/channels/staffpicks/videos", "https://vimeo.com/channels/staffpicks"),
    ("https://vimeo.com/showcase/123", "https://vimeo.com/showcase/123"),
    ("https://vimeo.com/showcase/123/videos", "https://vimeo.com/showcase/123"),
    ("https://vimeo.com/user/someuser", "https://vimeo.com/someuser"),
    ("https://vimeo.com/user12345", "https://vimeo.com/user12345"),
    ("https://vimeo.com/someuser", "https://vimeo.com/someuser"),
    ("https://vimeo.com/someuser/videos", "https://vimeo.com/someuser"),
])
def test_vimeo_urls_match_an_extractor(stored, expected):
    url = get_channel_videos_url("vimeo", stored)
    assert url == expected
    from yt_dlp.extractor import gen_extractor_classes
    matches = [e.IE_NAME for e in gen_extractor_classes() if e.suitable(url) and e.IE_NAME != "generic"]
    assert matches, f"no yt-dlp extractor matches {url}"


async def test_refresh_metadata_uses_platform_and_reports_failure(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path, platform="vimeo", url="https://vimeo.com/someuser")
            calls = []

            def boom(self, url, platform="youtube"):
                calls.append((url, platform))
                raise ValueError("yt-dlp could not extract channel info: blocked")

            monkeypatch.setattr(YtdlpService, "get_channel_info", boom)
            with pytest.raises(HTTPException) as exc:
                await refresh_metadata(ch.id, db)
            assert exc.value.status_code == 502
            assert "blocked" in exc.value.detail
            assert calls == [("https://vimeo.com/someuser", "vimeo")]

            def ok(self, url, platform="youtube"):
                calls.append((url, platform))
                return {"description": "hello", "thumbnails": []}

            monkeypatch.setattr(YtdlpService, "get_channel_info", ok)
            async def _nfo(self, channel):
                return None

            monkeypatch.setattr(ChannelService, "_write_channel_nfo", _nfo)
            out = await refresh_metadata(ch.id, db)
            assert "refreshed" in out["message"]
            assert calls[-1][1] == "vimeo"
    finally:
        await eng.dispose()
