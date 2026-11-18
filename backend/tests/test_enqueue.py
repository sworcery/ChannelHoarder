import asyncio
import ast
import pathlib
import re
import time
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.database as database
import app.services.download_service as ds
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.routers import downloads
from app.schemas import QueueAdd
from app.services.download_service import enqueue_video, enqueue_videos
from tests.test_delete_and_redownload import _service, _setup as _setup_upgrade, _video


async def _maker(tmp_path):
    eng = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'enq.db'}",
        connect_args={"timeout": 30},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u",
                     platform="youtube", health_status="unknown", quality="1080p")
        db.add(ch)
        await db.flush()
        vid = Video(channel_id=ch.id, video_id="v1", title="Vid", status="pending",
                    season=1, episode=1, upload_date=date(2025, 1, 1))
        db.add(vid)
        await db.commit()
        ids = (vid.id, ch.id)
    return maker, ids


async def _row_count(maker, video_id):
    async with maker() as db:
        return await db.scalar(
            select(func.count(DownloadQueue.id)).where(DownloadQueue.video_id == video_id)
        )


async def test_enqueue_video_reuses_row(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid)
        first = await enqueue_video(db, video, priority=2)
        await db.commit()
        assert video.status == "queued"
        first_id = first.id

    async with maker() as db:
        video = await db.get(Video, vid)
        entry = await db.get(DownloadQueue, first_id)
        entry.retry_after = datetime(2030, 1, 1)
        entry.quality_attempts = 2
        video.status = "failed"
        video.error_code = "X"
        again = await enqueue_video(db, video, priority=1, skip_min_quality=True)
        await db.commit()
        assert again.id == first_id
        assert again.priority == 2  # never lowered
        assert again.retry_after is None and again.quality_attempts == 0
        assert again.skip_min_quality is True
        assert video.status == "queued" and video.error_code is None

    assert await _row_count(maker, vid) == 1


async def test_enqueue_leaves_running_row_running(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid)
        entry = await enqueue_video(db, video)
        entry.started_at = datetime(2026, 1, 1)
        video.status = "downloading"
        await db.commit()
        await enqueue_video(db, video, priority=5)
        await db.commit()
        assert video.status == "downloading"
        assert entry.priority == 5


async def test_two_concurrent_queue_adds_make_one_row(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)

    async def _call():
        async with maker() as db:
            try:
                return await downloads.add_to_queue(QueueAdd(video_id=vid), db)
            except HTTPException as e:
                return e.status_code

    results = await asyncio.gather(_call(), _call())
    assert all(r in (409, {"message": "Video added to queue"}) for r in results)
    assert await _row_count(maker, vid) == 1


async def test_two_concurrent_enqueues_make_one_row(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)

    async def _call():
        async with maker() as db:
            video = await db.get(Video, vid)
            await enqueue_video(db, video)
            await db.commit()

    await asyncio.gather(_call(), _call())
    assert await _row_count(maker, vid) == 1


async def test_remove_refuses_running_row(monkeypatch, tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)
    monkeypatch.setattr(ds, "_active_downloads", {"v1"})
    async with maker() as db:
        video = await db.get(Video, vid)
        entry = await enqueue_video(db, video)
        entry.started_at = datetime(2026, 1, 1)
        video.status = "downloading"
        await db.commit()
        entry_id = entry.id

    async with maker() as db:
        with pytest.raises(HTTPException) as exc:
            await downloads.remove_from_queue(entry_id, db)
        assert exc.value.status_code == 409

    async with maker() as db:
        out = await downloads.bulk_remove_from_queue(
            downloads.BulkQueueRemove(queue_ids=[entry_id]), db)
        assert out["removed"] == 0

    assert await _row_count(maker, vid) == 1
    async with maker() as db:
        assert (await db.get(Video, vid)).status == "downloading"


async def test_retryable_failure_with_row_gone_is_not_queued(monkeypatch, tmp_path):
    maker, (vid, ch) = await _maker(tmp_path)
    monkeypatch.setattr(ds, "async_session", maker)
    monkeypatch.setattr(database, "async_session", maker)

    async def _no_broadcast(self, *a, **k):
        return None

    monkeypatch.setattr(ds.NotificationService, "broadcast", _no_broadcast)
    async with maker() as db:
        video = await db.get(Video, vid)
        video.status = "downloading"
        await db.commit()

    vdata = ds._VideoData(pk=vid, video_id="v1", title="Vid")
    # Queue row id that no longer exists (removed while yt-dlp was running)
    await ds.DownloadService()._record_failure(
        vid, ch, 9999, Exception("Connection reset by peer"), vdata)

    async with maker() as db:
        video = await db.get(Video, vid)
        assert video.status != "queued"
    assert await _row_count(maker, vid) == 0


async def test_failed_upgrade_returns_to_completed_when_file_exists(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup_upgrade(monkeypatch, tmp_path)
    async with maker() as db:
        q = await db.get(DownloadQueue, q_id)
        q.target_quality = "1080p"
        await db.commit()
    svc = _service(monkeypatch, succeed=False, error="Video unavailable")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert v.file_path == str(old)
    assert old.exists()


_ROW_CREATION = re.compile(
    r"\bDownloadQueue\(|insert\s*\(\s*DownloadQueue|DownloadQueue\.__table__\.insert"
    r"|INSERT\s+(OR\s+\w+\s+)?INTO\s+download_queue",
    re.IGNORECASE,
)


def test_only_the_enqueue_functions_create_queue_rows():
    app_dir = pathlib.Path(__file__).resolve().parent.parent / "app"
    service = app_dir / "services" / "download_service.py"
    tree = ast.parse(service.read_text(encoding="utf-8"))
    allowed = [
        (n.lineno, n.end_lineno) for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name in ("enqueue_video", "enqueue_videos")
    ]
    assert len(allowed) == 2

    stray = []
    for path in app_dir.rglob("*.py"):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if line.lstrip().startswith("class ") or not _ROW_CREATION.search(line):
                continue
            if path == service and any(a <= n <= b for a, b in allowed):
                continue
            stray.append((path.name, n))
    assert stray == [], stray


async def test_race_loser_applies_the_callers_options(tmp_path, monkeypatch):
    maker, (vid, _ch) = await _maker(tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid)
        real_flush = db.flush
        state = {"done": False}

        async def _flush():
            if not state["done"]:
                state["done"] = True
                async with maker() as other:  # the winner commits its row first
                    await enqueue_video(other, await other.get(Video, vid), priority=1)
                    await other.commit()
            await real_flush()

        monkeypatch.setattr(db, "flush", _flush)
        entry = await enqueue_video(
            db, video, priority=4, target_quality="720p", skip_min_quality=True)
        await db.commit()
        assert entry.priority == 4
        assert entry.skip_min_quality is True
        assert entry.target_quality == "720p"
        assert video.status == "queued"
    assert await _row_count(maker, vid) == 1


async def test_running_row_only_gets_the_priority(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid)
        entry = await enqueue_video(db, video, priority=1, target_quality="1080p")
        entry.started_at = datetime(2026, 1, 1)
        entry.retry_after = datetime(2030, 1, 1)
        entry.quality_attempts = 2
        video.status = "downloading"
        video.error_code = "X"
        await db.commit()

        await enqueue_video(db, video, priority=3, target_quality="720p", skip_min_quality=True)
        await db.commit()
        assert entry.priority == 3
        assert entry.target_quality == "1080p"
        assert entry.skip_min_quality is False
        assert entry.quality_attempts == 2
        assert entry.retry_after == datetime(2030, 1, 1)
        assert video.status == "downloading" and video.error_code == "X"


async def test_rollback_leaves_no_row_and_video_unchanged(tmp_path):
    maker, (vid, _ch) = await _maker(tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid)
        await enqueue_video(db, video)
        await db.rollback()
    assert await _row_count(maker, vid) == 0
    async with maker() as db:
        assert (await db.get(Video, vid)).status == "pending"


async def test_skipped_video_stays_skipped_when_row_gone(monkeypatch, tmp_path):
    maker, (vid, ch) = await _maker(tmp_path)
    monkeypatch.setattr(ds, "async_session", maker)
    monkeypatch.setattr(database, "async_session", maker)

    async def _no_broadcast(self, *a, **k):
        return None

    monkeypatch.setattr(ds.NotificationService, "broadcast", _no_broadcast)
    async with maker() as db:
        (await db.get(Video, vid)).status = "skipped"
        await db.commit()
    vdata = ds._VideoData(pk=vid, video_id="v1", title="Vid")
    await ds.DownloadService()._record_failure(
        vid, ch, 9999, Exception("Connection reset by peer"), vdata)
    async with maker() as db:
        assert (await db.get(Video, vid)).status == "skipped"


async def test_bulk_enqueue_is_fast_and_makes_no_duplicates(tmp_path):
    maker, (vid, ch) = await _maker(tmp_path)
    async with maker() as db:
        db.add_all([
            Video(channel_id=ch, video_id=f"b{i}", title="t", status="failed",
                  error_code="X", season=1, episode=i + 10, upload_date=date(2025, 1, 1))
            for i in range(5000)
        ])
        await db.commit()
    async with maker() as db:
        videos = (await db.execute(select(Video).where(Video.status == "failed"))).scalars().all()
        assert len(videos) == 5000
        # One already has a row, one is running: neither may be duplicated or disturbed
        await enqueue_video(db, videos[0])
        running = await enqueue_video(db, videos[1])
        running.started_at = datetime(2026, 1, 1)
        videos[1].status = "downloading"
        await db.commit()

        started = time.monotonic()
        created = await enqueue_videos(db, videos, priority=2)
        await db.commit()
        assert time.monotonic() - started < 10

    assert created == 4998
    async with maker() as db:
        assert await db.scalar(select(func.count(DownloadQueue.id))) == 5000
        assert await db.scalar(
            select(func.count(DownloadQueue.video_id.distinct()))) == 5000
        assert await db.scalar(
            select(func.count(Video.id)).where(Video.status == "queued")) == 4999
        row = (await db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == videos[1].id))).scalar_one()
        assert row.priority == 2 and row.started_at is not None
        assert (await db.get(Video, videos[1].id)).status == "downloading"
