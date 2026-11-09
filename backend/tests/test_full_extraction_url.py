"""The non-YouTube full-extraction fallback must store page URLs, not media URLs."""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.ytdlp_service as ytdlp_module
from app.database import Base
from app.models import Channel, Video
from app.services.channel_service import ChannelService
from app.services.ytdlp_service import YtdlpService

PAGE = "https://vimeo.com/1001"
CDN = "https://vod-adaptive.akamaized.net/exp=1700000000~acl=/1001/video.mp4"


class _FakeYDL:
    def __init__(self, opts):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        return {"entries": [
            {"id": "1001", "title": "A", "url": CDN, "webpage_url": PAGE},
            {"id": "1002", "title": "B", "webpage_url": "https://vimeo.com/1002"},
            {"id": "1003", "title": "C", "url": CDN},
        ]}


def test_fallback_returns_page_urls(monkeypatch):
    monkeypatch.setattr(ytdlp_module.yt_dlp, "YoutubeDL", _FakeYDL)
    entries = YtdlpService()._get_channel_video_list_full("https://vimeo.com/someone", "vimeo", "videos")
    assert [e.get("url") for e in entries] == [PAGE, "https://vimeo.com/1002", CDN]
    # Only entries whose URL was replaced may overwrite a stored source_url.
    assert [bool(e.get("_full_extraction")) for e in entries] == [True, True, False]


async def _scan_existing(stored_url, entry):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db:
            channel = Channel(
                channel_id="vimeo_test", channel_name="Vimeo Channel",
                channel_url="https://vimeo.com/someone", platform="vimeo",
                auto_download=False, health_status="unknown",
            )
            db.add(channel)
            await db.flush()
            db.add(Video(channel_id=channel.id, video_id="1001", title="A", status="completed",
                         source_url=stored_url, upload_date=date(2025, 1, 1), season=1, episode=1))
            await db.commit()
            await db.refresh(channel)

            service = ChannelService(db)
            service.yt_api = None
            service.ytdlp.get_channel_video_list_all_tabs = lambda url, platform: [dict(entry)]
            service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {}
            service.ytdlp.get_channel_info = lambda url, platform="youtube": None
            service.ytdlp.get_video_info_or_error = lambda vid, platform="youtube", url=None: (None, "ERROR")
            await service.scan_channel(channel)

            return (await db.execute(select(Video.source_url).where(Video.video_id == "1001"))).scalar_one()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scan_replaces_a_stored_media_url():
    entry = {"id": "1001", "title": "A", "url": PAGE, "webpage_url": PAGE, "_full_extraction": True}
    assert await _scan_existing(CDN, entry) == PAGE


@pytest.mark.asyncio
async def test_flat_listing_does_not_replace_a_stored_url():
    entry = {"id": "1001", "title": "A", "url": "https://vimeo.com/1001?share=copy"}
    assert await _scan_existing(PAGE, entry) == PAGE
