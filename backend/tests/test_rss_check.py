import json
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import AppSetting, Channel, Video
from app.schemas import SettingsUpdate
from app.tasks import rss_check as rc

T0 = datetime(2026, 1, 1, 12, 0)


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
    e.now = T0
    e.fetches = []
    e.scans = []
    e.feed = {}  # feed channel_id -> dict | None
    monkeypatch.setattr(rc, "async_session", maker)
    monkeypatch.setattr(rc, "_now", lambda: e.now)

    def fake_fetch(cid, is_playlist):
        e.fetches.append(cid)
        f = e.feed.get(cid, {})
        return (f, None) if f is not None else (None, "HTTP 429")

    async def fake_scan(pk):
        e.scans.append(pk)

    async def no_pause():
        return None

    monkeypatch.setattr(rc, "_fetch", fake_fetch)
    monkeypatch.setattr(rc, "_scan", fake_scan)
    monkeypatch.setattr(rc, "_pause_between_feeds", no_pause)
    rc._next_check.clear()
    rc._handled.clear()
    rc._working.clear()
    rc._failures_in_a_row = 0
    rc._paused_until = None
    rc._last_minutes = None
    rc._last_spacing = None
    yield e
    await eng.dispose()


async def _set_minutes(e, n):
    async with e.maker() as db:
        db.add(AppSetting(key="rss_check_minutes", value=json.dumps(n)))
        await db.commit()


async def _channel(e, cid, platform="youtube", videos=()):
    async with e.maker() as db:
        ch = Channel(channel_id=cid, channel_name=cid, channel_url=f"https://x/{cid}",
                     platform=platform, health_status="unknown")
        db.add(ch)
        await db.flush()
        for v in videos:
            db.add(Video(channel_id=ch.id, video_id=v, title=v, status="downloaded", upload_date=datetime(2026, 1, 1).date(),
                         season=1, episode=1))
        await db.commit()
        return ch.id


async def _tick(e, advance_minutes=None):
    """Run a tick far enough ahead that every channel is due."""
    if not rc._next_check:
        await rc.rss_check_tick()  # first sight: schedules a random first check
    e.now = e.now + timedelta(minutes=advance_minutes if advance_minutes is not None else 2000)
    await rc.rss_check_tick()


async def test_new_id_scans_once(env):
    await _set_minutes(env, 30)
    pk = await _channel(env, "c1", videos=["a"])
    env.feed["c1"] = {"a": "20260101", "b": "20260102"}
    await _tick(env)
    assert env.scans == [pk]


async def test_known_id_no_scan(env):
    await _set_minutes(env, 30)
    await _channel(env, "c1", videos=["a", "b"])
    env.feed["c1"] = {"a": "20260101", "b": "20260102"}
    await _tick(env)
    assert env.fetches == ["c1"]
    assert env.scans == []


async def test_handled_id_not_rescanned(env):
    await _set_minutes(env, 30)
    await _channel(env, "c1", videos=["a"])
    env.feed["c1"] = {"a": "20260101", "short": "20260102"}
    await _tick(env)
    await _tick(env)
    assert len(env.fetches) == 2
    assert len(env.scans) == 1


async def test_setting_zero_no_fetch(env):
    await _set_minutes(env, 0)
    await _channel(env, "c1")
    await _tick(env)
    assert env.fetches == []


async def test_working_feed_that_fails_pauses_everything(env):
    await _set_minutes(env, 15)
    await _channel(env, "c1")
    await _tick(env)
    assert env.fetches == ["c1"]
    env.feed = {"c1": None}
    await _tick(env)
    assert len(env.fetches) == 2
    assert rc._paused_until is not None
    await _tick(env, 30)
    assert len(env.fetches) == 2  # still paused
    env.feed = {}
    await _tick(env, 40)  # past the 60 minute pause: checks are spread out again
    assert len(env.fetches) == 2
    await _tick(env, 20)
    assert len(env.fetches) == 3


async def test_never_working_feed_waits_alone(env):
    await _set_minutes(env, 15)
    await _channel(env, "broken")
    await _channel(env, "ok")
    env.feed = {"broken": None, "ok": {}}
    await _tick(env)
    assert sorted(env.fetches) == ["broken", "ok"]
    assert rc._paused_until is None
    # The broken feed waits 24h; the working one keeps its normal spacing.
    await _tick(env, 60)
    assert env.fetches.count("broken") == 1
    assert env.fetches.count("ok") == 2
    await _tick(env, 24 * 60)
    assert env.fetches.count("broken") == 2


async def test_two_failures_pause(env, monkeypatch):
    await _set_minutes(env, 15)
    for c in ("c1", "c2", "c3"):
        await _channel(env, c)
    # first fetch succeeds (whichever channel is oldest), the next two fail
    results = iter([{}, None, None])

    def fetch(cid, pl):
        env.fetches.append(cid)
        r = next(results)
        return (r, None) if r is not None else (None, "HTTP 429")

    monkeypatch.setattr(rc, "_fetch", fetch)
    await _tick(env)
    assert len(env.fetches) == 3
    assert rc._paused_until is not None
    n = len(env.fetches)
    await _tick(env, 10)
    assert len(env.fetches) == n


async def test_dead_feed_pauses_once_then_waits_alone(env):
    await _set_minutes(env, 15)
    await _channel(env, "dies")
    await _channel(env, "ok")
    await _tick(env)
    assert rc._paused_until is None
    env.feed = {"dies": None, "ok": {}}
    # Advance one tick at a time until the dead feed is next due and pauses everything.
    while rc._paused_until is None:
        await _tick(env, 5)
    dies_pk = next(pk for pk in rc._next_check if pk not in rc._working)
    # After the pause, its next failure gets the 24h retry rather than another pause.
    await _tick(env, 61)
    tries = env.fetches.count("dies")
    while env.fetches.count("dies") == tries:
        await _tick(env, 5)
    assert rc._paused_until is None
    assert rc._next_check[dies_pk] >= env.now + timedelta(hours=23)


async def test_turning_off_and_on_spreads_checks_again(env):
    await _set_minutes(env, 30)
    for c in ("c1", "c2", "c3"):
        await _channel(env, c)
    await _tick(env)
    async with env.maker() as db:
        s = await db.get(AppSetting, "rss_check_minutes")
        s.value = json.dumps(0)
        await db.commit()
    await rc.rss_check_tick()
    assert rc._next_check == {}
    async with env.maker() as db:
        s = await db.get(AppSetting, "rss_check_minutes")
        s.value = json.dumps(30)
        await db.commit()
    n = len(env.fetches)
    await rc.rss_check_tick()
    assert len(env.fetches) == n  # re-spread, not all due at once
    assert all(env.now <= t <= env.now + timedelta(minutes=30) for t in rc._next_check.values())


async def test_outside_scan_window_no_fetch(env, monkeypatch):
    monkeypatch.setattr(rc, "in_scan_window", lambda start, end: False)
    await _set_minutes(env, 30)
    await _channel(env, "c1")
    await _tick(env)
    assert env.fetches == []


async def test_channel_being_scanned_is_left_for_next_tick(env, monkeypatch):
    await _set_minutes(env, 30)
    pk = await _channel(env, "c1", videos=["a"])
    env.feed["c1"] = {"a": "20260101", "b": "20260102"}
    monkeypatch.setattr(rc, "_scanning_channels", {pk})
    await _tick(env)
    assert env.fetches == [] and env.scans == []
    monkeypatch.setattr(rc, "_scanning_channels", set())
    await _tick(env, 5)
    assert env.scans == [pk]


def test_spacing_leaves_room_for_scan_feed_requests():
    assert rc._spacing(30, 12, 12) == 30
    # 50 channels scanned every 12h load 100 feeds a day, leaving 500 for checks.
    assert rc._spacing(15, 50, 12) == 144
    # Scans alone would use the whole budget: still one check per channel a day.
    assert rc._spacing(15, 300, 12) == 1440


async def test_spacing_grows_with_channel_count(env, caplog):
    await _set_minutes(env, 15)
    for i in range(50):
        await _channel(env, f"c{i}")
    with caplog.at_level("INFO"):
        await rc.rss_check_tick()
    assert rc._last_spacing == 144
    assert "spacing raised" in caplog.text
    assert all(T0 <= t <= T0 + timedelta(minutes=144) for t in rc._next_check.values())


async def test_non_youtube_skipped(env):
    await _set_minutes(env, 30)
    await _channel(env, "r1", platform="rumble")
    await _tick(env)
    assert env.fetches == []


def test_in_scan_window():
    from app.utils.scan_window import in_scan_window
    at = lambda h: datetime(2026, 1, 1, h, 30)  # noqa: E731
    assert in_scan_window(None, None, at(3))
    assert in_scan_window(5, 5, at(3))
    assert in_scan_window(8, 20, at(8)) and not in_scan_window(8, 20, at(20))
    assert in_scan_window(22, 6, at(23)) and in_scan_window(22, 6, at(2))
    assert not in_scan_window(22, 6, at(12))


class _Resp:
    def __init__(self, status, text=""):
        self.status_code = status
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            req = httpx.Request("GET", "https://www.youtube.com/feeds/videos.xml")
            raise httpx.HTTPStatusError("err", request=req, response=httpx.Response(self.status_code, request=req))


_FEED = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <entry><yt:videoId>abc</yt:videoId><published>2026-01-15T12:00:00+00:00</published></entry>
</feed>"""


def test_fetch_rss_feed(monkeypatch):
    import app.services.ytdlp_service as ys
    monkeypatch.setattr(ys.httpx, "get", lambda url, timeout: _Resp(200, _FEED))
    assert ys.YtdlpService.fetch_rss_feed("UC1") == ({"abc": "20260115"}, None)
    assert ys.YtdlpService.get_rss_upload_dates("UC1") == {"abc": "20260115"}
    monkeypatch.setattr(ys.httpx, "get", lambda url, timeout: _Resp(404))
    assert ys.YtdlpService.fetch_rss_feed("UC1") == (None, "HTTP 404")
    assert ys.YtdlpService.get_rss_upload_dates("UC1") == {}


def test_settings_validation():
    assert SettingsUpdate(rss_check_minutes=0).rss_check_minutes == 0
    assert SettingsUpdate(rss_check_minutes=15).rss_check_minutes == 15
    with pytest.raises(ValidationError):
        SettingsUpdate(rss_check_minutes=14)
