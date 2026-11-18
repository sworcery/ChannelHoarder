import time
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel
from app.routers import dashboard
from app.services import queue_state as qs


async def _maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


def _fake_client(pings, status=200, fail=False):
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, timeout=None):
            pings.append(url)
            if fail:
                raise OSError("down")
            return _Resp(status)

    return FakeClient


@pytest.fixture
def env(monkeypatch):
    pings = []
    monkeypatch.setattr(dashboard, "_pot_cache", None)
    monkeypatch.setattr(dashboard, "_APP_START", time.monotonic() - 3600)
    monkeypatch.setattr(settings, "POT_SERVER_ENABLED", True)
    monkeypatch.setattr(dashboard, "disk_space_status", lambda: (50.0, "", "healthy"))
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", _fake_client(pings))
    return pings


def _ids(alerts):
    return [a["id"] for a in alerts]


def _channel(n, **kw):
    return Channel(channel_id=f"c{n}", channel_name=f"Chan {n}", channel_url=f"https://x/{n}", **kw)


async def test_quiet_when_all_is_well(env):
    maker = await _maker()
    async with maker() as db:
        assert await dashboard.get_alerts(db) == []


async def test_disk_full_paused_and_critical(env, monkeypatch):
    maker = await _maker()
    async with maker() as db:
        await qs.pause_queue(db, qs.REASON_DISK_FULL)
        monkeypatch.setattr(dashboard, "disk_space_status", lambda: (0.4, "", "critical"))
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["disk_full"]  # no second "paused" alert
        assert alerts[0]["action"]["label"] == "Open Diagnostics"
        assert alerts[0]["action"]["kind"] == "link"
        assert alerts[0]["severity"] == "critical"

        # Space freed after the pause: only the Resume is left, worded that way
        monkeypatch.setattr(dashboard, "disk_space_status", lambda: (50.0, "", "healthy"))
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["disk_full"]
        assert alerts[0]["severity"] == "warning"
        assert "50.0 GB free now" in alerts[0]["message"]
        assert alerts[0]["action"]["kind"] == "resume_queue"

        await qs.resume_queue(db)
        monkeypatch.setattr(dashboard, "disk_space_status", lambda: (0.4, " in the temp folder", "critical"))
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["disk_full"]
        assert "temp folder" in alerts[0]["message"]
        assert alerts[0]["action"]["kind"] == "link"


async def test_cookies_expired(env):
    maker = await _maker()
    async with maker() as db:
        await qs.set_cookies_expired(db, True)
        await qs.pause_queue(db, qs.REASON_COOKIES)
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["cookies_expired"]
        assert alerts[0]["action"] == {"label": "Upload cookies", "kind": "link", "target": "/settings?tab=auth"}
        assert "whole queue is paused" in alerts[0]["message"]


async def test_queue_paused_manual(env):
    maker = await _maker()
    async with maker() as db:
        await qs.pause_queue(db, qs.REASON_MANUAL)
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["queue_paused"]
        assert "You paused the queue" in alerts[0]["message"]
        assert alerts[0]["action"]["kind"] == "resume_queue"


async def test_pot_down_and_cache(env, monkeypatch):
    pings = env
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", _fake_client(pings, fail=True))
    maker = await _maker()
    async with maker() as db:
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["pot_server_down"]
        assert alerts[0]["action"]["target"] == "/settings?tab=diagnostics"
        await dashboard.get_alerts(db)
        assert len(pings) == 1  # cached
        # After 60 s it pings again
        t, ok = dashboard._pot_cache
        monkeypatch.setattr(dashboard, "_pot_cache", (t - 61, ok))
        await dashboard.get_alerts(db)
        assert len(pings) == 2


async def test_pot_bad_status_and_disabled(env, monkeypatch):
    pings = env
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", _fake_client(pings, status=500))
    maker = await _maker()
    async with maker() as db:
        assert _ids(await dashboard.get_alerts(db)) == ["pot_server_down"]
        monkeypatch.setattr(settings, "POT_SERVER_ENABLED", False)
        monkeypatch.setattr(dashboard, "_pot_cache", None)
        assert await dashboard.get_alerts(db) == []
        assert len(pings) == 1  # disabled: no ping


async def test_unhealthy_channels(env):
    maker = await _maker()
    async with maker() as db:
        for i in range(5):
            db.add(_channel(i, health_status="unhealthy", last_error_code="RATE_LIMITED" if i == 0 else None))
        db.add(_channel(9, health_status="healthy"))
        db.add(_channel(10, health_status="unhealthy", enabled=False))  # paused channels don't count
        await db.commit()
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["unhealthy_channels"]
        a = alerts[0]
        assert a["title"].startswith("5 channels")
        assert "Chan 0 (The site is rate limiting downloads)" in a["message"]
        assert "Chan 3" not in a["message"] and "2 more" in a["message"]
        assert a["action"]["target"] == "/channels?health=unhealthy"


async def test_overdue_scans(env):
    maker = await _maker()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with maker() as db:
        db.add(_channel(1, next_scan_at=now - timedelta(hours=30)))
        db.add(_channel(2, next_scan_at=now - timedelta(hours=30), enabled=False))
        db.add(_channel(3, next_scan_at=now - timedelta(hours=2)))
        db.add(_channel(4, next_scan_at=None))
        await db.commit()
        alerts = await dashboard.get_alerts(db)
        assert _ids(alerts) == ["overdue_scans"]
        assert alerts[0]["title"].startswith("1 channel overdue")
        assert "catch up" in alerts[0]["message"]


async def test_priority_order(env, monkeypatch):
    pings = env
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", _fake_client(pings, fail=True))
    monkeypatch.setattr(dashboard, "disk_space_status", lambda: (0.2, "", "critical"))
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    maker = await _maker()
    async with maker() as db:
        await qs.set_cookies_expired(db, True)
        await qs.pause_queue(db, qs.REASON_MANUAL)
        db.add(_channel(1, health_status="unhealthy", next_scan_at=now - timedelta(days=3)))
        await db.commit()
        assert _ids(await dashboard.get_alerts(db)) == [
            "disk_full", "cookies_expired", "pot_server_down", "queue_paused",
            "unhealthy_channels", "overdue_scans",
        ]


async def test_pot_quiet_in_first_two_minutes(env, monkeypatch):
    pings = env
    monkeypatch.setattr(dashboard.httpx, "AsyncClient", _fake_client(pings, fail=True))
    monkeypatch.setattr(dashboard, "_APP_START", time.monotonic() - 30)
    maker = await _maker()
    async with maker() as db:
        assert await dashboard.get_alerts(db) == []
        assert pings == []


async def test_stats_ignore_standalone_channel(env, monkeypatch):
    monkeypatch.setattr(dashboard, "get_storage_usage", lambda custom_dirs=None: {
        "downloads_used": 0, "disk_free": 1, "disk_total": 1, "disk_used": 0, "channels": {}})
    maker = await _maker()
    async with maker() as db:
        db.add(Channel(channel_id="__standalone__", channel_name="Standalone", channel_url="x"))
        await db.commit()
        stats = await dashboard.get_stats(db)
        assert stats.total_channels == 0 and stats.active_channels == 0
