"""Stuck states: a row left started, a running-vs-started mix-up, orphan statuses,
a channel that can never be claimed, and quality waits that cannot be skipped."""
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select

import asyncio

import app.main as app_main
import app.tasks.retention as retention
import app.routers.channels as channels_router
import app.services.channel_service as cs
import app.services.download_service as ds
import app.tasks.process_queue as pq
from app.models import Channel, DownloadQueue, Video
from app.routers import downloads
from app.schemas import ChannelUpdate
from app.services import queue_state
from tests.test_download_failures import _setup, _state


@pytest.fixture(autouse=True)
def clean_state():
    for s in (cs._moving_channels, cs._scanning_channels, cs._working_channels, ds._active_downloads):
        s.clear()
    cs._draining_channels.clear()
    yield
    for s in (cs._moving_channels, cs._scanning_channels, cs._working_channels, ds._active_downloads):
        s.clear()
    cs._draining_channels.clear()


def _naive_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _mark_started(maker, ids, hours_ago=0):
    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        # Always past the 2-minute guard that protects a row the dispatcher just started.
        q.started_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago, minutes=10)
        v = await db.get(Video, ids[0])
        v.status = "downloading"
        await db.commit()


# ── S1: errors before the download starts ────────────────────────────────

async def test_error_before_download_leaves_row_not_started(monkeypatch, tmp_path):
    maker, service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)

    def _boom(*a, **k):
        raise PermissionError("Permission denied: cannot create the folder")

    monkeypatch.setattr(ds.os, "makedirs", _boom)
    assert await service.download_video(*ids) is False

    video, queue = await _state(maker, ids)
    assert video.status in ("queued", "failed")
    assert video.status != "downloading"
    assert queue is None or queue.started_at is None
    assert video.error_code is not None


async def test_full_disk_at_makedirs_pauses_queue(monkeypatch, tmp_path):
    maker, service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)

    def _full(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(ds.os, "makedirs", _full)
    assert await service.download_video(*ids) is False

    video, queue = await _state(maker, ids)
    assert video.status == "queued"
    assert queue is not None and queue.started_at is None
    assert video.retry_count == 0
    async with maker() as db:
        assert await queue_state.get_pause_state(db) == (True, "disk_full")


# ── Remove: running vs stuck ─────────────────────────────────────────────

async def test_remove_clears_stuck_row_and_refuses_running(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)

    # Running: refused, row untouched.
    ds._active_downloads.add("v1")
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.remove_from_queue(ids[2], db=db)
        assert exc.value.status_code == 409
    video, queue = await _state(maker, ids)
    assert queue is not None and queue.started_at is not None

    # Stuck (started, nothing running): cleared, video back to pending.
    ds._active_downloads.clear()
    async with maker() as db:
        await downloads.remove_from_queue(ids[2], db=db)
    video, queue = await _state(maker, ids)
    assert queue is None
    assert video.status == "pending"


async def test_clear_queue_clears_stuck_but_not_running(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)
    ds._active_downloads.add("v1")
    async with maker() as db:
        await downloads.clear_queue(db=db)
    _video, queue = await _state(maker, ids)
    assert queue is not None

    ds._active_downloads.clear()
    async with maker() as db:
        await downloads.clear_queue(db=db)
    video, queue = await _state(maker, ids)
    assert queue is None and video.status == "pending"


async def test_queue_api_exposes_running(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)
    async with maker() as db:
        data = await downloads.get_queue(skip=0, limit=50, search=None, db=db)
        assert data["items"][0].running is False
    ds._active_downloads.add("v1")
    async with maker() as db:
        data = await downloads.get_queue(skip=0, limit=50, search=None, db=db)
        assert data["items"][0].running is True


# ── 4-hour stale reset ───────────────────────────────────────────────────

async def test_stale_reset_runs_while_paused_and_skips_running(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids, hours_ago=5)
    async with maker() as db:
        await queue_state.pause_queue(db, queue_state.REASON_MANUAL)
        await db.commit()

    # Still running: left alone.
    ds._active_downloads.add("v1")
    await pq.process_download_queue()
    video, queue = await _state(maker, ids)
    assert queue.started_at is not None and video.status == "downloading"

    # Not running: reset even though the queue is paused.
    ds._active_downloads.clear()
    await pq.process_download_queue()
    video, queue = await _state(maker, ids)
    assert queue.started_at is None and video.status == "queued"


# ── S4: startup repair ───────────────────────────────────────────────────

async def test_startup_repairs_queued_video_without_row(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        # v1 keeps its row; v2 and v3 have none.
        ch_id = ids[1]
        db.add(Video(channel_id=ch_id, video_id="v2", title="Two", status="queued",
                     season=1, episode=2, upload_date=date(2025, 1, 2)))
        db.add(Video(channel_id=ch_id, video_id="v3", title="Three", status="downloading",
                     season=1, episode=3, upload_date=date(2025, 1, 3)))
        await db.commit()
    async with maker() as db:
        assert await app_main.repair_unqueued_videos(db) == 2
    async with maker() as db:
        rows = {v.video_id: v.status for v in (await db.execute(select(Video))).scalars()}
    assert rows == {"v1": "queued", "v2": "pending", "v3": "pending"}


# ── S2: draining ─────────────────────────────────────────────────────────

async def test_draining_channel_gets_no_new_download_until_claim_succeeds(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    launched = []

    async def _fake_run(video_id, channel_id, queue_id):
        launched.append(queue_id)

    monkeypatch.setattr(pq, "_run_download", _fake_run)

    async with maker() as db:
        db.add(Video(channel_id=ids[1], video_id="v2", title="Two", status="downloading",
                     season=1, episode=2, upload_date=date(2025, 1, 2)))
        await db.commit()
    ds._active_downloads.add("v2")

    # Channel-wide claim refused because of the running download: channel drains.
    async with maker() as db:
        assert await cs.try_claim(db, ids[1], "work") is not None  # no drain asked
        assert not cs._draining_channels
        reason = await cs.try_claim(db, ids[1], "work", drain=True)
    assert reason == cs.DRAINING_MESSAGE
    assert ids[1] in cs._draining_channels
    assert not cs._working_channels

    await pq.process_download_queue()
    assert launched == []

    # A per-video claim does not start draining.
    cs._draining_channels.clear()
    async with maker() as db:
        v2 = (await db.execute(select(Video).where(Video.video_id == "v2"))).scalar_one()
        assert await cs.try_claim(db, ids[1], "work", video_ids=[v2.id]) is not None
    assert not cs._draining_channels
    cs._working_channels.clear()

    # An entry expires after 30 minutes.
    cs._draining_channels[ids[1]] = cs.time.monotonic() - cs.DRAINING_MAX_SECONDS - 1
    assert cs.draining_channel_ids() == set()

    # The download ends: the claim succeeds and clears draining.
    cs._draining_channels[ids[1]] = cs.time.monotonic()
    ds._active_downloads.clear()
    async with maker() as db:
        v2 = (await db.execute(select(Video).where(Video.video_id == "v2"))).scalar_one()
        v2.status = "completed"
        await db.commit()
    async with maker() as db:
        assert await cs.try_claim(db, ids[1], "work") is None
    cs.release_claim(ids[1], "work")
    assert ids[1] not in cs._draining_channels

    await pq.process_download_queue()
    for t in list(pq._active_tasks):
        await t
    assert launched == [ids[2]]


# ── S3: quality waits ────────────────────────────────────────────────────

async def _make_waiting(maker, ids, attempts=1):
    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        q.quality_attempts = attempts
        q.retry_after = _naive_now() + timedelta(hours=6)
        await db.commit()


async def test_download_anyway_on_waiting_row(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p")
    launched = []

    async def _fake_run(video_id, channel_id, queue_id):
        launched.append(queue_id)

    monkeypatch.setattr(pq, "_run_download", _fake_run)

    # A queued video that is not waiting for quality is refused.
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.download_anyway(ids[0], db=db)
        assert exc.value.status_code == 409

    await _make_waiting(maker, ids)
    async with maker() as db:
        await downloads.download_anyway(ids[0], db=db)
    _video, queue = await _state(maker, ids)
    assert queue.skip_min_quality is True and queue.retry_after is None

    await pq.process_download_queue()
    for t in list(pq._active_tasks):
        await t
    assert launched == [ids[2]]


async def test_lowering_or_clearing_min_quality_clears_waits(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p")
    await _make_waiting(maker, ids)

    # Raising it changes nothing.
    async with maker() as db:
        await channels_router.update_channel(ids[1], ChannelUpdate(min_quality="2160p"), db=db)
    _video, queue = await _state(maker, ids)
    assert queue.retry_after is not None and queue.quality_attempts == 1

    # Lowering it clears the wait.
    async with maker() as db:
        await channels_router.update_channel(ids[1], ChannelUpdate(min_quality="720p"), db=db)
    _video, queue = await _state(maker, ids)
    assert queue.retry_after is None and queue.quality_attempts == 0

    # Clearing it does too.
    await _make_waiting(maker, ids)
    async with maker() as db:
        await channels_router.update_channel(ids[1], ChannelUpdate(min_quality=None), db=db)
    _video, queue = await _state(maker, ids)
    assert queue.retry_after is None and queue.quality_attempts == 0


# ── Review follow-ups ────────────────────────────────────────────────────

async def test_run_download_resets_row_when_record_failure_raises(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)

    def _boom(*a, **k):
        raise PermissionError("denied")

    async def _record_boom(self, *a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(ds.os, "makedirs", _boom)
    monkeypatch.setattr(ds.DownloadService, "_record_failure", _record_boom)
    await pq._run_download(*ids)

    video, queue = await _state(maker, ids)
    assert queue is not None and queue.started_at is None
    assert video.status == "queued"


async def test_error_in_naming_template_lookup_is_recorded(monkeypatch, tmp_path):
    import app.services.naming_service as naming

    maker, service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)

    async def _boom(*a, **k):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(naming, "resolve_naming_template", _boom)
    assert await service.download_video(*ids) is False

    video, queue = await _state(maker, ids)
    assert video.status in ("queued", "failed")
    assert queue is None or queue.started_at is None


async def test_live_task_counts_as_running_without_active_downloads(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)
    task = asyncio.create_task(asyncio.sleep(30), name="download-v1")
    pq._active_tasks.add(task)
    try:
        assert not ds._active_downloads
        async with maker() as db:
            with pytest.raises(HTTPException) as exc:
                await downloads.remove_from_queue(ids[2], db=db)
            assert exc.value.status_code == 409
        _video, queue = await _state(maker, ids)
        assert queue is not None
    finally:
        task.cancel()
        pq._active_tasks.discard(task)


async def test_remove_refuses_row_started_seconds_ago(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        q.started_at = datetime.now(timezone.utc) - timedelta(seconds=30)
        (await db.get(Video, ids[0])).status = "downloading"
        await db.commit()
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.remove_from_queue(ids[2], db=db)
        assert exc.value.status_code == 409
    async with maker() as db:
        out = await downloads.bulk_remove_from_queue(downloads.BulkQueueRemove(queue_ids=[ids[2]]), db)
        assert out["removed"] == 0
    async with maker() as db:
        await downloads.clear_queue(db=db)
    _video, queue = await _state(maker, ids)
    assert queue is not None


async def test_bulk_remove_clears_stuck_and_leaves_running(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    await _mark_started(maker, ids)
    ds._active_downloads.add("v1")
    async with maker() as db:
        out = await downloads.bulk_remove_from_queue(downloads.BulkQueueRemove(queue_ids=[ids[2]]), db)
        assert out["removed"] == 0
    ds._active_downloads.clear()
    async with maker() as db:
        out = await downloads.bulk_remove_from_queue(downloads.BulkQueueRemove(queue_ids=[ids[2]]), db)
        assert out["removed"] == 1
    video, queue = await _state(maker, ids)
    assert queue is None and video.status == "pending"


async def test_download_now_on_stuck_row_vs_running_row(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    launched = []

    async def _fake_run(video_id, channel_id, queue_id):
        launched.append(queue_id)

    monkeypatch.setattr(pq, "_run_download", _fake_run)
    await _mark_started(maker, ids)

    ds._active_downloads.add("v1")
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.download_now(ids[2], db=db)
        assert exc.value.status_code == 409
    assert launched == []

    ds._active_downloads.clear()
    async with maker() as db:
        await downloads.download_now(ids[2], db=db)
    for t in list(pq._active_tasks):
        await t
    assert launched == [ids[2]]

    # Started seconds ago (the dispatcher's window): refused.
    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        q.started_at = datetime.now(timezone.utc) - timedelta(seconds=20)
        await db.commit()
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.download_now(ids[2], db=db)
        assert exc.value.status_code == 409
    assert launched == [ids[2]]


async def test_startup_repair_keeps_downloaded_file(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    f = tmp_path / "kept.mp4"
    f.write_text("data")
    async with maker() as db:
        db.add(Video(channel_id=ids[1], video_id="v2", title="Two", status="queued",
                     season=1, episode=2, upload_date=date(2025, 1, 2), file_path=str(f)))
        await db.commit()
    async with maker() as db:
        assert await app_main.repair_unqueued_videos(db) == 1
    async with maker() as db:
        v2 = (await db.execute(select(Video).where(Video.video_id == "v2"))).scalar_one()
    assert v2.status == "completed"


async def test_retention_refusal_does_not_drain(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(retention, "async_session", maker)
    async with maker() as db:
        ch = await db.get(Channel, ids[1])
        ch.retention_days = 30
        db.add(Video(channel_id=ids[1], video_id="v2", title="Two", status="downloading",
                     season=1, episode=2, upload_date=date(2025, 1, 2)))
        await db.commit()
    ds._active_downloads.add("v2")
    await retention._apply_to_channel(ids[1])
    assert not cs._draining_channels
    assert not cs._working_channels
