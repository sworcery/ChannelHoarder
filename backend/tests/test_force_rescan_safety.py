import asyncio
from datetime import date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.config import settings
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.routers import channels as channels_router
from app.routers.channels import (
    download_all_missing, force_rescan, monitor_season, MonitorRequest,
)
from app.services.channel_service import ChannelService, _scanning_channels
from app.services.ytdlp_service import YtdlpService

MARK = datetime(2026, 3, 1, 12, 0)


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def _maker():
    eng = _engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    _scanning_channels.clear()

    async def _noop(*args, **kwargs):
        return 0

    monkeypatch.setattr(ChannelService, "_backfill_channel_art", _noop)
    monkeypatch.setattr(ChannelService, "_auto_import_existing", _noop)
    monkeypatch.setattr(ChannelService, "_rename_existing_files", _noop)
    monkeypatch.setattr(YtdlpService, "get_rss_upload_dates", lambda self, *a, **k: {})
    yield
    _scanning_channels.clear()


def _listing(monkeypatch, entries):
    async def fake(self, channel):
        return [dict(e) for e in entries]

    monkeypatch.setattr(ChannelService, "fetch_video_list", fake)


def _entry(vid):
    return {"id": vid, "title": f"Video {vid}", "upload_date": "20260901", "duration": 600}


async def _seed(db, tmp_path):
    ch = Channel(
        channel_id="UC_x", channel_name="Chan", channel_url="https://www.youtube.com/@x",
        platform="youtube", auto_download=False, health_status="unknown",
        download_dir=str(tmp_path), thumbnail_url="https://example.com/a.jpg",
    )
    db.add(ch)
    await db.flush()
    db.add(Video(
        channel_id=ch.id, video_id="removed_vid1", title="Old", status="skipped",
        monitored=False, upload_date=date(2026, 1, 1), season=2026, episode=1,
        downloaded_at=MARK, retention_removed_at=MARK,
    ))
    db.add(Video(
        channel_id=ch.id, video_id="kept_video01", title="Kept", status="pending",
        upload_date=date(2026, 2, 1), season=2026, episode=2,
    ))
    await db.commit()
    return ch


async def _rows(db, channel_id):
    res = await db.execute(select(Video).where(Video.channel_id == channel_id))
    return {v.video_id: v for v in res.scalars().all()}


async def test_empty_listing_changes_nothing(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, [])
            with pytest.raises(HTTPException) as exc:
                await force_rescan(ch.id, db)
            assert exc.value.status_code == 502
            rows = await _rows(db, ch.id)
            assert set(rows) == {"removed_vid1", "kept_video01"}
            assert rows["removed_vid1"].status == "skipped"
            assert rows["removed_vid1"].retention_removed_at == MARK
            assert rows["kept_video01"].status == "pending"
            assert ch.id not in _scanning_channels
    finally:
        await eng.dispose()


async def test_failed_listing_changes_nothing(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)

            async def boom(self, channel):
                raise RuntimeError("network down")

            monkeypatch.setattr(ChannelService, "fetch_video_list", boom)
            with pytest.raises(HTTPException) as exc:
                await force_rescan(ch.id, db)
            assert exc.value.status_code == 502
            assert len(await _rows(db, ch.id)) == 2
    finally:
        await eng.dispose()


async def test_running_scan_gets_409(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, [_entry("removed_vid1")])
            _scanning_channels.add(ch.id)
            with pytest.raises(HTTPException) as exc:
                await force_rescan(ch.id, db)
            assert exc.value.status_code == 409
            assert len(await _rows(db, ch.id)) == 2
            # The other scan's lock is not ours to release
            assert ch.id in _scanning_channels
    finally:
        await eng.dispose()


async def test_channel_marked_scanning_during_run(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            seen = []

            async def fake(self, channel):
                seen.append(channel.id in _scanning_channels)
                return [_entry("removed_vid1")]

            monkeypatch.setattr(ChannelService, "fetch_video_list", fake)
            await force_rescan(ch.id, db)
            assert seen == [True]
            assert ch.id not in _scanning_channels
    finally:
        await eng.dispose()


async def test_two_rescans_keep_retention_removed_skipped(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            _listing(monkeypatch, [_entry("removed_vid1"), _entry("kept_video01"), _entry("new_video001")])
            for _ in range(2):
                await force_rescan(ch.id, db)
                rows = await _rows(db, ch.id)
                gone = rows["removed_vid1"]
                assert gone.status == "skipped"
                assert gone.monitored is False
                assert gone.retention_removed_at == MARK
                queued = (await db.execute(
                    select(DownloadQueue.video_id).where(DownloadQueue.video_id == gone.id)
                )).all()
                assert queued == []
                assert "new_video001" in rows
    finally:
        await eng.dispose()


async def test_season_monitor_leaves_marked_video(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            await monitor_season(ch.id, 2026, MonitorRequest(monitored=True), db)
            await download_all_missing(db)
            rows = await _rows(db, ch.id)
            gone = rows["removed_vid1"]
            assert gone.monitored is False
            assert gone.status == "skipped"
            assert rows["kept_video01"].monitored is True
            assert rows["kept_video01"].status == "queued"
    finally:
        await eng.dispose()


async def test_user_queue_clears_marker(tmp_path):
    from app.routers.downloads import QueueAdd, add_to_queue

    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            vid = (await _rows(db, ch.id))["removed_vid1"]
            await add_to_queue(QueueAdd(video_id=vid.id), db)
            await db.refresh(vid)
            assert vid.retention_removed_at is None
            assert vid.status == "queued"
    finally:
        await eng.dispose()


async def _add_completed(db, ch, tmp_path, vid="done_vid001"):
    p = tmp_path / f"{vid}.mp4"
    p.write_bytes(b"x")
    # Dated after the seeded rows so its episode number is already in date order
    # (Force Rescan renumbers the channel, renaming files only where numbers change).
    db.add(Video(
        channel_id=ch.id, video_id=vid, title="Done", status="completed",
        upload_date=date(2026, 3, 5), season=2026, episode=3,
        file_path=str(p), file_size=1, downloaded_at=MARK,
    ))
    await db.commit()
    return str(p)


async def test_removed_video_never_queued_with_auto_download(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            ch.auto_download = True
            await db.commit()
            _listing(monkeypatch, [_entry("removed_vid1"), _entry("kept_video01"), _entry("new_video001")])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            queued_ids = set((await db.execute(select(DownloadQueue.video_id))).scalars().all())
            # The scan really does queue new videos here, but never the removed one
            assert rows["new_video001"].id in queued_ids
            assert rows["removed_vid1"].id not in queued_ids
            assert rows["removed_vid1"].status == "skipped"
            assert rows["removed_vid1"].retention_removed_at == MARK
    finally:
        await eng.dispose()


async def test_scan_failure_keeps_completed_and_marked_rows(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            path = await _add_completed(db, ch, tmp_path)
            _listing(monkeypatch, [_entry("removed_vid1"), _entry("done_vid001")])

            async def boom(self, channel, video_list=None):
                raise RuntimeError("scan blew up")

            monkeypatch.setattr(ChannelService, "_scan_channel_inner", boom)
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            assert rows["done_vid001"].status == "completed"
            assert rows["done_vid001"].file_path == path
            assert rows["done_vid001"].downloaded_at == MARK
            assert rows["removed_vid1"].status == "skipped"
            assert rows["removed_vid1"].retention_removed_at == MARK
            assert ch.id not in _scanning_channels
    finally:
        await eng.dispose()


async def test_partial_listing_keeps_completed_and_marked_rows(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            path = await _add_completed(db, ch, tmp_path)
            ch.auto_download = True
            await db.commit()
            # The listing omits the completed and the marked video
            _listing(monkeypatch, [_entry("new_video001")])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            assert rows["done_vid001"].status == "completed"
            assert rows["done_vid001"].file_path == path
            assert rows["removed_vid1"].status == "skipped"
            assert rows["removed_vid1"].retention_removed_at == MARK
            queued = (await db.execute(select(DownloadQueue.video_id))).scalars().all()
            assert rows["done_vid001"].id not in queued
            assert rows["removed_vid1"].id not in queued
    finally:
        await eng.dispose()


async def test_existing_file_on_record_is_never_replaced(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            path = await _add_completed(db, ch, tmp_path)
            other = tmp_path / "Chan" / "Other [done_vid001].mp4"
            other.parent.mkdir()
            other.write_bytes(b"other")
            _listing(monkeypatch, [_entry("done_vid001")])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            assert rows["done_vid001"].file_path == path
    finally:
        await eng.dispose()


async def test_rebuilt_episode_gets_its_own_number_and_no_file_is_renamed(monkeypatch, tmp_path):
    # Episodes 1-6 of one season, all downloaded except 3, which had failed. The
    # rebuilt row must come back as episode 3, not repeat a kept number.
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = Channel(channel_id="UC_y", channel_name="Chan", channel_url="https://www.youtube.com/@y",
                         platform="youtube", auto_download=False, health_status="unknown",
                         download_dir=str(tmp_path))
            db.add(ch)
            await db.flush()
            paths = {}
            for n in range(1, 7):
                vid = f"ep{n:02d}_video"
                if n == 3:
                    db.add(Video(channel_id=ch.id, video_id=vid, title=f"E{n}", status="failed",
                                 upload_date=date(2026, 1, n), season=2026, episode=n))
                    continue
                p = tmp_path / f"{vid}.mp4"
                p.write_bytes(b"x")
                paths[vid] = str(p)
                db.add(Video(channel_id=ch.id, video_id=vid, title=f"E{n}", status="completed",
                             upload_date=date(2026, 1, n), season=2026, episode=n,
                             file_path=str(p), file_size=1, downloaded_at=MARK))
            await db.commit()
            _listing(monkeypatch, [
                {"id": f"ep{n:02d}_video", "title": f"E{n}", "upload_date": f"202601{n:02d}", "duration": 600}
                for n in range(1, 7)
            ])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            episodes = [r.episode for r in rows.values() if r.season == 2026]
            assert sorted(episodes) == [1, 2, 3, 4, 5, 6]
            assert rows["ep03_video"].episode == 3
            for vid, p in paths.items():
                assert rows[vid].file_path == p
    finally:
        await eng.dispose()


async def test_user_skipped_and_unmonitored_rows_are_kept(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            db.add(Video(channel_id=ch.id, video_id="user_skipped", title="Skipped", status="skipped",
                         upload_date=date(2026, 1, 10), season=2026, episode=4))
            db.add(Video(channel_id=ch.id, video_id="user_unmonit", title="Unmonitored", status="pending",
                         monitored=False, upload_date=date(2026, 1, 11), season=2026, episode=5))
            await db.commit()
            ids_before = {v.video_id: v.id for v in (await _rows(db, ch.id)).values()}
            ch.auto_download = True
            await db.commit()
            _listing(monkeypatch, [_entry("user_skipped"), _entry("user_unmonit")])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            assert rows["user_skipped"].id == ids_before["user_skipped"]
            assert rows["user_skipped"].status == "skipped"
            assert rows["user_unmonit"].id == ids_before["user_unmonit"]
            assert rows["user_unmonit"].monitored is False
            queued = (await db.execute(select(DownloadQueue.video_id))).scalars().all()
            assert rows["user_skipped"].id not in queued and rows["user_unmonit"].id not in queued
    finally:
        await eng.dispose()


async def test_missing_recorded_file_is_relinked_by_id(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            db.add(Video(channel_id=ch.id, video_id="moved_vid01", title="Moved", status="completed",
                         upload_date=date(2026, 1, 20), season=2026, episode=6,
                         file_path=str(tmp_path / "gone.mp4"), file_size=1, downloaded_at=MARK))
            await db.commit()
            found = tmp_path / "Chan" / "Moved [moved_vid01].mp4"
            found.parent.mkdir()
            found.write_bytes(b"x")
            _listing(monkeypatch, [_entry("moved_vid01")])
            await force_rescan(ch.id, db)
            rows = await _rows(db, ch.id)
            assert rows["moved_vid01"].file_path.endswith("[moved_vid01].mp4")
            assert rows["moved_vid01"].status == "completed"
    finally:
        await eng.dispose()


async def test_retention_task_sets_marker(monkeypatch, tmp_path):
    from datetime import timedelta
    from app.tasks import retention

    eng, maker = await _maker()
    monkeypatch.setattr(retention, "async_session", maker)
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            ch.retention_days = 30
            p = tmp_path / "old.mp4"
            p.write_bytes(b"x")
            db.add(Video(
                channel_id=ch.id, video_id="old_video01", title="o", status="completed",
                upload_date=date(2020, 1, 1), season=2020, episode=1, file_path=str(p),
                downloaded_at=datetime.utcnow() - timedelta(days=90),
            ))
            await db.commit()
        await retention.apply_retention()
        async with maker() as db:
            row = (await _rows(db, ch.id))["old_video01"]
        assert row.status == "skipped"
        assert row.retention_removed_at is not None
    finally:
        await eng.dispose()


async def test_user_actions_clear_marker(tmp_path):
    from app.routers.channels import (
        BulkMonitorRequest, BulkVideoAction, bulk_monitor_videos, bulk_queue_videos,
        bulk_unskip_videos, redownload_video, toggle_video_monitored,
    )
    from app.routers.downloads import retry_download

    async def marked(db, ch, vid):
        db.add(Video(
            channel_id=ch.id, video_id=vid, title=vid, status="skipped", monitored=False,
            upload_date=date(2026, 1, 1), season=2026, episode=1,
            downloaded_at=MARK, retention_removed_at=MARK,
        ))
        await db.commit()
        return (await _rows(db, ch.id))[vid]

    eng, maker = await _maker()
    try:
        async with maker() as db:
            ch = await _seed(db, tmp_path)
            actions = {
                "bulk_queue": lambda v: bulk_queue_videos(ch.id, BulkVideoAction(video_ids=[v.id]), db),
                "redownload": lambda v: redownload_video(ch.id, v.id, db),
                "unskip": lambda v: bulk_unskip_videos(ch.id, BulkVideoAction(video_ids=[v.id]), db),
                "monitor": lambda v: toggle_video_monitored(ch.id, v.id, MonitorRequest(monitored=True), db),
                "bulk_monitor": lambda v: bulk_monitor_videos(
                    ch.id, BulkMonitorRequest(video_ids=[v.id], monitored=True), db),
                "retry": lambda v: retry_download(v.id, db),
            }
            for name, act in actions.items():
                v = await marked(db, ch, f"m_{name}_0001")
                await act(v)
                await db.refresh(v)
                assert v.retention_removed_at is None, name
    finally:
        await eng.dispose()


async def test_successful_download_clears_marker(monkeypatch, tmp_path):
    from tests.test_delete_and_redownload import _service, _setup

    maker, (vid_id, ch_id, q_id), _old = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        v = await db.get(Video, vid_id)
        v.retention_removed_at = MARK
        await db.commit()
    svc = _service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    async with maker() as db:
        v = await db.get(Video, vid_id)
    assert v.status == "completed"
    assert v.retention_removed_at is None


def test_migration_adds_and_backfills_retention_marker(monkeypatch):
    async def run():
        eng = _engine()
        monkeypatch.setattr(database, "engine", eng)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("ALTER TABLE videos DROP COLUMN retention_removed_at"))
            await conn.execute(text(
                "INSERT INTO channels (id, channel_id, channel_name, channel_url, platform, "
                "quality, title_filter_is_regex, title_filter_mode, enabled, include_shorts, "
                "include_livestreams, auto_download, total_videos, downloaded_count, health_status, "
                "prioritize_downloads, retention_days) "
                "VALUES (1, 'a', 'a', 'u', 'youtube', 'best', 0, 'include', 1, 0, 0, 1, 0, 0, 'unknown', 0, 30), "
                "(2, 'b', 'b', 'u2', 'youtube', 'best', 0, 'include', 1, 0, 0, 1, 0, 0, 'unknown', 0, NULL)"
            ))
            base = ("INSERT INTO videos (channel_id, video_id, title, status, monitored, is_short, "
                    "is_livestream, retry_count, discovered_at, downloaded_at, file_path, upload_date, season, episode) VALUES ")
            # old retention signature
            await conn.execute(text(base + "(1, 'ret', 't', 'skipped', 0, 0, 0, 0, '2026-01-01', '2026-02-02 10:00:00', NULL, '2026-01-01', 1, 1)"))
            # reclassified short and a channel without retention: not retention removals
            await conn.execute(text(base + "(1, 'short', 't', 'skipped', 0, 1, 0, 0, '2026-01-01', '2026-02-02 10:00:00', NULL, '2026-01-01', 1, 1)"))
            await conn.execute(text(base + "(2, 'noret', 't', 'skipped', 0, 0, 0, 0, '2026-01-01', '2026-02-02 10:00:00', NULL, '2026-01-01', 1, 1)"))
            # skipped by the user, never downloaded
            await conn.execute(text(base + "(1, 'usr', 't', 'skipped', 0, 0, 0, 0, '2026-01-01', NULL, NULL, '2026-01-01', 1, 1)"))
            # completed with a file
            await conn.execute(text(base + "(1, 'ok', 't', 'completed', 1, 0, 0, 0, '2026-01-01', '2026-02-02 10:00:00', '/x.mp4', '2026-01-01', 1, 1)"))
        await database.init_database()
        await database.init_database()  # idempotent
        async with eng.connect() as conn:
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(videos)"))).all()}
            vals = dict((await conn.execute(text("SELECT video_id, retention_removed_at FROM videos"))).all())
        await eng.dispose()
        assert "retention_removed_at" in cols
        assert vals["ret"] == "2026-02-02 10:00:00"
        assert vals["usr"] is None
        assert vals["short"] is None
        assert vals["noret"] is None
        assert vals["ok"] is None
    asyncio.run(run())


def test_fresh_install_has_column(monkeypatch):
    async def run():
        eng = _engine()
        monkeypatch.setattr(database, "engine", eng)
        await database.init_database()
        async with eng.connect() as conn:
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(videos)"))).all()}
        await eng.dispose()
        assert "retention_removed_at" in cols
    asyncio.run(run())
