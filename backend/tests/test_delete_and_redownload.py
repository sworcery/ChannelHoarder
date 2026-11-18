import os
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.routers.channels as channels_router
import app.services.download_service as ds
from app.database import Base
from app.models import Channel, DownloadQueue, Video

OLD_BYTES = b"old-video-bytes"
NEW_BYTES = b"new-video-bytes"


async def _setup(monkeypatch, tmp_path, mode="other", old_ext=".mp4", old_sidecars=()):
    """mode: "other" = old file under a different name, "same" = at the new download's
    own path (stem), "alias" = same file recorded through a symlinked folder."""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(ds, "async_session", maker)
    import app.database as database
    monkeypatch.setattr(database, "async_session", maker)

    root = tmp_path / "downloads"
    root.mkdir()

    async def _no_settings():
        return {}

    import app.services.settings_service as ss
    monkeypatch.setattr(ss, "get_all_settings", _no_settings)

    async def _no_broadcast(self, *a, **k):
        return None

    monkeypatch.setattr(ds.NotificationService, "broadcast", _no_broadcast)

    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u",
                     platform="youtube", health_status="unknown",
                     download_dir=str(root), quality="1080p")
        db.add(ch)
        await db.flush()
        vid = Video(channel_id=ch.id, video_id="v1", title="Vid", status="queued",
                    season=1, episode=1, upload_date=date(2025, 1, 1))
        db.add(vid)
        await db.flush()

        from app.services.naming_service import resolve_naming_template
        template = await resolve_naming_template(db, None)
        stem = ds.build_output_path(
            channel_name="Chan", video_title="Vid", video_id="v1",
            upload_date=date(2025, 1, 1), season=1, episode=1,
            naming_template=template, base_dir=str(root),
        )
        if mode == "other":
            stem = str(root / "Chan" / "old name")
        os.makedirs(os.path.dirname(stem), exist_ok=True)
        old = stem + old_ext
        with open(old, "wb") as f:
            f.write(OLD_BYTES)
        for sc in old_sidecars:
            with open(stem + sc, "wb") as f:
                f.write(b"old-sidecar")
        recorded = old
        if mode == "alias":
            link = tmp_path / "link"
            os.symlink(root, link)
            recorded = str(link / os.path.relpath(old, root))
        vid.file_path = recorded
        vid.file_size = len(OLD_BYTES)
        vid.quality_downloaded = "480p"
        q = DownloadQueue(video_id=vid.id)
        db.add(q)
        await db.commit()
        ids = (vid.id, ch.id, q.id)
    return maker, ids, Path(old)


def _service(monkeypatch, succeed, error="Video unavailable", sidecars=(".nfo", "-thumb.jpg"),
             content=NEW_BYTES):
    async def _no_wait():
        return None

    monkeypatch.setattr(ds, "wait_for_rate_limit", _no_wait)
    real_sleep = ds.asyncio.sleep

    async def _fast_sleep(delay, *a, **k):
        await real_sleep(min(delay, 0.01), *a, **k)

    monkeypatch.setattr(ds.asyncio, "sleep", _fast_sleep)
    svc = ds.DownloadService()

    def fake_download(url, output_path, **kw):
        if not succeed:
            raise Exception(error)
        base = os.path.join(kw["temp_dir"], os.path.basename(output_path))
        with open(base + ".mp4", "wb") as f:
            f.write(content)
        for sc in sidecars:
            with open(base + sc, "wb") as f:
                f.write(b"new-sidecar")
        return {}

    monkeypatch.setattr(svc.ytdlp, "download_video", fake_download)
    return svc


async def _video(maker, vid_id):
    async with maker() as db:
        return await db.get(Video, vid_id)


@pytest.mark.asyncio
async def test_redownload_endpoint_keeps_file(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, _q), old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        v = await db.get(Video, vid_id)
        v.status = "completed"
        v.retry_count = 2
        await db.commit()
        await channels_router.redownload_video(ch_id, vid_id, db)
    v = await _video(maker, vid_id)
    assert v.status == "queued"
    assert v.retry_count == 0
    assert v.file_path == str(old)
    assert old.read_bytes() == OLD_BYTES


@pytest.mark.asyncio
async def test_failed_redownload_keeps_old_file(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    svc = _service(monkeypatch, succeed=False)
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert v.file_path == str(old)
    assert old.read_bytes() == OLD_BYTES


@pytest.mark.asyncio
async def test_retryable_failure_keeps_old_file_and_requeues(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    svc = _service(monkeypatch, succeed=False, error="HTTP Error 503: Service Unavailable")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status in ("queued", "completed")
    assert v.file_path == str(old)
    assert old.read_bytes() == OLD_BYTES


@pytest.mark.asyncio
async def test_system_error_does_not_mark_video_as_gone(monkeypatch, tmp_path):
    # A failure that's about the system (here: yt-dlp out of date), not the video,
    # must not leave the marker that makes the quality upgrade skip it for good.
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    svc = _service(monkeypatch, succeed=False,
                   error="ERROR: This version of yt-dlp is outdated, please update")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status == "completed" and v.file_path == str(old)
    assert v.error_code is None
    assert old.read_bytes() == OLD_BYTES


@pytest.mark.asyncio
async def test_successful_redownload_replaces_file(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    svc = _service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert v.file_path != str(old)
    assert open(v.file_path, "rb").read() == NEW_BYTES
    assert not old.exists()


@pytest.mark.asyncio
async def test_same_name_redownload_keeps_new_file_and_sidecars(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path, mode="same")
    svc = _service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    v = await _video(maker, vid_id)
    assert v.file_path == str(old)
    assert old.read_bytes() == NEW_BYTES
    stem = os.path.splitext(str(old))[0]
    assert os.path.exists(stem + ".nfo")
    assert os.path.exists(stem + "-thumb.jpg")


@pytest.mark.asyncio
async def test_mkv_to_mp4_keeps_new_sidecars(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(
        monkeypatch, tmp_path, mode="same", old_ext=".mkv", old_sidecars=(".nfo", "-thumb.jpg"))
    svc = _service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    v = await _video(maker, vid_id)
    assert v.file_path.endswith(".mp4")
    assert open(v.file_path, "rb").read() == NEW_BYTES
    assert not old.exists()
    stem = os.path.splitext(v.file_path)[0]
    assert os.path.exists(stem + ".nfo")
    with open(stem + "-thumb.jpg", "rb") as f:
        assert f.read() == b"new-sidecar"


@pytest.mark.asyncio
async def test_symlinked_alias_of_same_file_is_not_deleted(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), real = await _setup(monkeypatch, tmp_path, mode="alias")
    svc = _service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert os.path.exists(v.file_path)
    assert real.read_bytes() == NEW_BYTES
    assert os.path.exists(os.path.splitext(str(real))[0] + ".nfo")


@pytest.mark.asyncio
async def test_empty_new_download_keeps_old_file(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path, mode="same")
    svc = _service(monkeypatch, succeed=True, content=b"")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    assert old.read_bytes() == OLD_BYTES
    v = await _video(maker, vid_id)
    assert v.file_path == str(old)
    assert v.file_size == len(OLD_BYTES)


@pytest.mark.asyncio
async def test_failure_with_empty_old_file_is_not_completed(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    old.write_bytes(b"")
    svc = _service(monkeypatch, succeed=False, error="Video unavailable")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status == "skipped"


@pytest.mark.asyncio
async def test_failed_quality_upgrade_returns_to_completed(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        q = await db.get(DownloadQueue, q_id)
        q.target_quality = "1080p"
        await db.commit()
    # A permanent failure (video removed) must not strand the video as skipped/failed.
    svc = _service(monkeypatch, succeed=False, error="Video unavailable")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert v.file_path == str(old)
    assert old.exists()


@pytest.mark.asyncio
async def test_removed_upstream_upgrade_is_not_requeued(monkeypatch, tmp_path):
    import app.tasks.quality_upgrade as qu
    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(qu, "async_session", maker)
    broadcasts = []

    async def _record(self, event, data=None):
        broadcasts.append(event)

    monkeypatch.setattr(ds.NotificationService, "broadcast", _record)

    async with maker() as db:
        v = await db.get(Video, vid_id)
        ch = await db.get(Channel, ch_id)
        v.monitored = True
        ch.enabled = True
        ch.quality_cutoff = "1080p"
        q = await db.get(DownloadQueue, q_id)
        v.status = "queued"
        await db.commit()
        await db.refresh(q)

    svc = _service(monkeypatch, succeed=False, error="Video unavailable")
    assert await svc.download_video(vid_id, ch_id, q_id) is False
    assert "download_failed" not in broadcasts
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    assert v.error_code is not None

    await qu.check_quality_upgrades()
    v = await _video(maker, vid_id)
    assert v.status == "completed"
    async with maker() as db:
        from sqlalchemy import select
        rows = (await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == vid_id))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_removing_a_queued_redownload_keeps_it_downloaded(monkeypatch, tmp_path):
    # The re-download keeps its file while queued; taking it out of the queue (Remove,
    # bulk remove, Clear Queue, bulk Skip) must leave it downloaded, not pending/skipped.
    from sqlalchemy import select

    from app.routers import downloads as downloads_router

    for action in ("remove", "bulk_remove", "clear", "skip"):
        (tmp_path / action).mkdir()
        maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path / action)
        async with maker() as db:
            if action == "remove":
                await downloads_router.remove_from_queue(q_id, db)
            elif action == "bulk_remove":
                from app.schemas import BulkQueueRemove
                await downloads_router.bulk_remove_from_queue(BulkQueueRemove(queue_ids=[q_id]), db)
            elif action == "clear":
                await downloads_router.clear_queue(db)
            else:
                await channels_router.bulk_skip_videos(
                    ch_id, channels_router.BulkVideoAction(video_ids=[vid_id]), db)
        v = await _video(maker, vid_id)
        assert v.status == "completed", action
        assert v.file_path and old.read_bytes() == OLD_BYTES
        async with maker() as db:
            assert (await db.execute(select(DownloadQueue))).first() is None


@pytest.mark.asyncio
async def test_removing_an_ordinary_queued_video_makes_it_pending(monkeypatch, tmp_path):
    from app.routers import downloads as downloads_router

    maker, (vid_id, ch_id, q_id), old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        v = await db.get(Video, vid_id)
        v.file_path = None
        await db.commit()
        await downloads_router.remove_from_queue(q_id, db)
    assert (await _video(maker, vid_id)).status == "pending"
