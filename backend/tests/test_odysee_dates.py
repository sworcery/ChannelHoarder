from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.ytdlp_service as ytdlp_module
from app.database import Base
from app.models import Channel, Video
from app.services.channel_service import ChannelService
from app.services.ytdlp_service import YtdlpService

# Shape of yt-dlp's flat Odysee channel listing: no date strings, only timestamps,
# and a url that carries the claim name the bare claim ID URL lacks.
RELEASE_TS = 1791417600  # 2026-10-08 00:00 UTC
UPLOAD_TS = 1791546989   # 2026-10-09 UTC


def _odysee_entry(claim_id, **kw):
    entry = {
        "id": claim_id, "title": f"Video {claim_id}",
        "url": f"https://odysee.com/video-{claim_id}:{claim_id}",
    }
    entry.update(kw)
    return entry


async def _scan(entries, video_info=None, platform="odysee", channel_url="https://odysee.com/@test:1"):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    fetches = []
    try:
        async with maker() as db:
            channel = Channel(
                channel_id="odysee_test", channel_name="Odysee Channel",
                channel_url=channel_url, platform=platform,
                auto_download=False, health_status="unknown",
            )
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            service = ChannelService(db)
            service.yt_api = None
            service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: [dict(e) for e in entries]
            service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {}
            service.ytdlp.get_channel_info = lambda url, platform="youtube": None

            def _fetch(vid_id, platform="youtube", url=None):
                fetches.append((vid_id, url))
                info = (video_info or {}).get(vid_id)
                return (info, None) if info else (None, "ERROR: Unsupported URL")

            service.ytdlp.get_video_info_or_error = _fetch

            async def _noop(*args, **kwargs):
                return 0

            service._auto_import_existing = _noop
            service._rename_existing_files = _noop

            await service.scan_channel(channel)
            rows = (await db.execute(
                select(Video).where(Video.channel_id == channel.id)
            )).scalars().all()
            return {v.video_id: v.upload_date for v in rows}, fetches
    finally:
        await engine.dispose()


class TestOdyseeListingDates:
    async def test_release_timestamp_dates_the_video_without_a_fetch(self):
        videos, fetches = await _scan([_odysee_entry("a1", release_timestamp=RELEASE_TS, timestamp=UPLOAD_TS)])
        assert videos == {"a1": date(2026, 10, 8)}
        assert fetches == []

    async def test_timestamp_used_when_no_release_timestamp(self):
        videos, fetches = await _scan([_odysee_entry("b2", timestamp=UPLOAD_TS)])
        assert videos == {"b2": date(2026, 10, 9)}
        assert fetches == []

    async def test_date_string_still_wins(self):
        videos, _ = await _scan([_odysee_entry("c3", upload_date="20260101", timestamp=UPLOAD_TS)])
        assert videos == {"c3": date(2026, 1, 1)}

    async def test_undated_entry_is_fetched_by_its_listing_url(self):
        videos, fetches = await _scan(
            [_odysee_entry("d4")],
            video_info={"d4": {"upload_date": "20260915"}},
        )
        assert fetches == [("d4", "https://odysee.com/video-d4:d4")]
        assert videos == {"d4": date(2026, 9, 15)}

    async def test_youtube_premiere_is_not_dated_from_its_schedule(self):
        # A YouTube listing's release_timestamp on an upcoming entry is the premiere's
        # scheduled start; the video must go through the normal fetch instead.
        entry = {
            "id": "prem1", "title": "Premiere", "live_status": "is_upcoming",
            "release_timestamp": RELEASE_TS, "url": "https://www.youtube.com/watch?v=prem1",
        }
        videos, fetches = await _scan(
            [entry], platform="youtube", channel_url="https://www.youtube.com/@test",
        )
        assert [vid for vid, _ in fetches] == ["prem1"]
        assert videos == {}


class TestRepairDatesOdysee:
    async def test_lookup_uses_the_stored_video_url(self):
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        today = date.today()
        try:
            async with maker() as db:
                channel = Channel(
                    channel_id="odysee_test", channel_name="Odysee Channel",
                    channel_url="https://odysee.com/@test:1", platform="odysee",
                    auto_download=False, health_status="healthy",
                )
                db.add(channel)
                await db.commit()
                await db.refresh(channel)
                # Saved before 1.9.43 with the scan date in place of the real one
                db.add(Video(
                    video_id="e5", channel_id=channel.id, title="Video e5",
                    upload_date=today, discovered_at=datetime(today.year, today.month, today.day, 9, 0),
                    season=today.year, episode=1, status="completed",
                    source_url="https://odysee.com/video-e5:e5",
                ))
                await db.commit()

                service = ChannelService(db)
                service.yt_api = None
                service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {}
                fetches = []

                def _fetch(vid_id, platform="youtube", url=None):
                    fetches.append((vid_id, url))
                    return {"upload_date": "20250102"}, None

                service.ytdlp.get_video_info_or_error = _fetch
                changes, checked, stopped_early, _ = await service.resolve_fallback_dates(channel)

                assert fetches == [("e5", "https://odysee.com/video-e5:e5")]
                assert [c["new_date"] for c in changes] == ["2025-01-02"]
                assert stopped_early is False
        finally:
            await engine.dispose()


class _FakeYDL:
    urls = []

    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        _FakeYDL.urls.append(url)
        return {"id": "x"}


class TestFetchUrl:
    @pytest.fixture(autouse=True)
    def fake_ydl(self, monkeypatch):
        _FakeYDL.urls = []
        monkeypatch.setattr(ytdlp_module.yt_dlp, "YoutubeDL", _FakeYDL)
        monkeypatch.setattr(ytdlp_module, "_curlcffi_cooling_down", lambda: False)

    def test_off_youtube_the_given_url_is_used(self):
        YtdlpService().get_video_info_or_error("abc", "odysee", "https://odysee.com/name:abc")
        assert _FakeYDL.urls == ["https://odysee.com/name:abc"]

    def test_off_youtube_without_a_url_falls_back_to_the_id(self):
        YtdlpService().get_video_info_or_error("abc", "odysee")
        assert _FakeYDL.urls == ["https://odysee.com/abc"]

    def test_youtube_always_uses_the_id(self):
        YtdlpService().get_video_info_or_error("dQw4w9WgXcQ", "youtube", "https://example.com/elsewhere")
        assert _FakeYDL.urls == ["https://www.youtube.com/watch?v=dQw4w9WgXcQ"]
