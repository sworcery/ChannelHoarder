import json
import os
import time as time_mod
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AppSetting, Channel
from app.tasks import scan_channels as sc
from app.utils.scan_window import compute_next_scan_at, in_scan_window, spread_into_window

TZ = timezone.utc


def _local(hour, minute=0):
    return datetime(2026, 6, 1, hour, minute, tzinfo=TZ)


def _to_local(naive_utc):
    return naive_utc.replace(tzinfo=TZ)


def _set_tz(monkeypatch, name):
    if not hasattr(time_mod, "tzset"):
        pytest.skip("no tzset on this platform")
    monkeypatch.setenv("TZ", name)
    time_mod.tzset()


@pytest.fixture(autouse=True)
def _utc_system_tz(monkeypatch):
    """Window hours are system-local; pin the system zone to UTC (restored after)."""
    if not hasattr(time_mod, "tzset"):
        yield
        return
    old = os.environ.get("TZ")
    _set_tz(monkeypatch, "UTC")
    yield
    monkeypatch.undo()
    if old is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = old
    time_mod.tzset()


@pytest.fixture
async def env(monkeypatch):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    class Env:
        pass

    e = Env()
    e.maker = maker
    e.scanned = []
    monkeypatch.setattr(sc, "async_session", maker)

    async def fake_scan(self, channel):
        e.scanned.append(channel.id)
        return 0

    async def no_sleep(_):
        return None

    async def no_broadcast(*a, **k):
        return None

    monkeypatch.setattr(sc.ChannelService, "scan_channel", fake_scan)
    monkeypatch.setattr(sc.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(sc.NotificationService, "broadcast", no_broadcast)
    yield e
    await eng.dispose()


async def _set(e, key, value):
    async with e.maker() as db:
        db.add(AppSetting(key=key, value=json.dumps(value)))
        await db.commit()


async def _channel(e, cid, next_scan_at):
    async with e.maker() as db:
        ch = Channel(channel_id=cid, channel_name=cid, channel_url=f"https://x/{cid}",
                     platform="youtube", health_status="unknown", next_scan_at=next_scan_at)
        db.add(ch)
        await db.commit()
        return ch.id


async def _next_scan(e, pk):
    async with e.maker() as db:
        return (await db.get(Channel, pk)).next_scan_at


def _window_around_now(inside: bool):
    h = datetime.now().astimezone().hour
    if inside:
        return (h - 1) % 24, (h + 8) % 24
    return (h + 2) % 24, (h + 11) % 24


def _overdue():
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=3)


class TestTick:
    async def test_outside_window_scans_nothing_and_keeps_next_scan(self, env):
        start, end = _window_around_now(inside=False)
        assert not in_scan_window(start, end)
        await _set(env, "scan_window_start_hour", start)
        await _set(env, "scan_window_end_hour", end)
        due = _overdue()
        pk = await _channel(env, "a", due)
        pk_null = await _channel(env, "b", None)

        await sc.scan_due_channels()

        assert env.scanned == []
        assert await _next_scan(env, pk) == due
        assert await _next_scan(env, pk_null) is None

    async def test_inside_window_scans_due_channels(self, env):
        start, end = _window_around_now(inside=True)
        await _set(env, "scan_window_start_hour", start)
        await _set(env, "scan_window_end_hour", end)
        pk = await _channel(env, "a", _overdue())
        pk_null = await _channel(env, "b", None)
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=5)
        pk_future = await _channel(env, "c", future)

        await sc.scan_due_channels()

        assert sorted(env.scanned) == sorted([pk, pk_null])
        assert pk_future not in env.scanned

    async def test_no_window_scans_due_channels(self, env):
        pk = await _channel(env, "a", _overdue())
        await sc.scan_due_channels()
        assert env.scanned == [pk]

    async def test_tick_stops_when_window_closes_mid_tick(self, env, monkeypatch):
        for i in range(6):
            await _channel(env, f"c{i}", _overdue() - timedelta(minutes=i))
        calls = {"n": 0}

        def flipping(start, end, now_local=None):
            calls["n"] += 1
            return calls["n"] <= 3  # entry check + two loop passes, then closed

        monkeypatch.setattr(sc, "in_scan_window", flipping)
        await sc.scan_due_channels()
        assert len(env.scanned) == 2

    async def test_oldest_due_channels_go_first(self, env):
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        newer = await _channel(env, "newer", now - timedelta(hours=1))
        oldest = await _channel(env, "oldest", now - timedelta(hours=9))
        never = await _channel(env, "never", None)
        middle = await _channel(env, "middle", now - timedelta(hours=4))
        await sc.scan_due_channels()
        assert env.scanned == [never, oldest, middle, newer]


class TestOvernightNextStart:
    @pytest.mark.parametrize("hour", [23, 1, 12])
    def test_next_start_is_inside_window_and_after_offset(self, hour):
        now = _local(hour)
        for _ in range(300):
            result = _to_local(compute_next_scan_at(22, 6, now_local=now, min_offset_hours=1))
            assert in_scan_window(22, 6, result)
            assert result >= now + timedelta(hours=1)

    def test_at_23_can_land_the_same_night(self):
        # earliest is 00:00, the window is still open until 06:00 the same night
        now = _local(23)
        results = [_to_local(compute_next_scan_at(22, 6, now_local=now, min_offset_hours=1))
                   for _ in range(300)]
        assert max(results) < now + timedelta(hours=7)

    def test_at_01_can_land_the_same_night(self):
        # earliest is 02:00; the window that opened yesterday is open until 06:00
        now = _local(1)
        results = [_to_local(compute_next_scan_at(22, 6, now_local=now, min_offset_hours=1))
                   for _ in range(300)]
        assert max(results) < _local(6)

    def test_at_12_waits_for_evening(self):
        now = _local(12)
        results = [_to_local(compute_next_scan_at(22, 6, now_local=now, min_offset_hours=1))
                   for _ in range(300)]
        assert min(results) >= _local(22)
        assert max(results) <= _local(6) + timedelta(days=1)


class TestSpread:
    @pytest.mark.parametrize("hour", [23, 1, 12])
    @pytest.mark.parametrize("window", [(22, 6), (9, 17), (0, 12)])
    def test_all_times_inside_window(self, hour, window):
        now = _local(hour)
        times = spread_into_window(200, now_local=now, start_hour=window[0], end_hour=window[1])
        assert len(times) == 200
        for t in times:
            assert in_scan_window(window[0], window[1], _to_local(t))
            assert _to_local(t) > now

    def test_spread_is_wide(self):
        times = spread_into_window(200, now_local=_local(12), start_hour=22, end_hour=6)
        assert (times[-1] - times[0]) > timedelta(hours=6)

    def test_no_window_old_behaviour(self):
        now = _local(12)
        times = spread_into_window(100, now_local=now)
        for t in times:
            assert now < _to_local(t) <= now + timedelta(hours=24)

    def test_zero_count(self):
        assert spread_into_window(0, now_local=_local(12), start_hour=22, end_hour=6) == []


class TestImportStagger:
    async def test_imported_channels_get_times_inside_window(self, monkeypatch):
        import io

        from fastapi import UploadFile

        from app.routers import settings as settings_router

        eng = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        start, end = 22, 6
        payload = {
            "settings": {},
            "channels": [
                {"channel_id": f"UC{i}", "channel_name": f"n{i}", "channel_url": f"https://x/{i}"}
                for i in range(20)
            ],
        }
        async with maker() as db:
            db.add(AppSetting(key="scan_window_start_hour", value=json.dumps(start)))
            db.add(AppSetting(key="scan_window_end_hour", value=json.dumps(end)))
            await db.commit()
            upload = UploadFile(file=io.BytesIO(json.dumps(payload).encode()), filename="c.json")
            out = await settings_router.import_config(db=db, file=upload)
            assert out["imported_channels"] == 20
            from sqlalchemy import select
            rows = (await db.execute(select(Channel.next_scan_at))).scalars().all()
        await eng.dispose()
        assert len(rows) == 20
        for t in rows:
            assert t is not None
            assert in_scan_window(start, end, t.replace(tzinfo=TZ).astimezone())

    async def test_bad_imported_hour_is_skipped_and_channels_still_scheduled(self):
        import io

        from fastapi import UploadFile
        from sqlalchemy import select

        from app.routers import settings as settings_router

        eng = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        payload = {
            "settings": {"scan_window_start_hour": 24, "scan_window_end_hour": 6},
            "channels": [
                {"channel_id": f"UC{i}", "channel_name": f"n{i}", "channel_url": f"https://x/{i}"}
                for i in range(5)
            ],
        }
        async with maker() as db:
            upload = UploadFile(file=io.BytesIO(json.dumps(payload).encode()), filename="c.json")
            out = await settings_router.import_config(db=db, file=upload)
            assert out["imported_channels"] == 5
            assert {s["key"] for s in out["skipped_settings"]} >= {"scan_window_start_hour"}
            assert out["imported_settings"] == 0
            rows = (await db.execute(select(Channel.next_scan_at))).scalars().all()
        await eng.dispose()
        assert len(rows) == 5 and all(t is not None for t in rows)


class TestEndMargin:
    def test_times_stop_ten_minutes_before_close(self):
        now = _local(5, 30)  # the window closes at 06:00
        for _ in range(200):
            t = _to_local(compute_next_scan_at(22, 6, now_local=now - timedelta(hours=1), min_offset_hours=1))
            assert t <= _local(5, 50)
        times = spread_into_window(200, now_local=_local(3), start_hour=22, end_hour=6)
        assert max(times).replace(tzinfo=TZ) <= _local(5, 50)


class TestDst:
    def test_spring_forward_night_stays_inside_window(self, monkeypatch):
        _set_tz(monkeypatch, "America/Denver")
        # 2027-03-13 22:00 local; clocks jump 02:00 -> 03:00 overnight
        for hour in (20, 23):
            now = datetime(2027, 3, 13, hour, 0).astimezone()
            times = spread_into_window(200, now_local=now, start_hour=22, end_hour=6)
            for t in times:
                local = t.replace(tzinfo=timezone.utc).astimezone()
                assert in_scan_window(22, 6, local), local
        now = datetime(2027, 3, 13, 12, 0).astimezone()
        for _ in range(200):
            t = compute_next_scan_at(22, 6, now_local=now, min_offset_hours=1)
            local = t.replace(tzinfo=timezone.utc).astimezone()
            assert in_scan_window(22, 6, local), local


class TestStartupStagger:
    async def _run_init(self, monkeypatch, start, end, n):
        from sqlalchemy import select

        from app import database

        eng = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        monkeypatch.setattr(database, "engine", eng)
        await database.init_database()  # fresh install: nothing to stagger, nothing breaks
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            if start is not None:
                db.add(AppSetting(key="scan_window_start_hour", value=json.dumps(start)))
                db.add(AppSetting(key="scan_window_end_hour", value=json.dumps(end)))
            for i in range(n):
                db.add(Channel(channel_id=f"UC{i}", channel_name=f"n{i}", channel_url=f"https://x/{i}",
                               platform="youtube", health_status="unknown", next_scan_at=None))
            await db.commit()
        await database.init_database()
        async with maker() as db:
            rows = (await db.execute(select(Channel.next_scan_at))).scalars().all()
        await eng.dispose()
        return rows

    async def test_null_next_scan_gets_times_inside_window(self, monkeypatch):
        rows = await self._run_init(monkeypatch, 22, 6, 30)
        assert len(rows) == 30
        for t in rows:
            assert t is not None
            assert in_scan_window(22, 6, t.replace(tzinfo=TZ).astimezone())

    async def test_bad_stored_hour_counts_as_no_window(self, monkeypatch):
        rows = await self._run_init(monkeypatch, 24, 6, 5)
        assert len(rows) == 5 and all(t is not None for t in rows)

    async def test_no_window_staggers_over_a_day(self, monkeypatch):
        rows = await self._run_init(monkeypatch, None, None, 5)
        assert len(rows) == 5 and all(t is not None for t in rows)
