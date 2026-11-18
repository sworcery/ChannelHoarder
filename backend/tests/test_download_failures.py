from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
import app.services.download_service as ds
import app.tasks.process_queue as pq
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.routers import downloads
from app.services import queue_state


class _FakeYtdlp:
    def __init__(self, info=None, download_error=None, lookup_error=None):
        self.info = info
        self.download_error = download_error
        self.lookup_error = lookup_error
        self.info_calls = 0
        self.download_calls = 0

    def get_video_info_or_error(self, *a, **k):
        self.info_calls += 1
        if self.info is None:
            return None, self.lookup_error
        return self.info, None

    def download_video(self, *a, **k):
        self.download_calls += 1
        raise Exception(self.download_error or "stop here")


async def _setup(monkeypatch, tmp_path, min_quality=None, ytdlp=None):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ds, "async_session", maker)
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(pq, "async_session", maker)

    async def _no_wait():
        return None

    async def _no_broadcast(self, *a, **k):
        return None

    monkeypatch.setattr(ds, "wait_for_rate_limit", _no_wait)
    monkeypatch.setattr(ds.NotificationService, "broadcast", _no_broadcast)
    real_sleep = ds.asyncio.sleep

    async def _fast_sleep(delay, *a, **k):
        await real_sleep(min(delay, 0.01), *a, **k)

    monkeypatch.setattr(ds.asyncio, "sleep", _fast_sleep)

    root = tmp_path / "downloads"
    root.mkdir()
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u",
                     platform="youtube", health_status="unknown",
                     download_dir=str(root), quality="1080p", min_quality=min_quality)
        db.add(ch)
        await db.flush()
        vid = Video(channel_id=ch.id, video_id="v1", title="Vid", status="queued",
                    season=1, episode=1, upload_date=date(2025, 1, 1))
        db.add(vid)
        await db.flush()
        q = DownloadQueue(video_id=vid.id)
        db.add(q)
        await db.commit()
        ids = (vid.id, ch.id, q.id)

    service = ds.DownloadService()
    service.ytdlp = ytdlp or _FakeYtdlp()
    return maker, service, ids


async def _state(maker, ids):
    async with maker() as db:
        video = await db.get(Video, ids[0])
        queue = (await db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == ids[0])
        )).scalar_one_or_none()
        return video, queue


LOW_FORMATS = {"formats": [{"height": 480}]}


async def test_disk_full_pauses_queue_and_keeps_row(monkeypatch, tmp_path):
    maker, service, ids = await _setup(
        monkeypatch, tmp_path, ytdlp=_FakeYtdlp(download_error="No space left on device"))
    assert await service.download_video(*ids) is False

    video, queue = await _state(maker, ids)
    assert queue is not None and queue.started_at is None
    assert video.status == "queued"
    assert video.retry_count == 0
    async with maker() as db:
        assert await queue_state.get_pause_state(db) == (True, "disk_full")


async def test_quality_too_low_waits_1h_6h_24h_then_fails(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=LOW_FORMATS)
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)

    for hours in (1, 6, 24):
        await service.download_video(*ids)
        video, queue = await _state(maker, ids)
        assert video.status == "queued"
        assert queue is not None and queue.started_at is None
        wait = queue.retry_after - datetime.now(timezone.utc).replace(tzinfo=None)
        assert timedelta(hours=hours) - timedelta(minutes=1) < wait <= timedelta(hours=hours)
        assert video.retry_count == 0

    await service.download_video(*ids)
    video, queue = await _state(maker, ids)
    assert video.status == "failed"
    assert video.error_code == "QUALITY_TOO_LOW"
    assert queue is None
    assert ytdlp.download_calls == 0


async def test_dispatcher_skips_future_retry_after(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    launched = []

    async def _fake_run(video_id, channel_id, queue_id):
        launched.append(queue_id)

    monkeypatch.setattr(pq, "_run_download", _fake_run)

    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        q.retry_after = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
        await db.commit()
    await pq.process_download_queue()
    assert launched == []

    async with maker() as db:
        q = await db.get(DownloadQueue, ids[2])
        q.retry_after = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
        await db.commit()
    await pq.process_download_queue()
    for t in list(pq._active_tasks):
        await t
    assert launched == [ids[2]]


async def test_download_anyway_queues_with_check_skipped(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=LOW_FORMATS)
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    async with maker() as db:
        video = await db.get(Video, ids[0])
        video.status = "failed"
        video.error_code = "QUALITY_TOO_LOW"
        await db.delete(await db.get(DownloadQueue, ids[2]))
        await db.commit()

    async with maker() as db:
        await downloads.download_anyway(ids[0], db)

    video, queue = await _state(maker, ids)
    assert video.status == "queued" and video.error_code is None
    assert queue is not None and queue.skip_min_quality is True

    # The check is skipped: yt-dlp is asked to download without looking at formats
    await service.download_video(video.id, ids[1], queue.id)
    assert ytdlp.info_calls == 0
    assert ytdlp.download_calls == 1


async def test_download_anyway_refuses_other_failures(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        video = await db.get(Video, ids[0])
        video.status = "failed"
        video.error_code = "NETWORK_ERROR"
        await db.commit()
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.download_anyway(ids[0], db)
        assert exc.value.status_code == 409
    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.download_anyway(9999, db)
        assert exc.value.status_code == 404


async def test_failed_info_lookup_does_not_download(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=None)
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    assert await service.download_video(*ids) is False

    video, queue = await _state(maker, ids)
    assert ytdlp.download_calls == 0
    assert video.status == "queued" and video.retry_count == 1
    assert queue is not None
    # a short pause, not an immediate retry
    wait = queue.retry_after - datetime.now(timezone.utc).replace(tzinfo=None)
    assert timedelta(minutes=9) < wait <= timedelta(minutes=10)


async def test_failed_lookup_ends_failed_after_max_retries(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=None, lookup_error="Something odd happened")
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    for _ in range(ds.settings.MAX_RETRIES):
        await service.download_video(*ids)
        video, queue = await _state(maker, ids)
        assert video.status == "queued" and queue is not None
    await service.download_video(*ids)
    video, queue = await _state(maker, ids)
    assert video.status == "failed"
    assert queue is None
    assert ytdlp.download_calls == 0


async def test_expired_cookies_lookup_error_flags_cookies(monkeypatch, tmp_path):
    import os
    cfg = tmp_path / "config"
    cfg.mkdir()
    cookies = cfg / "cookies.txt"
    cookies.write_text("# Netscape\n")
    old = datetime.now().timestamp() - 3600
    os.utime(cookies, (old, old))
    monkeypatch.setattr(ds.settings, "CONFIG_DIR", str(cfg))

    ytdlp = _FakeYtdlp(info=None, lookup_error="Sign in to confirm you're not a bot")
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    await service.download_video(*ids)

    async with maker() as db:
        assert await queue_state.get_cookies_expired(db) is True
        assert await queue_state.get_pause_state(db) == (True, "cookies_expired")


async def test_private_video_lookup_error_is_skipped(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=None, lookup_error="Private video. Sign in if you've been granted access")
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    await service.download_video(*ids)
    video, queue = await _state(maker, ids)
    assert video.status == "skipped" and queue is None


async def test_upgrade_of_video_with_file_skips_the_waits(monkeypatch, tmp_path):
    ytdlp = _FakeYtdlp(info=LOW_FORMATS)
    maker, service, ids = await _setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    existing = tmp_path / "old.mp4"
    existing.write_bytes(b"old-video")
    async with maker() as db:
        video = await db.get(Video, ids[0])
        video.file_path = str(existing)
        video.status = "completed"
        await db.commit()

    await service.download_video(*ids)
    video, queue = await _state(maker, ids)
    assert video.status == "completed"
    assert queue is None
    assert existing.exists()


async def test_waiting_row_does_not_block_the_next_one(monkeypatch, tmp_path):
    maker, _service, ids = await _setup(monkeypatch, tmp_path)
    launched = []

    async def _fake_run(video_id, channel_id, queue_id):
        launched.append(queue_id)

    monkeypatch.setattr(pq, "_run_download", _fake_run)
    async with maker() as db:
        vid2 = Video(channel_id=ids[1], video_id="v2", title="Two", status="queued",
                     season=1, episode=2, upload_date=date(2025, 1, 2))
        db.add(vid2)
        await db.flush()
        q2 = DownloadQueue(video_id=vid2.id, priority=-5)
        db.add(q2)
        first = await db.get(DownloadQueue, ids[2])
        first.retry_after = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(tzinfo=None)
        await db.commit()
        q2_id = q2.id

    await pq.process_download_queue()
    for t in list(pq._active_tasks):
        await t
    assert launched == [q2_id]
