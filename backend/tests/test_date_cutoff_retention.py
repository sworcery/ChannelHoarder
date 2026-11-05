import asyncio
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.routers.settings import _import_from_date, _import_retention_days
from app.schemas import ChannelUpdate
from tests.test_upload_date_fallback import _run_scan


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


# --- Migration ---

def test_migration_copies_year_to_date_and_fresh_install(monkeypatch):
    async def run():
        # Upgrade: legacy table with download_from_year and no download_from_date
        eng = _engine()
        monkeypatch.setattr(database, "engine", eng)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            for i, cid in enumerate(("a", "b"), start=1):
                db.add(Channel(id=i, channel_id=cid, channel_name=cid, channel_url="u",
                               platform="youtube", health_status="unknown"))
            await db.commit()
        async with eng.begin() as conn:
            await conn.execute(text("ALTER TABLE channels DROP COLUMN download_from_date"))
            await conn.execute(text("ALTER TABLE channels DROP COLUMN retention_days"))
            await conn.execute(text("ALTER TABLE channels ADD COLUMN download_from_year INTEGER"))
            await conn.execute(text("UPDATE channels SET download_from_year = 2019 WHERE id = 1"))
        await database.init_database()
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT id, download_from_date FROM channels ORDER BY id"
            ))).all()
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(channels)"))).all()}
        await eng.dispose()
        assert rows[0][1] == "2019-01-01"
        assert rows[1][1] is None
        assert "retention_days" in cols

        # Fresh install: no year column at all
        eng2 = _engine()
        monkeypatch.setattr(database, "engine", eng2)
        await database.init_database()
        async with eng2.connect() as conn:
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(channels)"))).all()}
        await eng2.dispose()
        assert "download_from_date" in cols and "download_from_year" not in cols

    asyncio.run(run())


# --- Scan filter ---

def test_scan_skips_videos_before_start_date():
    entries = [
        {"id": "old1", "title": "Old", "release_date": "20230614"},
        {"id": "new1", "title": "New", "release_date": "20230615"},
    ]
    result = asyncio.run(_run_scan_with_status(entries, date(2023, 6, 15)))
    assert result == {"old1": "skipped", "new1": "pending"}


async def _run_scan_with_status(entries, from_date):
    from tests.test_upload_date_fallback import _make_channel_kwargs, _wire_service
    from app.services.channel_service import ChannelService

    eng = _engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as db:
            kwargs = _make_channel_kwargs("youtube", {"download_from_date": from_date})
            channel = Channel(**kwargs)
            db.add(channel)
            await db.commit()
            await db.refresh(channel)
            service = ChannelService(db)
            service.yt_api = None
            _wire_service(service, [], entries, {}, None, None)
            await service.scan_channel(channel)
            rows = (await db.execute(select(Video))).scalars().all()
            return {v.video_id: v.status for v in rows}
    finally:
        await eng.dispose()


# --- Retention ---

def test_retention_deletes_old_keeps_recent_and_other_channels(tmp_path, monkeypatch):
    async def run():
        from app.tasks import retention

        eng = _engine()
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(retention, "async_session", maker)

        now = datetime.now(timezone.utc)

        def mkfile(name):
            p = tmp_path / name
            p.write_bytes(b"x")
            return str(p)

        async with maker() as db:
            ch1 = Channel(channel_id="c1", channel_name="One", channel_url="u1",
                          platform="youtube", retention_days=30, health_status="unknown")
            ch2 = Channel(channel_id="c2", channel_name="Two", channel_url="u2",
                          platform="youtube", health_status="unknown")
            db.add_all([ch1, ch2])
            await db.flush()

            def vid(ch, vid_id, downloaded_at, status="completed"):
                return Video(
                    channel_id=ch.id, video_id=vid_id, title=vid_id, status=status,
                    upload_date=date(2024, 1, 1), season=2024, episode=1,
                    downloaded_at=downloaded_at, file_path=mkfile(vid_id + ".mp4"), file_size=1,
                )

            old = vid(ch1, "old", (now - timedelta(days=31)).replace(tzinfo=None))
            recent = vid(ch1, "recent", (now - timedelta(days=5)).replace(tzinfo=None))
            nodate = vid(ch1, "nodate", None)
            other = vid(ch2, "other", (now - timedelta(days=400)).replace(tzinfo=None))
            db.add_all([old, recent, nodate, other])
            await db.flush()
            db.add(DownloadQueue(video_id=old.id))
            await db.commit()

        await retention.apply_retention()

        async with maker() as db:
            vids = {v.video_id: v for v in (await db.execute(select(Video))).scalars().all()}
            queue = (await db.execute(select(DownloadQueue))).scalars().all()
        await eng.dispose()

        assert vids["old"].status == "skipped"
        assert vids["old"].monitored is False
        assert vids["old"].file_path is None and vids["old"].file_size is None
        assert not os.path.exists(tmp_path / "old.mp4")
        assert queue == []
        for name in ("recent", "nodate", "other"):
            assert vids[name].status == "completed"
            assert os.path.exists(tmp_path / f"{name}.mp4")

    asyncio.run(run())


# --- Import / schema ---

def test_import_legacy_year_and_new_date():
    assert _import_from_date({"download_from_year": 2020}) == date(2020, 1, 1)
    assert _import_from_date({"download_from_date": "2021-03-04"}) == date(2021, 3, 4)
    assert _import_from_date({"download_from_date": "2021-03-04", "download_from_year": 2010}) == date(2021, 3, 4)
    assert _import_from_date({"download_from_date": "garbage"}) is None
    assert _import_from_date({"download_from_year": "abc"}) is None
    assert _import_from_date({}) is None


def test_import_retention_days_and_update_validation():
    assert _import_retention_days(30) == 30
    assert _import_retention_days(0) is None
    assert _import_retention_days("x") is None
    assert _import_retention_days(None) is None
    assert ChannelUpdate(retention_days=None).retention_days is None
    with pytest.raises(ValidationError):
        ChannelUpdate(retention_days=0)
    with pytest.raises(ValidationError):
        ChannelUpdate(retention_days=36501)
