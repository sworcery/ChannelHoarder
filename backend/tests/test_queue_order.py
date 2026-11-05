import asyncio
import json
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.models import AppSetting, Channel, DownloadQueue, Video
from app.routers.downloads import get_queue, move_queue_item_to_top
from app.services.queue_order import queue_order_by


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _maker():
    eng = _engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _channel(db, cid, prioritize=False):
    ch = Channel(channel_id=cid, channel_name=cid, channel_url="u", platform="youtube",
                 health_status="unknown", prioritize_downloads=prioritize)
    db.add(ch)
    await db.flush()
    return ch


_t0 = datetime(2025, 1, 1)


async def _queued(db, ch, vid, upload, duration, n, priority=0):
    v = Video(channel_id=ch.id, video_id=vid, title=vid, status="queued",
              upload_date=upload, duration=duration, season=1, episode=1)
    db.add(v)
    await db.flush()
    q = DownloadQueue(video_id=v.id, priority=priority, queued_at=_t0 + timedelta(minutes=n))
    db.add(q)
    await db.flush()
    return q


async def _dispatch_order(db, mode):
    result = await db.execute(
        select(Video.video_id)
        .select_from(DownloadQueue)
        .join(Video, DownloadQueue.video_id == Video.id)
        .join(Channel, Video.channel_id == Channel.id)
        .order_by(*queue_order_by(mode))
    )
    return [r[0] for r in result.all()]


async def _list_order(db):
    out = await get_queue(skip=0, limit=50, search=None, db=db)
    return [i.video.video_id for i in out["items"]]


async def _seed(db):
    a = await _channel(db, "a")
    await _queued(db, a, "v1", date(2024, 1, 1), 300, 1)
    await _queued(db, a, "v2", date(2024, 3, 1), 100, 2)
    await _queued(db, a, "v3", date(2024, 2, 1), None, 3)
    await _queued(db, a, "v4", date(2024, 4, 1), 200, 4)
    await db.commit()
    return a


def test_each_mode_orders_and_nulls_last():
    async def run():
        eng, maker = await _maker()
        async with maker() as db:
            await _seed(db)
            assert await _dispatch_order(db, "queued") == ["v1", "v2", "v3", "v4"]
            assert await _dispatch_order(db, None) == ["v1", "v2", "v3", "v4"]
            assert await _dispatch_order(db, "bogus") == ["v1", "v2", "v3", "v4"]
            assert await _dispatch_order(db, "newest") == ["v4", "v2", "v3", "v1"]
            assert await _dispatch_order(db, "oldest") == ["v1", "v3", "v2", "v4"]
            assert await _dispatch_order(db, "shortest") == ["v2", "v4", "v1", "v3"]
            assert await _dispatch_order(db, "longest") == ["v1", "v4", "v2", "v3"]
        await eng.dispose()
    asyncio.run(run())


def test_priority_channel_beats_mode_and_manual_beats_both():
    async def run():
        eng, maker = await _maker()
        async with maker() as db:
            a = await _seed(db)
            b = await _channel(db, "b", prioritize=True)
            await _queued(db, b, "b1", date(2020, 1, 1), 999, 10)
            await _queued(db, b, "b2", date(2020, 1, 2), 998, 11)
            await db.commit()
            assert await _dispatch_order(db, "newest") == ["b2", "b1", "v4", "v2", "v3", "v1"]
            assert await _dispatch_order(db, "queued") == ["b1", "b2", "v1", "v2", "v3", "v4"]
            q = (await db.execute(select(DownloadQueue).join(Video).where(Video.video_id == "v3"))).scalar_one()
            q.priority = 5
            await db.commit()
            assert (await _dispatch_order(db, "newest"))[0] == "v3"
            assert await _list_order(db) == await _dispatch_order(db, "queued")
            db.add(AppSetting(key="download_queue_order", value=json.dumps("shortest")))
            await db.commit()
            order = await _list_order(db)
            assert order == await _dispatch_order(db, "shortest")
            assert order[0] == "v3" and order[1:3] == ["b2", "b1"]
        await eng.dispose()
    asyncio.run(run())


def test_list_shows_active_download_first():
    async def run():
        eng, maker = await _maker()
        async with maker() as db:
            await _seed(db)
            q = (await db.execute(select(DownloadQueue).join(Video).where(Video.video_id == "v1"))).scalar_one()
            q.started_at = _t0
            db.add(AppSetting(key="download_queue_order", value=json.dumps("newest")))
            await db.commit()
            assert await _list_order(db) == ["v1", "v4", "v2", "v3"]
        await eng.dispose()
    asyncio.run(run())


def test_list_search_does_not_double_join():
    async def run():
        eng, maker = await _maker()
        async with maker() as db:
            await _seed(db)
            out = await get_queue(skip=0, limit=50, search="v2", db=db)
            assert [i.video.video_id for i in out["items"]] == ["v2"]
            assert out["total"] == 1
        await eng.dispose()
    asyncio.run(run())


def test_move_to_top():
    async def run():
        eng, maker = await _maker()
        async with maker() as db:
            a = await _seed(db)
            q4 = (await db.execute(select(DownloadQueue).join(Video).where(Video.video_id == "v4"))).scalar_one()
            q1 = (await db.execute(select(DownloadQueue).join(Video).where(Video.video_id == "v1"))).scalar_one()
            q1.priority = 3
            await db.commit()
            res = await move_queue_item_to_top(q4.id, db)
            assert res["priority"] == 4
            assert (await _list_order(db))[0] == "v4"

            with pytest.raises(HTTPException) as e:
                await move_queue_item_to_top(9999, db)
            assert e.value.status_code == 404

            q1.started_at = datetime(2025, 1, 2)
            await db.commit()
            with pytest.raises(HTTPException) as e:
                await move_queue_item_to_top(q1.id, db)
            assert e.value.status_code == 409
        await eng.dispose()
    asyncio.run(run())


def test_migration_adds_prioritize_downloads(monkeypatch):
    async def run():
        eng = _engine()
        monkeypatch.setattr(database, "engine", eng)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE channels DROP COLUMN prioritize_downloads"))
            await conn.execute(text(
                "INSERT INTO channels (id, channel_id, channel_name, channel_url, platform, "
                "quality, title_filter_is_regex, title_filter_mode, enabled, include_shorts, "
                "include_livestreams, auto_download, total_videos, downloaded_count, health_status) "
                "VALUES (1, 'a', 'a', 'u', 'youtube', 'best', 0, 'include', 1, 0, 0, 1, 0, 0, 'unknown')"
            ))
        await database.init_database()
        async with eng.connect() as conn:
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(channels)"))).all()}
            val = (await conn.execute(text("SELECT prioritize_downloads FROM channels"))).scalar()
        await eng.dispose()
        assert "prioritize_downloads" in cols
        assert val == 0
    asyncio.run(run())
