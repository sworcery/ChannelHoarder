"""A file another channel's video also records (an old shared folder) survives every
delete and move path, and a manual short/livestream mark outlasts scans."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
import app.routers.channels as channels_router
from app.config import settings
from app.database import Base, init_database
from app.models import Channel, Video
from app.services.channel_service import ChannelService, _scanning_channels


async def _setup(monkeypatch, tmp_path):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path / "dl"))
    _scanning_channels.clear()

    folder = tmp_path / "dl" / "Chan" / "Season 1"
    folder.mkdir(parents=True)
    shared = folder / "shared.mp4"
    own = folder / "own.mp4"
    shared.write_text("shared")
    own.write_text("own")
    old = (datetime.now(timezone.utc) - timedelta(days=90)).replace(tzinfo=None)
    async with maker() as db:
        chans = []
        for cid in ("a", "b"):
            ch = Channel(channel_id=cid, channel_name="Chan", channel_url=f"u{cid}",
                         platform="youtube", health_status="unknown", retention_days=30)
            db.add(ch)
            chans.append(ch)
        await db.flush()
        a, b = chans

        def vid(ch, name, path):
            return Video(channel_id=ch.id, video_id=f"{ch.channel_id}_{name}", title=name,
                         status="completed", file_path=str(path), season=1, episode=1,
                         upload_date=date(2025, 1, 1), downloaded_at=old, duration=300)
        db.add_all([vid(a, "shared", shared), vid(a, "own", own), vid(b, "shared", shared)])
        await db.commit()
        ids = {"a": a.id, "b": b.id}
    return eng, maker, ids, shared, own


async def _get(maker, video_id):
    async with maker() as db:
        return (await db.execute(select(Video).where(Video.video_id == video_id))).scalar_one()


async def _vid_pk(maker, video_id):
    return (await _get(maker, video_id)).id


async def test_bulk_delete_keeps_shared_file(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        pks = [await _vid_pk(maker, "a_shared"), await _vid_pk(maker, "a_own")]
        body = channels_router.BulkDeleteRequest(video_ids=pks, delete_files=True)
        await channels_router._bulk_delete_videos(ids["a"], body, db)
    assert shared.exists() and not own.exists()
    for name in ("a_shared", "a_own"):
        v = await _get(maker, name)
        assert v.status == "skipped" and v.file_path is None
    assert (await _get(maker, "b_shared")).file_path == str(shared)
    await eng.dispose()


async def test_delete_video_with_files_keeps_shared(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        await channels_router._delete_video(ids["a"], await _vid_pk(maker, "a_shared"), True, db)
        await channels_router._delete_video(ids["a"], await _vid_pk(maker, "a_own"), True, db)
    assert shared.exists() and not own.exists()
    assert (await _get(maker, "a_shared")).file_path is None
    await eng.dispose()


async def test_delete_file_keeps_shared(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        await channels_router._delete_video_file(ids["a"], await _vid_pk(maker, "a_shared"), db)
        await channels_router._delete_video_file(ids["a"], await _vid_pk(maker, "a_own"), db)
    assert shared.exists() and not own.exists()
    v = await _get(maker, "a_shared")
    assert v.status == "pending" and v.file_path is None
    await eng.dispose()


async def test_retention_keeps_shared_file(monkeypatch, tmp_path):
    from app.tasks import retention
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(retention, "async_session", maker)
    async with maker() as db:
        ch = await db.get(Channel, ids["a"])
        await retention._apply_expired(db, ch)
    assert shared.exists() and not own.exists()
    v = await _get(maker, "a_shared")
    assert v.status == "skipped" and v.file_path is None and v.retention_removed_at
    assert (await _get(maker, "b_shared")).status == "completed"
    await eng.dispose()


async def test_move_keeps_shared_file(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    new_root = tmp_path / "other"
    await channels_router._move_channel_task(ids["a"], str(new_root), str(tmp_path / "dl"))
    assert shared.read_text() == "shared" and not own.exists()
    assert (new_root / "Chan" / "Season 1" / "own.mp4").read_text() == "own"
    assert (await _get(maker, "a_shared")).file_path == str(shared)
    assert (await _get(maker, "b_shared")).file_path == str(shared)
    await eng.dispose()


async def test_move_all_keeps_shared_file(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    new_root = tmp_path / "other"
    old = str(tmp_path / "dl")
    await channels_router._move_all_task(str(new_root), {ids["a"]: old, ids["b"]: old})
    # The shared file is recorded by both channels, so neither moves it
    assert shared.read_text() == "shared"
    assert (await _get(maker, "a_shared")).file_path == str(shared)
    assert (await _get(maker, "b_shared")).file_path == str(shared)
    assert not own.exists()
    await eng.dispose()


async def test_reclassify_keeps_downloaded_file_and_locked_flag(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    async with maker() as db:
        ch = await db.get(Channel, ids["a"])
        ch.include_shorts = False
        # a_own: user unmarked it (locked); a_shared: never touched, file on disk
        v_own = (await db.execute(select(Video).where(Video.video_id == "a_own"))).scalar_one()
        v_own.short_locked = True
        await db.commit()
        svc = ChannelService(db)
        listing = [{"id": "a_own", "_source_tab": "shorts"},
                   {"id": "a_shared", "_source_tab": "shorts"}]
        await svc._reclassify_existing_videos(ch, listing)
    locked = await _get(maker, "a_own")
    assert not locked.is_short and locked.status == "completed" and own.exists()
    flagged = await _get(maker, "a_shared")
    assert flagged.is_short and flagged.status == "completed" and shared.exists()
    await eng.dispose()


async def test_unmark_short_sticks_across_scan(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        ch = await db.get(Channel, ids["a"])
        ch.include_shorts = False
        v = (await db.execute(select(Video).where(Video.video_id == "a_own"))).scalar_one()
        v.is_short = True
        await db.commit()
        await channels_router.toggle_video_short(
            ids["a"], v.id, channels_router.ShortRequest(is_short=False), db)
        await ChannelService(db)._reclassify_existing_videos(
            ch, [{"id": "a_own", "_source_tab": "shorts"}])
    v = await _get(maker, "a_own")
    assert v.short_locked and not v.is_short and v.status == "completed" and own.exists()
    await eng.dispose()


async def test_bulk_reclassify_and_livestream_toggle_lock(monkeypatch, tmp_path):
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        pk = await _vid_pk(maker, "a_own")
        await channels_router.bulk_reclassify_videos(
            ids["a"], channels_router.BulkReclassifyRequest(video_ids=[pk], set_short=False), db)
        await channels_router.toggle_video_livestream(
            ids["a"], pk, channels_router.LivestreamRequest(is_livestream=False), db)
    v = await _get(maker, "a_own")
    assert v.short_locked and v.livestream_locked
    assert not (await _get(maker, "a_shared")).short_locked
    await eng.dispose()


async def test_locked_column_on_fresh_install_and_upgrade(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(database, "engine", eng)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_database()
    async with eng.connect() as conn:
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(videos)"))).fetchall()}
    assert {"short_locked", "livestream_locked"} <= cols

    # Upgrade: a database from before the columns existed
    async with eng.begin() as conn:
        await conn.execute(text("ALTER TABLE videos DROP COLUMN short_locked"))
        await conn.execute(text("ALTER TABLE videos DROP COLUMN livestream_locked"))
    await init_database()
    await init_database()
    async with eng.connect() as conn:
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(videos)"))).fetchall()}
    assert {"short_locked", "livestream_locked"} <= cols
    await eng.dispose()


async def _rename_and_retain(maker, ids):
    from app.tasks import retention
    async with maker() as db:
        ch = await db.get(Channel, ids["a"])
        await ChannelService(db)._rename_existing_files(ch)
        await retention._apply_expired(db, ch)


async def test_shared_file_stays_after_move_then_rename_and_retention(monkeypatch, tmp_path):
    from app.tasks import retention
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(retention, "async_session", maker)
    new_root = tmp_path / "other"
    await channels_router._move_channel_task(ids["a"], str(new_root), str(tmp_path / "dl"))
    async with maker() as db:
        assert (await db.get(Channel, ids["a"])).download_dir == str(new_root)
    await _rename_and_retain(maker, ids)
    assert shared.read_text() == "shared"
    assert (await _get(maker, "b_shared")).file_path == str(shared)
    assert (await _get(maker, "a_shared")).file_path in (str(shared), None)
    await eng.dispose()


async def test_shared_file_stays_after_move_all_then_rename_and_retention(monkeypatch, tmp_path):
    from app.tasks import retention
    eng, maker, ids, shared, own = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(retention, "async_session", maker)
    old = str(tmp_path / "dl")
    await channels_router._move_all_task(str(tmp_path / "other"), {ids["a"]: old, ids["b"]: old})
    await _rename_and_retain(maker, ids)
    assert shared.read_text() == "shared"
    assert (await _get(maker, "b_shared")).file_path == str(shared)
    await eng.dispose()
