import json
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.tasks.process_queue as pq
from app.database import Base
from app.models import AppSetting, Channel, DownloadQueue, Video
from app.routers import channels as channels_router
from app.routers import downloads
from app.services import channel_activity as ca
from app.services import queue_service


@pytest.fixture(autouse=True)
def clean_state():
    def _clear():
        for s in (ca.moving_channels, ca.scanning_channels, ca.exclusive_scans,
                  ca.working_channels, ca.draining_channels, ca.active_downloads,
                  ca.download_channels, ca.active_tasks):
            s.clear()
        ca.set_move_all_running(False)
    _clear()
    yield
    _clear()


async def _maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _channel(db, cid):
    ch = Channel(channel_id=cid, channel_name=cid, channel_url="u", platform="youtube",
                 health_status="unknown", quality="720p")
    db.add(ch)
    await db.flush()
    return ch


async def _queued(db, ch, vid, n, **row):
    v = Video(channel_id=ch.id, video_id=vid, title=vid, status="queued",
              upload_date=date(2024, 1, 1), duration=100, season=1, episode=1)
    db.add(v)
    await db.flush()
    q = DownloadQueue(video_id=v.id, queued_at=datetime(2025, 1, 1) + timedelta(minutes=n), **row)
    db.add(q)
    await db.flush()
    return q


# ── activity() ────────────────────────────────────────────────────────────

def test_activity_reports_each_state():
    idle = {"moving": False, "scanning": False, "exclusive": False,
            "working": False, "subtitles": False, "draining": False, "downloading": False}
    assert ca.activity(1) == idle
    ca.moving_channels.add(1)
    assert ca.activity(1) == {**idle, "moving": True}
    ca.moving_channels.clear()
    ca.scanning_channels.add(1)
    assert ca.activity(1) == {**idle, "scanning": True}
    ca.scanning_channels.clear()
    ca.exclusive_scans.add(1)
    assert ca.activity(1) == {**idle, "exclusive": True}
    ca.exclusive_scans.clear()
    ca.working_channels.add(1)
    assert ca.activity(1) == {**idle, "working": True}
    ca.working_channels.clear()
    ca.draining_channels[1] = ca.time.monotonic()
    assert ca.activity(1) == {**idle, "draining": True}
    ca.draining_channels.clear()
    ca.download_channels[10] = 1
    assert ca.activity(1) == {**idle, "downloading": True}
    assert ca.activity(2) == idle  # other channels are unaffected


def test_expired_draining_is_not_reported():
    ca.draining_channels[1] = ca.time.monotonic() - ca.DRAINING_MAX_SECONDS - 1
    assert ca.activity(1)["draining"] is False


def test_is_running_covers_downloads_and_tasks():
    assert not ca.is_running("v1")
    ca.active_downloads.add("v1")
    assert ca.is_running("v1")


async def test_channel_endpoints_carry_activity(tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            await db.commit()
            ca.exclusive_scans.add(ch.id)
            listed = await channels_router.list_channels(skip=0, limit=10, search=None, db=db)
            assert listed[0].activity["exclusive"] is True
            detail = await channels_router.get_channel(ch.id, db)
            assert detail.activity["exclusive"] is True and detail.activity["moving"] is False
    finally:
        await eng.dispose()


# ── dispatcher against the claim matrix ───────────────────────────────────

def _claim(kind, cid):
    if kind == "move":
        ca.moving_channels.add(cid)
    elif kind == "work":
        ca.working_channels.add(cid)
    elif kind == "exclusive":
        ca.exclusive_scans.add(cid)
    elif kind == "scan":
        ca.scanning_channels.add(cid)
    elif kind == "draining":
        ca.draining_channels[cid] = ca.time.monotonic()
    elif kind == "move_all":
        ca.set_move_all_running(True)  # Move All has not reached this channel yet


@pytest.mark.parametrize("kind,dispatched", [
    ("move", False), ("work", False), ("exclusive", False), ("draining", False),
    ("scan", True),       # a plain scan runs beside downloads
    ("move_all", True),   # Move All has not claimed this channel yet
    (None, True),
])
async def test_dispatcher_claim_matrix(monkeypatch, kind, dispatched):
    eng, maker = await _maker()
    started = []

    async def fake_download(video_id, channel_id, queue_id):
        started.append(channel_id)

    monkeypatch.setattr(pq, "_run_download", fake_download)
    monkeypatch.setattr(pq, "async_session", maker)
    try:
        async with maker() as db:
            claimed = await _channel(db, "claimed")
            other = await _channel(db, "other")
            await _queued(db, claimed, "c1", 1)
            await _queued(db, other, "o1", 2)
            db.add(AppSetting(key="max_concurrent_downloads", value=json.dumps(5)))
            await db.commit()
            if kind:
                _claim(kind, claimed.id)
            await pq.process_download_queue()
            import asyncio
            await asyncio.gather(*ca.active_tasks, return_exceptions=True)
            assert other.id in started  # the rest of the queue is never stalled
            assert (claimed.id in started) is dispatched
    finally:
        await eng.dispose()


# ── queue_service ─────────────────────────────────────────────────────────

async def test_download_now_on_waiting_row_clears_retry_after(monkeypatch):
    eng, maker = await _maker()
    started = []

    async def fake_download(video_id, channel_id, queue_id):
        started.append(video_id)

    monkeypatch.setattr(pq, "_run_download", fake_download)
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            q = await _queued(db, ch, "w1", 1, quality_attempts=1,
                              retry_after=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1))
            await db.commit()
            await downloads.download_now(q.id, db)
            import asyncio
            await asyncio.gather(*ca.active_tasks, return_exceptions=True)
            row = (await db.execute(select(DownloadQueue).where(DownloadQueue.id == q.id))).scalar_one()
            await db.refresh(row)
            assert row.retry_after is None
            assert row.started_at is not None
            assert row.target_quality == "720p"
            assert started
    finally:
        await eng.dispose()


async def test_start_refuses_a_row_already_running(monkeypatch):
    eng, maker = await _maker()

    async def fake_download(video_id, channel_id, queue_id):
        pass

    monkeypatch.setattr(pq, "_run_download", fake_download)
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            q = await _queued(db, ch, "r1", 1, started_at=datetime.now(timezone.utc))
            await db.commit()
            entry = (await db.execute(
                select(DownloadQueue).where(DownloadQueue.id == q.id)
            )).scalar_one()
            entry.video = await db.get(Video, entry.video_id)
            assert await queue_service.start(db, entry, ch, forced=True) is False
    finally:
        await eng.dispose()


async def test_remove_idle_resets_status_and_skips_running():
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            idle = await _queued(db, ch, "i1", 1)
            running = await _queued(db, ch, "r1", 2)
            await db.commit()
            ca.active_downloads.add("r1")
            ids = await queue_service.remove_idle(db)
            await db.commit()
            assert ids == [idle.video_id]
            left = (await db.execute(select(DownloadQueue.id))).scalars().all()
            assert left == [running.id]
    finally:
        await eng.dispose()


async def test_clear_quality_waits_only_touches_that_channel():
    eng, maker = await _maker()
    try:
        async with maker() as db:
            a = await _channel(db, "a")
            b = await _channel(db, "b")
            soon = datetime(2030, 1, 1)
            qa = await _queued(db, a, "a1", 1, quality_attempts=2, retry_after=soon)
            qb = await _queued(db, b, "b1", 2, quality_attempts=2, retry_after=soon)
            await db.commit()
            await queue_service.clear_quality_waits(db, a.id)
            await db.commit()
            rows = {r.id: r for r in (await db.execute(select(DownloadQueue))).scalars()}
            await db.refresh(rows[qa.id])
            assert rows[qa.id].retry_after is None and rows[qa.id].quality_attempts == 0
            assert rows[qb.id].retry_after == soon
    finally:
        await eng.dispose()


# ── download registration covers the whole task ──────────────────────────

@pytest.mark.parametrize("outcome", ["success", "failure", "cancelled"])
async def test_download_channels_set_during_task_and_cleared(monkeypatch, outcome):
    import asyncio
    eng, maker = await _maker()
    seen = []

    class FakeService:
        async def download_video(self, video_id, channel_id, queue_id):
            seen.append(dict(ca.download_channels))
            assert ca.activity(channel_id)["downloading"] is True
            if outcome == "failure":
                raise RuntimeError("boom")
            if outcome == "cancelled":
                raise asyncio.CancelledError()

    monkeypatch.setattr(pq, "DownloadService", FakeService)
    monkeypatch.setattr(pq, "async_session", maker)
    try:
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await pq._run_download(7, 3, 1)
        else:
            await pq._run_download(7, 3, 1)
        assert seen == [{7: 3}]
        assert ca.download_channels == {}
        assert ca.activity(3)["downloading"] is False
    finally:
        await eng.dispose()


async def test_claim_refused_while_task_registered_even_without_downloading_status():
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            q = await _queued(db, ch, "p1", 1)  # status "queued": not yet in yt-dlp
            await db.commit()
            ca.download_channels[q.video_id] = ch.id
            assert await ca.try_claim(db, ch.id, "work") is not None
            ca.download_channels.clear()
            assert await ca.try_claim(db, ch.id, "work") is None
    finally:
        await eng.dispose()


# ── start() races and Download Now refusals ──────────────────────────────

async def test_dispatcher_start_skips_row_download_now_started_meanwhile(monkeypatch):
    eng, maker = await _maker()
    started = []

    async def fake_download(video_id, channel_id, queue_id):
        started.append(video_id)

    monkeypatch.setattr(pq, "_run_download", fake_download)
    try:
        async with maker() as dispatcher_db, maker() as now_db:
            ch = await _channel(dispatcher_db, "c1")
            q = await _queued(dispatcher_db, ch, "d1", 1)
            await dispatcher_db.commit()
            # The dispatcher has picked the row (started_at still None in its view)...
            picked = (await dispatcher_db.execute(
                select(DownloadQueue).where(DownloadQueue.id == q.id))).scalar_one()
            picked.video = await dispatcher_db.get(Video, picked.video_id)
            # ...and Download Now starts it in the gap.
            await downloads.download_now(q.id, now_db)
            assert len(started) == 0  # task not run yet
            assert await queue_service.start(dispatcher_db, picked, ch) is False
            import asyncio
            await asyncio.gather(*ca.active_tasks, return_exceptions=True)
            assert len(started) == 1
    finally:
        await eng.dispose()


@pytest.mark.parametrize("kind,refused", [
    ("work", True), ("exclusive", True), ("move", True),
    ("draining", False), ("scan", False),
])
async def test_download_now_respects_claims(monkeypatch, kind, refused):
    from fastapi import HTTPException
    eng, maker = await _maker()

    async def fake_download(video_id, channel_id, queue_id):
        pass

    monkeypatch.setattr(pq, "_run_download", fake_download)
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            q = await _queued(db, ch, "n1", 1)
            await db.commit()
            _claim(kind, ch.id)
            if refused:
                with pytest.raises(HTTPException) as e:
                    await downloads.download_now(q.id, db)
                assert e.value.status_code == 409
                assert e.value.detail == ca.flag_reason(ch.id)
                row = (await db.execute(select(DownloadQueue).where(DownloadQueue.id == q.id))).scalar_one()
                assert row.started_at is None
            else:
                await downloads.download_now(q.id, db)
            import asyncio
            await asyncio.gather(*ca.active_tasks, return_exceptions=True)
    finally:
        await eng.dispose()


async def test_exclusive_claim_blocks_scan_channel(monkeypatch):
    from app.services.channel_service import ChannelService
    eng, maker = await _maker()
    called = []

    async def fake_inner(self, channel, video_list=None):
        called.append(channel.id)
        return 5

    monkeypatch.setattr(ChannelService, "_scan_channel_inner", fake_inner)
    try:
        async with maker() as db:
            ch = await _channel(db, "c1")
            await db.commit()
            ca.exclusive_scans.add(ch.id)
            assert await ChannelService(db).scan_channel(ch) == 0
            assert called == []
            ca.exclusive_scans.clear()
            assert await ChannelService(db).scan_channel(ch) == 5
    finally:
        await eng.dispose()
