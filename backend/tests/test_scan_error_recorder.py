"""One recorder for failed scans (S4), Scan All on the tick's loop (C4), first scan
time of a new channel (C5), RSS and subtitles writers (C6)."""
import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.routers.channels as channels_router
import app.routers.system as system_router
import app.services.channel_service as cs
import app.tasks.rss_check as rc
import app.tasks.scan_channels as sc
from app.config import settings
from app.database import Base
from app.models import AppSetting, Channel
from app.services import channel_activity
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import ChannelListingError
from tests.test_download_failures import _FakeYtdlp, _setup as _download_setup, _state


@pytest.fixture(autouse=True)
def clean():
    cs._scanning_channels.clear()
    channel_activity.working_channels.clear()
    cs._last_scan_attempt.clear()
    yield
    cs._scanning_channels.clear()
    channel_activity.working_channels.clear()


@pytest.fixture
async def maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    await eng.dispose()


@pytest.fixture
def sent(monkeypatch):
    events = []

    async def fake(event, payload):
        events.append((event, payload))

    monkeypatch.setattr(NotificationService, "broadcast", staticmethod(fake))
    return events


async def _channel(maker, name="Chan", **kw):
    kw.setdefault("health_status", "healthy")
    async with maker() as db:
        ch = Channel(channel_id=f"id_{name}", channel_name=name, channel_url=f"u_{name}",
                     platform="youtube", **kw)
        db.add(ch)
        await db.commit()
        return ch.id


async def _no_jitter(db):
    return False, 0


# --- S4 -----------------------------------------------------------------

async def test_failed_download_keeps_scan_failed_and_webhook_is_not_resent(monkeypatch, tmp_path, sent):
    maker, service, ids = await _download_setup(
        monkeypatch, tmp_path, ytdlp=_FakeYtdlp(download_error="Private video"))

    async def record(event, payload):
        sent.append((event, payload))

    monkeypatch.setattr(NotificationService, "broadcast", staticmethod(record))  # setup silenced it
    async with maker() as db:
        ch = await db.get(Channel, ids[1])
        await cs.record_scan_error(db, ch, RuntimeError("listing dead"))
    assert [e for e, _ in sent] == ["scan_failed"]
    sent.clear()

    await service.download_video(*ids)  # fails for its own reason
    async with maker() as db:
        ch = await db.get(Channel, ids[1])
        assert ch.last_error_code == "SCAN_FAILED"
        await cs.record_scan_error(db, ch, RuntimeError("listing still dead"))
    assert "scan_failed" not in [e for e, _ in sent]  # still one per outage


async def test_record_scan_error_does_not_override_a_severe_code(maker, sent):
    cid = await _channel(maker, last_error_code="DISK_FULL")
    async with maker() as db:
        ch = await db.get(Channel, cid)
        await cs.record_scan_error(db, ch, RuntimeError("x"))
        assert ch.last_error_code == "DISK_FULL"
        assert ch.next_scan_at is not None
    assert cid in cs._last_scan_attempt
    assert sent == []


async def test_tick_generic_exception_records_like_a_listing_failure(maker, sent, monkeypatch):
    cid = await _channel(maker)
    monkeypatch.setattr(sc, "async_session", maker)
    monkeypatch.setattr(sc, "_get_jitter_settings", _no_jitter)

    async def boom(self, channel):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(cs.ChannelService, "scan_channel", boom)
    await sc.scan_due_channels()
    async with maker() as db:
        ch = await db.get(Channel, cid)
        assert ch.last_error_code == "SCAN_FAILED"
        assert ch.health_status == "unhealthy"
        assert ch.next_scan_at is not None
    assert cid in cs._last_scan_attempt
    assert [e for e, _ in sent].count("scan_failed") == 1
    assert sent[-1][0] == "scan_complete"


async def test_recovery_clears_scan_failed(maker, sent, monkeypatch):
    cid = await _channel(maker)
    async with maker() as db:
        ch = await db.get(Channel, cid)
        await cs.record_scan_error(db, ch, ChannelListingError("x"))
        assert ch.health_status == "unhealthy"

        async def listing(self, channel):
            return []

        monkeypatch.setattr(cs.ChannelService, "fetch_video_list", listing)
        monkeypatch.setattr(cs.ChannelService, "_backfill_channel_art", lambda *a, **k: asyncio.sleep(0))
        await cs.ChannelService(db)._scan_channel_inner(ch)
        await db.refresh(ch)
        assert ch.last_error_code is None
        assert ch.health_status == "healthy"


# --- C4 -----------------------------------------------------------------

async def test_scan_all_sends_scan_complete_with_counts(maker, sent, monkeypatch):
    import app.database as database

    a = await _channel(maker, "A")
    b = await _channel(maker, "B")
    await _channel(maker, "C")
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(sc, "_get_jitter_settings", _no_jitter)
    channel_activity.working_channels.add(b)  # busy: skipped, not scanned
    scanned = []

    async def fake_scan(self, channel):
        scanned.append(channel.channel_name)
        return 2

    monkeypatch.setattr(cs.ChannelService, "scan_channel", fake_scan)
    async with maker() as db:
        resp = await system_router.trigger_scan_all(db)
    assert resp["channels_scanned"] == 3
    for task in list(system_router._scan_all_tasks):
        await task
    assert sorted(scanned) == ["A", "C"]
    done = [p for e, p in sent if e == "scan_complete"]
    assert len(done) == 1
    assert done[0]["channels_scanned"] == 2
    assert done[0]["new_videos"] == 4
    assert done[0]["channels_skipped"] == 1
    assert a != b


# --- C5 -----------------------------------------------------------------

async def test_first_scan_inside_window_is_spread(maker):
    async with maker() as db:
        db.add(AppSetting(key="scan_window_start_hour", value=json.dumps(2)))
        db.add(AppSetting(key="scan_window_end_hour", value=json.dumps(10)))
        await db.commit()
        due = await cs.ChannelService(db)._first_scan_at()
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        assert due > now  # not a bare "due now" that waits for the window
        from app.utils.scan_window import in_scan_window
        local = due.replace(tzinfo=timezone.utc).astimezone()
        assert in_scan_window(2, 10, now_local=local)


async def test_first_scan_without_window_is_now(maker):
    async with maker() as db:
        due = await cs.ChannelService(db)._first_scan_at()
    assert abs((datetime.now(timezone.utc).replace(tzinfo=None) - due).total_seconds()) < 5


# --- C6 -----------------------------------------------------------------

async def _rss_env(monkeypatch, maker):
    monkeypatch.setattr(rc, "async_session", maker)
    monkeypatch.setattr(rc, "_fetch", lambda cid, pl: ({"new1": "20260101"}, None))

    async def no_pause():
        return None

    monkeypatch.setattr(rc, "_pause_between_feeds", no_pause)
    rc._next_check.clear()
    rc._handled.clear()
    rc._working.clear()
    rc._failures_in_a_row = 0
    rc._paused_until = None
    rc._last_minutes = None
    async with maker() as db:
        await db.merge(AppSetting(key="rss_check_minutes", value=json.dumps(30)))
        await db.commit()


async def _two_ticks():
    from datetime import timedelta
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    clock = {"t": now}
    rc._now = lambda: clock["t"]
    await rc.rss_check_tick()
    clock["t"] += timedelta(minutes=2000)
    await rc.rss_check_tick()
    clock["t"] += timedelta(minutes=2000)
    await rc.rss_check_tick()


async def test_rss_failed_scan_leaves_ids_unhandled(maker, monkeypatch, sent):
    original_now = rc._now
    try:
        await _rss_env(monkeypatch, maker)
        cid = await _channel(maker)
        calls = []

        async def failing_scan(pk):
            calls.append(pk)
            raise RuntimeError("boom")

        monkeypatch.setattr(rc, "_scan", failing_scan)
        await _two_ticks()
        assert len(calls) == 2  # asked again, not remembered as handled
        assert rc._handled.get(cid) is None
        async with maker() as db:
            ch = await db.get(Channel, cid)
            assert ch.last_error_code == "SCAN_FAILED"
    finally:
        rc._now = original_now


async def test_rss_successful_scan_marks_ids_handled(maker, monkeypatch, sent):
    original_now = rc._now
    try:
        await _rss_env(monkeypatch, maker)
        cid = await _channel(maker)
        calls = []

        async def ok_scan(pk):
            calls.append(pk)

        monkeypatch.setattr(rc, "_scan", ok_scan)
        await _two_ticks()
        assert len(calls) == 1
        assert "new1" in rc._handled[cid]
    finally:
        rc._now = original_now


async def test_subtitles_job_holds_work_claim_until_it_ends(maker, monkeypatch):
    cid = await _channel(maker)
    started = asyncio.Event()
    finish = asyncio.Event()

    async def fake_job(channel_id, channel_name):
        started.set()
        await finish.wait()

    monkeypatch.setattr(channels_router, "_download_channel_subtitles", fake_job)

    async def one_eligible(*a, **k):
        return None

    monkeypatch.setattr(channels_router, "_has_subtitles", lambda p: False)
    async with maker() as db:
        from app.models import Video
        from datetime import date
        db.add(Video(channel_id=cid, video_id="v", title="v", status="completed",
                     file_path="/x/v.mp4", season=1, episode=1, upload_date=date(2025, 1, 1)))
        await db.commit()
    async with maker() as db:
        await channels_router.download_channel_subtitles(cid, db)
    await started.wait()
    assert cid in channel_activity.subtitle_channels
    assert channel_activity.activity(cid)["subtitles"] is True
    assert cid not in channel_activity.working_channels
    assert len(channels_router._subtitle_tasks) == 1
    async with maker() as db:
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            await channels_router.download_channel_subtitles(cid, db)
        assert exc.value.status_code == 409
        # a move, rename or delete is refused...
        for kind in ("move", "work", "scan"):
            assert await channel_activity.try_claim(db, cid, kind) is not None
        # ...but a scan and downloads are not held back
        assert channel_activity.flag_reason(cid) is None
        assert cid not in channel_activity.blocked_channel_ids()
    finish.set()
    for t in list(channels_router._subtitle_tasks):
        await t
    assert cid not in channel_activity.subtitle_channels
    async with maker() as db:
        assert await channel_activity.try_claim(db, cid, "work") is None
        channel_activity.release_claim(cid, "work")


async def test_subtitles_job_releases_claim_when_it_fails(maker, monkeypatch):
    cid = await _channel(maker)
    channel_activity.subtitle_channels.add(cid)

    async def broken(channel_id, channel_name):
        raise RuntimeError("boom")

    monkeypatch.setattr(channels_router, "_download_channel_subtitles", broken)
    await channels_router._download_channel_subtitles_task(cid, "Chan")
    assert cid not in channel_activity.subtitle_channels


# --- review follow-ups ------------------------------------------------------

async def test_rss_failed_scan_waits_before_rescanning(maker, monkeypatch, sent):
    from datetime import timedelta
    original_now = rc._now
    try:
        for exc in (RuntimeError("boom"), cs.ScanFailed("dead")):
            await _rss_env(monkeypatch, maker)
            cid = await _channel(maker, f"R{type(exc).__name__}")
            calls = []

            async def failing_scan(pk, exc=exc):
                calls.append(pk)
                raise exc

            monkeypatch.setattr(rc, "_scan", failing_scan)
            clock = {"t": datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)}
            rc._now = lambda: clock["t"]
            await rc.rss_check_tick()          # first sight: schedules the check
            clock["t"] += timedelta(minutes=2000)
            await rc.rss_check_tick()          # scans, fails
            assert calls.count(cid) == 1
            clock["t"] += timedelta(minutes=120)
            await rc.rss_check_tick()          # next intervals: no new listing
            clock["t"] += timedelta(minutes=120)
            await rc.rss_check_tick()
            assert calls.count(cid) == 1
            assert rc._handled.get(cid) is None
    finally:
        rc._now = original_now


async def test_scan_now_500_records_the_failure(maker, sent, monkeypatch):
    cid = await _channel(maker)

    async def boom(self, channel):
        raise RuntimeError("extractor broke")

    monkeypatch.setattr(cs.ChannelService, "scan_channel", boom)
    from fastapi import HTTPException
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await channels_router.trigger_scan(cid, db)
    assert exc.value.status_code == 500
    assert "extractor broke" in exc.value.detail
    async with maker() as db:
        ch = await db.get(Channel, cid)
        assert ch.last_error_code == "SCAN_FAILED"
        assert ch.health_status == "unhealthy"
    assert [e for e, _ in sent] == ["scan_failed"]


async def test_recovery_with_many_failed_videos_keeps_a_reason(maker):
    from datetime import date
    from app.models import Video
    cid = await _channel(maker, last_error_code="SCAN_FAILED", health_status="unhealthy")
    async with maker() as db:
        for i in range(4):
            db.add(Video(channel_id=cid, video_id=f"f{i}", title="f", status="failed",
                         error_code="VIDEO_PRIVATE" if i < 3 else "GEO_BLOCKED",
                         season=1, episode=i + 1, upload_date=date(2025, 1, 1)))
        await db.commit()
        ch = await db.get(Channel, cid)
        await cs.ChannelService(db)._clear_scan_failed(ch)
        assert ch.health_status == "unhealthy"
        assert ch.last_error_code == "VIDEO_PRIVATE"


async def test_auth_failure_during_scan_outage_keeps_health(monkeypatch, tmp_path, sent):
    maker, service, ids = await _download_setup(
        monkeypatch, tmp_path, ytdlp=_FakeYtdlp(download_error="Sign in to confirm you're not a bot"))
    async with maker() as db:
        ch = await db.get(Channel, ids[1])
        ch.last_error_code = "SCAN_FAILED"
        ch.health_status = "unhealthy"
        await db.commit()
    await service.download_video(*ids)
    async with maker() as db:
        ch = await db.get(Channel, ids[1])
        assert ch.last_error_code == "SCAN_FAILED"
        assert ch.health_status == "unhealthy"


async def test_tick_skips_busy_channel_and_stays_quiet_when_nothing_scanned(maker, sent, monkeypatch):
    busy = await _channel(maker, "Busy")
    free = await _channel(maker, "Free")
    scanned = []

    async def fake_scan(self, channel):
        scanned.append(channel.id)
        return 0

    monkeypatch.setattr(cs.ChannelService, "scan_channel", fake_scan)
    channel_activity.moving_channels.add(busy)
    try:
        result = await sc.scan_channel_list(
            [(busy, "Busy"), (free, "Free")], maker, jitter_enabled=False, jitter_max=0)
        assert result == (1, 0, 1)
        assert scanned == [free]
        sent.clear()
        result = await sc.scan_channel_list(
            [(busy, "Busy")], maker, jitter_enabled=False, jitter_max=0)
        assert result == (0, 0, 1)
        assert sent == []  # nothing scanned: no scan_complete
    finally:
        channel_activity.moving_channels.discard(busy)
