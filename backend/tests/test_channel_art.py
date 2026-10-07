from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.channel_service as channel_service_module
from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.schemas import ChannelCreate
from app.services.channel_service import ChannelService
from app.services.ytdlp_service import channel_avatar_url

AVATAR = "https://yt3.googleusercontent.com/avatar=s900-c-k-c0x00ffffff-no-rj"

# Shape of yt-dlp's YouTube channel info: no top-level 'thumbnail', wide banner
# entries and square avatar entries mixed in 'thumbnails'.
YOUTUBE_CHANNEL_INFO = {
    "id": "UC_art", "channel_id": "UC_art", "channel": "Art Channel",
    "channel_url": "https://www.youtube.com/channel/UC_art",
    "description": "About the channel",
    "thumbnails": [
        {"id": "0", "url": "https://yt3.googleusercontent.com/banner=w1060", "width": 1060, "height": 175},
        {"id": "5", "url": "https://yt3.googleusercontent.com/banner=w2560", "width": 2560, "height": 424},
        {"id": "banner_uncropped", "url": "https://yt3.googleusercontent.com/banner=s0"},
        {"id": "7", "url": AVATAR, "width": 900, "height": 900},
        {"id": "avatar_uncropped", "url": "https://yt3.googleusercontent.com/avatar=s0"},
    ],
}


class TestChannelAvatarUrl:
    def test_youtube_channel_picks_square_avatar(self):
        assert channel_avatar_url(YOUTUBE_CHANNEL_INFO) == AVATAR

    def test_top_level_thumbnail_wins(self):
        info = dict(YOUTUBE_CHANNEL_INFO, thumbnail="https://example.com/thumb.jpg")
        assert channel_avatar_url(info) == "https://example.com/thumb.jpg"

    def test_largest_square_wins(self):
        info = {"thumbnails": [
            {"url": "small", "width": 88, "height": 88},
            {"url": "big", "width": 900, "height": 900},
            {"url": "mid", "width": 240, "height": 240},
        ]}
        assert channel_avatar_url(info) == "big"

    def test_falls_back_to_avatar_uncropped(self):
        info = {"thumbnails": [
            {"id": "banner_uncropped", "url": "banner"},
            {"id": "avatar_uncropped", "url": "avatar"},
        ]}
        assert channel_avatar_url(info) == "avatar"

    def test_playlist_thumbnails_are_not_avatars(self):
        info = {"thumbnails": [{"url": "frame", "width": 1280, "height": 720}]}
        assert channel_avatar_url(info) is None

    @pytest.mark.parametrize("info", [None, {}, {"thumbnails": None}])
    def test_empty(self, info):
        assert channel_avatar_url(info) is None


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


@pytest.fixture
def nfo_calls(monkeypatch):
    calls = []
    monkeypatch.setattr(channel_service_module, "write_tvshow_nfo", lambda **kw: calls.append(kw))
    return calls


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


def _forbid_channel_info(url, platform="youtube"):
    raise AssertionError("channel info must not be fetched")


class TestChannelPoster:
    async def test_add_channel_writes_poster_from_avatar(self, nfo_calls):
        engine, maker = await _make_db()
        try:
            async with maker() as db:
                service = ChannelService(db)
                service.ytdlp.get_channel_info = lambda url, platform="youtube": dict(YOUTUBE_CHANNEL_INFO)

                channel = await service.add_channel(ChannelCreate(url="https://www.youtube.com/@art"))

                assert channel.thumbnail_url == AVATAR
                assert [c["thumbnail_url"] for c in nfo_calls] == [AVATAR]
        finally:
            await engine.dispose()

    async def test_scan_backfills_missing_avatar_and_poster(self, nfo_calls, tmp_path):
        engine, maker = await _make_db()
        try:
            async with maker() as db:
                channel = await _add_channel(db, download_dir=str(tmp_path))
                service = ChannelService(db)
                service.ytdlp.get_channel_info = lambda url, platform="youtube": dict(YOUTUBE_CHANNEL_INFO)
                service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: []

                await service.scan_channel(channel)

                assert channel.thumbnail_url == AVATAR
                assert [c["thumbnail_url"] for c in nfo_calls] == [AVATAR]
        finally:
            await engine.dispose()

    async def test_scan_writes_missing_poster_without_refetching(self, nfo_calls, tmp_path):
        engine, maker = await _make_db()
        try:
            async with maker() as db:
                channel = await _add_channel(db, download_dir=str(tmp_path), thumbnail_url=AVATAR)
                service = ChannelService(db)
                service.ytdlp.get_channel_info = _forbid_channel_info
                service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: []

                await service.scan_channel(channel)

                assert [c["thumbnail_url"] for c in nfo_calls] == [AVATAR]
        finally:
            await engine.dispose()

    async def test_scan_skips_backfill_when_art_present(self, nfo_calls, tmp_path):
        (tmp_path / "Art Channel").mkdir()
        (tmp_path / "Art Channel" / "poster.jpg").write_bytes(b"jpg")
        engine, maker = await _make_db()
        try:
            async with maker() as db:
                channel = await _add_channel(db, download_dir=str(tmp_path), thumbnail_url=AVATAR)
                service = ChannelService(db)
                service.ytdlp.get_channel_info = _forbid_channel_info
                service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: []

                await service.scan_channel(channel)

                assert nfo_calls == []
        finally:
            await engine.dispose()
