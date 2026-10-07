import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.services.channel_service import ChannelService

AVATAR = "https://yt3.googleusercontent.com/avatar=s900-c-k-c0x00ffffff-no-rj"


async def _make_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
def no_api_key(monkeypatch):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")


async def _add_channel(db, **kwargs):
    fields = {
        "channel_id": "UC_art", "channel_name": "Art Channel",
        "channel_url": "https://www.youtube.com/@art", "platform": "youtube",
        "auto_download": False, "health_status": "unknown",
    }
    fields.update(kwargs)
    channel = Channel(**fields)
    db.add(channel)
    await db.commit()
    await db.refresh(channel)
    return channel



class _FakeYtApi:
    def __init__(self, entries):
        self.entries = entries

    async def get_channel_videos(self, channel_id):
        return [dict(e) for e in self.entries]

    async def get_video_dates(self, video_ids):
        return {}


def _api_entry(vid, duration):
    return {"id": vid, "title": f"Video {vid}", "upload_date": "20260901", "duration": duration}


async def _api_scan(tmp_path, api_entries, shorts_tab_ids, existing=None):
    """Run one Data API scan with Shorts disabled. existing maps video_id -> has_file."""
    engine, maker = await _make_db()
    (tmp_path / "Shorts Channel").mkdir()
    (tmp_path / "Shorts Channel" / "poster.jpg").write_bytes(b"jpg")
    try:
        async with maker() as db:
            channel = await _add_channel(
                db, channel_id="UC_s", channel_name="Shorts Channel",
                channel_url="https://www.youtube.com/@shorts",
                thumbnail_url=AVATAR, download_dir=str(tmp_path),
            )
            files = {}
            for vid, has_file in (existing or {}).items():
                file_path = None
                if has_file:
                    file_path = str(tmp_path / f"{vid}.mp4")
                    open(file_path, "wb").close()
                    files[vid] = file_path
                db.add(Video(
                    video_id=vid, channel_id=channel.id, title=f"Video {vid}",
                    upload_date=date(2026, 8, 1), season=2026, episode=1,
                    status="completed" if has_file else "pending",
                    file_path=file_path, is_short=False,
                ))
            await db.commit()

            service = ChannelService(db)
            service.yt_api = _FakeYtApi(api_entries)
            tab_calls = []

            def _tab(url, platform="youtube", tab="videos"):
                tab_calls.append(tab)
                return [{"id": vid, "_source_tab": "shorts"} for vid in shorts_tab_ids]

            service.ytdlp.get_channel_video_list = _tab
            service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {}

            async def _noop(*args, **kwargs):
                return 0

            service._auto_import_existing = _noop
            service._rename_existing_files = _noop

            await service.scan_channel(channel)
            rows = (await db.execute(
                select(Video).where(Video.channel_id == channel.id)
            )).scalars().all()
            videos = {v.video_id: v for v in rows}
            return videos, tab_calls, files
    finally:
        await engine.dispose()


class TestApiScanShortsTab:
    async def test_long_short_on_shorts_tab_is_a_short(self, tmp_path):
        videos, tab_calls, _ = await _api_scan(
            tmp_path,
            [_api_entry("long_short", 150), _api_entry("regular", 150)],
            shorts_tab_ids=["long_short"],
        )
        assert tab_calls == ["shorts"]
        assert videos["long_short"].is_short is True
        assert videos["regular"].is_short is False

    async def test_tab_not_fetched_without_ambiguous_lengths(self, tmp_path):
        videos, tab_calls, _ = await _api_scan(
            tmp_path,
            [_api_entry("tiny", 30), _api_entry("long", 600)],
            shorts_tab_ids=["tiny", "long"],
        )
        assert tab_calls == []
        assert videos["tiny"].is_short is True
        assert videos["long"].is_short is False

    async def test_video_too_long_for_a_short_keeps_its_file(self, tmp_path):
        # A bad tab listing must never flip (and delete) a full-length video
        videos, _, files = await _api_scan(
            tmp_path,
            [_api_entry("candidate", 120), _api_entry("long", 600)],
            shorts_tab_ids=["long"],
            existing={"long": True},
        )
        assert videos["long"].is_short is False
        assert videos["long"].file_path == files["long"]
        assert os.path.exists(files["long"])

    async def test_empty_tab_deletes_nothing(self, tmp_path):
        videos, tab_calls, files = await _api_scan(
            tmp_path,
            [_api_entry("regular", 150)],
            shorts_tab_ids=[],
            existing={"regular": True},
        )
        assert tab_calls == ["shorts"]
        assert videos["regular"].is_short is False
        assert os.path.exists(files["regular"])

    async def test_downloaded_long_short_is_reclassified_and_cleaned(self, tmp_path):
        videos, _, files = await _api_scan(
            tmp_path,
            [_api_entry("old_long_short", 170), _api_entry("regular", 170)],
            shorts_tab_ids=["old_long_short"],
            existing={"old_long_short": True, "regular": True},
        )
        short = videos["old_long_short"]
        assert (short.is_short, short.episode, short.status, short.file_path) == (True, 0, "skipped", None)
        assert not os.path.exists(files["old_long_short"])
        assert videos["regular"].is_short is False
        assert os.path.exists(files["regular"])
