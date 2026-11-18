import asyncio
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as database
import app.routers.channels as channels_router
import app.routers.system as system_router
import app.services.channel_service as cs
import app.tasks.temp_cleanup as temp_cleanup
from app.config import settings
from app.database import Base
from app.models import AppSetting, Channel, Video
from app.services import download_service


@pytest.fixture(autouse=True)
def clean_state():
    cs._moving_channels.clear()
    cs._scanning_channels.clear()
    cs._working_channels.clear()
    cs._move_all_running = False
    cs._last_scan_attempt.clear()
    download_service._active_downloads.clear()
    yield
    cs._moving_channels.clear()
    cs._scanning_channels.clear()
    cs._working_channels.clear()
    download_service._active_downloads.clear()


async def _setup(monkeypatch, tmp_path, channels=(("one", "c_one"),)):
    db_file = tmp_path / "test.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(temp_cleanup, "async_session", maker)
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    monkeypatch.setattr(settings, "EXTRA_DOWNLOAD_DIRS", str(tmp_path))
    ids = []
    async with maker() as db:
        for name, channel_id in channels:
            ch = Channel(channel_id=channel_id, channel_name=name, channel_url=f"u_{name}",
                         platform="youtube", health_status="unknown")
            db.add(ch)
            await db.flush()
            ids.append(ch.id)
        await db.commit()
    return maker, db_file, root, ids


async def test_scan_all_continues_past_failure_and_skips_standalone(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path, channels=(
        ("good1", "c1"), ("bad", "c2"), ("good2", "c3"), ("standalone", "__standalone__"),
    ))
    scanned = []

    async def fake_scan(self, channel):
        scanned.append(channel.channel_name)
        if channel.channel_name == "bad":
            raise RuntimeError("boom")
        return 0

    monkeypatch.setattr(cs.ChannelService, "scan_channel", fake_scan)

    async with maker() as db:
        resp = await system_router.trigger_scan_all(db)
    assert resp["channels_scanned"] == 3
    assert "message" in resp
    for task in list(system_router._scan_all_tasks):
        await task
    assert sorted(scanned) == ["bad", "good1", "good2"]


async def test_scan_now_during_scan_returns_409(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    cs._scanning_channels.add(ids[0])
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await channels_router.trigger_scan(ids[0], db)
    assert exc.value.status_code == 409


def _make_service(db, monkeypatch, listing, on_network):
    service = cs.ChannelService(db)
    service.yt_api = None

    async def noop(channel):
        return None

    async def fetch(channel):
        return listing

    def rss(*a, **k):
        on_network("rss")
        return {}

    monkeypatch.setattr(service, "_backfill_channel_art", noop)
    monkeypatch.setattr(service, "fetch_video_list", fetch)
    monkeypatch.setattr(service.ytdlp, "get_rss_upload_dates", rss)
    return service


async def test_scan_holds_no_write_across_network_calls(monkeypatch, tmp_path):
    maker, db_file, _, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        db.add(Video(channel_id=ids[0], video_id="old", title="old", status="pending",
                     season=2025, episode=1, upload_date=date(2025, 1, 1)))
        await db.commit()

    listing = [
        {"id": "old", "title": "old", "url": "https://example.test/old", "upload_date": "20250101"},
        {"id": "new", "title": "new", "url": "https://example.test/new"},
    ]
    probes = []

    def on_network(what):
        # A second connection can take the write lock only if this session holds none.
        other = sqlite3.connect(db_file, timeout=0)
        try:
            other.execute("BEGIN IMMEDIATE")
            probes.append(what)
        finally:
            other.rollback()
            other.close()

    def info(*a, **k):
        on_network("info")
        return None, "timed out"

    async with maker() as db:
        channel = await db.get(Channel, ids[0])
        service = _make_service(db, monkeypatch, listing, on_network)
        monkeypatch.setattr(service.ytdlp, "get_video_info_or_error", info)
        await service._scan_channel_inner(channel)
    assert probes == ["rss", "info"]


async def test_failed_date_lookup_stops_after_three(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    listing = [{"id": "undated", "title": "u", "url": "https://example.test/u"}]
    calls = []

    def info(*a, **k):
        calls.append(1)
        return None, "timed out"

    async def run_scan():
        async with maker() as db:
            channel = await db.get(Channel, ids[0])
            service = _make_service(db, monkeypatch, listing, lambda what: None)
            monkeypatch.setattr(service.ytdlp, "get_video_info_or_error", info)
            await service._scan_channel_inner(channel)

    async def backdate(days):
        await _backdate(maker, ids[0], days)

    await run_scan()
    await run_scan()  # one blip is retried on the next scan
    assert len(calls) == 2
    await run_scan()  # after two failures, not again within a week
    assert len(calls) == 2
    await backdate(8)
    await run_scan()
    assert len(calls) == 3
    await backdate(8)
    await run_scan()  # three failures: given up, not retried after a week
    assert len(calls) == 3
    await backdate(31)
    await run_scan()  # but retried after 30 days
    assert len(calls) == 4

    async with maker() as db:
        count = await db.scalar(select(Video.id).where(Video.video_id == "undated"))
        assert count is None  # never stored with a made-up date


async def _backdate(maker, channel_pk, days):
    async with maker() as db:
        row = await db.get(AppSetting, f"date_lookup_failures:{channel_pk}")
        old = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        data = json.loads(row.value)
        for rec in data.values():
            rec["t"] = old
        row.value = json.dumps(data)
        await db.commit()


async def _scan_once(maker, channel_pk, listing, info):
    async with maker() as db:
        channel = await db.get(Channel, channel_pk)
        service = _make_service(db, monkeypatch_holder[0], listing, lambda what: None)
        service.ytdlp.get_video_info_or_error = info
        await service._scan_channel_inner(channel)
        return channel.health_status, channel.last_error_code


monkeypatch_holder = [None]


async def test_cookie_and_bot_errors_do_not_count(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    monkeypatch_holder[0] = monkeypatch
    listing = [{"id": "undated", "title": "u", "url": "https://example.test/u"}]
    calls = []

    def info(*a, **k):
        calls.append(1)
        return None, "Sign in to confirm you're not a bot"

    for _ in range(6):
        health, code = await _scan_once(maker, ids[0], listing, info)
    assert len(calls) == 6  # never given up: the error says nothing about the video
    assert code == "METADATA_DEGRADED"
    async with maker() as db:
        assert await db.get(AppSetting, f"date_lookup_failures:{ids[0]}") is None


async def test_given_up_video_keeps_the_warning(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    monkeypatch_holder[0] = monkeypatch
    listing = [{"id": "undated", "title": "u", "url": "https://example.test/u"}]

    def info(*a, **k):
        return None, "timed out"

    for _ in range(2):
        await _scan_once(maker, ids[0], listing, info)
    await _backdate(maker, ids[0], 8)
    await _scan_once(maker, ids[0], listing, info)  # third failure
    health, code = await _scan_once(maker, ids[0], listing, info)  # given up, skipped
    assert code == "METADATA_DEGRADED"


async def test_repeated_listing_entry_counts_once(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    monkeypatch_holder[0] = monkeypatch
    entry = {"id": "undated", "title": "u", "url": "https://example.test/u"}

    def info(*a, **k):
        return None, "timed out"

    await _scan_once(maker, ids[0], [entry, dict(entry)], info)
    async with maker() as db:
        row = await db.get(AppSetting, f"date_lookup_failures:{ids[0]}")
        assert json.loads(row.value)["undated"]["n"] == 1


async def test_force_rescan_clears_failures(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        db.add(AppSetting(key=f"date_lookup_failures:{ids[0]}", value='{"v": {"n": 3, "t": "2020-01-01T00:00:00+00:00"}}'))
        await db.commit()

    async def fetch(self, channel):
        return [{"id": "v", "title": "t"}]

    async def inner(self, channel, video_list=None):
        return 0

    monkeypatch.setattr(cs.ChannelService, "fetch_video_list", fetch)
    monkeypatch.setattr(cs.ChannelService, "_scan_channel_inner", inner)
    async with maker() as db:
        channel = await db.get(Channel, ids[0])
        await channels_router._force_rescan_locked(channel, db)
    async with maker() as db:
        assert await db.get(AppSetting, f"date_lookup_failures:{ids[0]}") is None


async def test_scan_all_refuses_while_running(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)

    async def forever():
        await asyncio.sleep(30)

    task = asyncio.create_task(forever())
    system_router._scan_all_tasks.add(task)
    try:
        async with maker() as db:
            with pytest.raises(HTTPException) as exc:
                await system_router.trigger_scan_all(db)
        assert exc.value.status_code == 409
    finally:
        task.cancel()
        system_router._scan_all_tasks.discard(task)


async def test_scan_now_allowed_while_download_runs(monkeypatch, tmp_path):
    maker, _, _, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        db.add(Video(channel_id=ids[0], video_id="dl", title="dl", status="downloading",
                     season=1, episode=1, upload_date=date(2025, 1, 1)))
        await db.commit()
    download_service._active_downloads.add("dl")

    async def fake_scan(self, channel):
        return 2

    monkeypatch.setattr(cs.ChannelService, "scan_channel", fake_scan)
    async with maker() as db:
        resp = await channels_router.trigger_scan(ids[0], db)
    assert resp["new_videos"] == 2


async def test_move_contents_folder_pair_cache(monkeypatch, tmp_path):
    maker, _, root, ids = await _setup(monkeypatch, tmp_path)
    old_folder = root / "one" / "Season 1"
    old_folder.mkdir(parents=True)
    async with maker() as db:
        channel = await db.get(Channel, ids[0])
        folder = channels_router.channel_folder_name(channel)
        old_folder = root / folder / "Season 1"
        old_folder.mkdir(parents=True, exist_ok=True)
        for n in ("a", "b"):
            p = old_folder / f"{n}.mp4"
            p.write_text(n)
            db.add(Video(channel_id=ids[0], video_id=f"v{n}", title=n, status="completed",
                         file_path=str(p), season=1, episode=1, upload_date=date(2025, 1, 1)))
        await db.commit()

        # Same place: nothing moves.
        moved, errors, _ = await channels_router._move_channel_contents(
            db, channel, str(root), str(root))
        assert (moved, errors) == (0, [])

        new_root = tmp_path / "elsewhere"
        new_root.mkdir()
        monkeypatch.setattr(settings, "EXTRA_DOWNLOAD_DIRS", str(tmp_path))
        moved, errors, _ = await channels_router._move_channel_contents(
            db, channel, str(root), str(new_root))
        assert (moved, errors) == (2, [])
        assert (new_root / folder / "Season 1" / "a.mp4").exists()
        assert (new_root / folder / "Season 1" / "b.mp4").exists()


async def test_temp_sweeper_leaves_running_download_folder(monkeypatch, tmp_path):
    await _setup(monkeypatch, tmp_path)
    tmp_root = tmp_path / "downloads" / temp_cleanup.TEMP_DIR_NAME
    running = tmp_root / "running"
    abandoned = tmp_root / "abandoned"
    for folder in (running, abandoned):
        folder.mkdir(parents=True)
        (folder / "video.mp4.part").write_text("x")
    old = time.time() - temp_cleanup.MAX_AGE_SECONDS - 3600
    os.utime(running, (old, old))       # the folder itself looks old...
    os.utime(abandoned / "video.mp4.part", (old, old))
    os.utime(abandoned, (old, old))     # ...but only the running one still has fresh writes

    download_service._active_downloads.add("some_video")
    await temp_cleanup.cleanup_download_temp()
    assert running.is_dir()
    assert not abandoned.exists()
