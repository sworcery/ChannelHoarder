"""Release 2.0.0 checkpoint fixes: stale channel error codes, ENOSPC classification,
retry backoff, bulk skip vs running downloads, unavailable-title cleanup."""
import errno
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.routers.channels as channels_router
import app.services.channel_service as cs
import app.services.download_service as ds
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.tasks.cleanup_unavailable import cleanup_unavailable_videos
from app.utils.error_codes import ErrorCode, classify_error
from tests.test_delete_and_redownload import NEW_BYTES, _service, _setup, _video
from tests.test_download_failures import _FakeYtdlp, _setup as _fail_setup, _state


def test_enospc_beats_path_words():
    path = "/downloads/Network News/Sign in to Rumble/ep.mp4"
    exc = OSError(errno.ENOSPC, "x")
    assert classify_error(f"cannot write {path}", exc) == ErrorCode.DISK_FULL
    # wrapped by another exception
    try:
        try:
            raise exc
        except OSError as inner:
            raise Exception(f"move failed {path}") from inner
    except Exception as wrapped:
        assert classify_error(str(wrapped), wrapped) == ErrorCode.DISK_FULL
    # text only
    assert classify_error(f"[Errno 28] No space left on device: '{path}'") == ErrorCode.DISK_FULL
    assert classify_error("Sign in to confirm you're not a bot") == ErrorCode.AUTH_EXPIRED


def test_retry_backoff_values():
    m = timedelta(minutes=1)
    assert [ds.retry_delay(ErrorCode.NETWORK_ERROR, n) for n in (1, 2, 3, 4, 9)] == [
        2 * m, 10 * m, 30 * m, 60 * m, 60 * m]
    assert [ds.retry_delay(ErrorCode.RATE_LIMITED, n) for n in (1, 2, 3, 7)] == [
        15 * m, 60 * m, 180 * m, 180 * m]


async def test_automatic_retry_sets_retry_after_and_manual_retry_clears_it(monkeypatch, tmp_path):
    maker, service, ids = await _fail_setup(
        monkeypatch, tmp_path, ytdlp=_FakeYtdlp(download_error="Connection reset by peer"))
    before = datetime.now(timezone.utc).replace(tzinfo=None)
    assert await service.download_video(*ids) is False
    video, queue = await _state(maker, ids)
    assert video.status == "queued" and video.retry_count == 1
    assert queue.retry_after is not None
    assert timedelta(minutes=1) < queue.retry_after - before <= timedelta(minutes=3)

    async with maker() as db:
        video = await db.get(Video, ids[0])
        await ds.enqueue_video(db, video, reset_retries=True)
        await db.commit()
    _video_row, queue = await _state(maker, ids)
    assert queue.retry_after is None


async def test_success_clears_download_error_code_but_not_scan_failed(monkeypatch, tmp_path):
    for code, expect in (("NETWORK_ERROR", None), (ErrorCode.SCAN_FAILED.value, ErrorCode.SCAN_FAILED.value)):
        sub = tmp_path / code
        sub.mkdir()
        maker, (vid_id, ch_id, q_id), _old = await _setup(monkeypatch, sub)
        async with maker() as db:
            ch = await db.get(Channel, ch_id)
            ch.last_error_code = code
            ch.health_status = "warning" if code == "NETWORK_ERROR" else "unhealthy"
            await db.commit()
        svc = _service(monkeypatch, succeed=True)
        assert await svc.download_video(vid_id, ch_id, q_id) is True
        async with maker() as db:
            ch = await db.get(Channel, ch_id)
            assert ch.last_error_code == expect
            assert ch.health_status == ("healthy" if expect is None else "unhealthy")
        assert (await _video(maker, vid_id)).file_path


async def test_success_keeps_metadata_degraded(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), _old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        ch = await db.get(Channel, ch_id)
        ch.last_error_code = ErrorCode.METADATA_DEGRADED.value
        ch.health_status = "warning"
        await db.commit()
    assert await _service(monkeypatch, succeed=True).download_video(vid_id, ch_id, q_id) is True
    async with maker() as db:
        ch = await db.get(Channel, ch_id)
        assert ch.last_error_code == ErrorCode.METADATA_DEGRADED.value
        assert ch.health_status == "warning"


async def test_success_with_many_failed_videos_names_the_top_code(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), _old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        ch = await db.get(Channel, ch_id)
        ch.last_error_code = "NETWORK_ERROR"
        db.add_all([
            Video(channel_id=ch_id, video_id=f"f{i}", title="t", status="failed",
                  error_code="RATE_LIMITED" if i < 3 else "FFMPEG_ERROR",
                  season=1, episode=10 + i, upload_date=date(2025, 1, 1))
            for i in range(4)
        ])
        await db.commit()
    assert await _service(monkeypatch, succeed=True).download_video(vid_id, ch_id, q_id) is True
    async with maker() as db:
        ch = await db.get(Channel, ch_id)
        assert ch.last_error_code == "RATE_LIMITED"
        assert ch.health_status == "unhealthy"


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


@pytest.mark.parametrize("old,overwritten", [
    ("NETWORK_ERROR", True), ("RATE_LIMITED", True), (None, True),
    ("DISK_FULL", False), ("AUTH_EXPIRED", False),
    ("PO_TOKEN_FAILURE", False), ("YTDLP_OUTDATED", False),
])
async def test_scan_error_overrides_per_video_codes_only(maker, monkeypatch, old, overwritten):
    async def _no_broadcast(*a, **k):
        return None

    monkeypatch.setattr(cs.NotificationService, "broadcast", _no_broadcast)
    async with maker() as db:
        ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                     health_status="warning", last_error_code=old)
        db.add(ch)
        await db.commit()
        await cs.record_scan_error(db, ch, Exception("boom"))
        await db.refresh(ch)
        if overwritten:
            assert ch.last_error_code == ErrorCode.SCAN_FAILED.value
            assert ch.health_status == "unhealthy"
        else:
            assert ch.last_error_code == old


async def test_bulk_skip_leaves_running_download_alone(maker):
    async with maker() as db:
        ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube")
        db.add(ch)
        await db.flush()
        idle = Video(channel_id=ch.id, video_id="i", title="idle", status="queued",
                     season=1, episode=1, upload_date=date(2025, 1, 1))
        busy = Video(channel_id=ch.id, video_id="b", title="busy", status="downloading",
                     season=1, episode=2, upload_date=date(2025, 1, 1))
        pend = Video(channel_id=ch.id, video_id="p", title="pend", status="pending",
                     season=1, episode=3, upload_date=date(2025, 1, 1))
        db.add_all([idle, busy, pend])
        await db.flush()
        db.add(DownloadQueue(video_id=idle.id))
        db.add(DownloadQueue(video_id=busy.id, started_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        await db.commit()
        ids = [idle.id, busy.id, pend.id]
        ch_id = ch.id
    # status "downloading" is not in the skip filter, so mark the busy one queued with a fresh start
    async with maker() as db:
        (await db.get(Video, ids[1])).status = "queued"
        await db.commit()
    async with maker() as db:
        out = await channels_router.bulk_skip_videos(
            ch_id, channels_router.BulkVideoAction(video_ids=ids), db)
    assert out["skipped"] == 2 and out["running"] == 1
    async with maker() as db:
        assert (await db.get(Video, ids[0])).status == "skipped"
        assert (await db.get(Video, ids[2])).status == "skipped"
        assert (await db.get(Video, ids[1])).status == "queued"
        rows = (await db.execute(select(DownloadQueue.video_id))).scalars().all()
        assert rows == [ids[1]]


async def test_cleanup_unavailable_keeps_completed_rows(maker):
    async with maker() as db:
        ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube")
        db.add(ch)
        await db.flush()
        done = Video(channel_id=ch.id, video_id="d", title="[Private video]", status="completed",
                     season=1, episode=1, upload_date=date(2025, 1, 1))
        junk = Video(channel_id=ch.id, video_id="j", title="[Private video]", status="pending",
                     season=1, episode=2, upload_date=date(2025, 1, 1))
        db.add_all([done, junk])
        await db.commit()
        done_id = done.id
    async with maker() as db:
        assert await cleanup_unavailable_videos(db) == 1
        assert await db.get(Video, done_id) is not None
