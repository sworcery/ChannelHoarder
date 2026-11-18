import os
from datetime import date

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.services.channel_service import ChannelService, pick_channel_folder
from app.services.naming_service import build_output_path
from app.utils.file_utils import channel_folder_name


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


async def _add(db, cid, name, platform, folder_name=None):
    ch = Channel(channel_id=cid, channel_name=name, channel_url="u", platform=platform,
                 health_status="unknown", folder_name=folder_name)
    db.add(ch)
    await db.commit()
    return ch


def _path(ch, root):
    return build_output_path(
        channel_name=ch.channel_name, video_title="T", video_id="vid12345678",
        upload_date=date(2025, 1, 1), season=2025, episode=1,
        base_dir=str(root), folder_name=channel_folder_name(ch),
    )


async def test_same_name_on_two_platforms_get_separate_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    async with maker() as db:
        f1 = await pick_channel_folder(db, "Creator", "youtube", None)
        a = await _add(db, "yt1", "Creator", "youtube", f1)
        f2 = await pick_channel_folder(db, "Creator", "rumble", None)
        b = await _add(db, "ru1", "Creator", "rumble", f2)
        f3 = await pick_channel_folder(db, "Creator", "rumble", None)
        c = await _add(db, "ru2", "Creator", "rumble", f3)
        # A case-only difference is the same folder on a case-insensitive disk
        f4 = await pick_channel_folder(db, "creator", "odysee", None)
    await eng.dispose()

    assert f1 == "Creator"
    assert f2 == "Creator (Rumble)"
    assert f3 == "Creator (Rumble 2)"
    assert f4 == "creator (Odysee)"
    pa, pb, pc = (_path(x, tmp_path) for x in (a, b, c))
    assert len({os.path.dirname(os.path.dirname(p)) for p in (pa, pb, pc)}) == 3
    assert "Creator (Rumble)" in pb
    # The file name keeps the plain display name
    assert os.path.basename(pb).startswith("S2025E001")
    assert os.path.basename(
        build_output_path("Creator", "T", "v", date(2025, 1, 1), 1, 1,
                          naming_template="{channel_name}/{channel_name} - {title}",
                          base_dir=str(tmp_path), folder_name="Creator (Rumble)")
    ) == "Creator - T"


async def test_unique_channel_keeps_old_path(tmp_path):
    ch = Channel(channel_id="x", channel_name="Solo: Show", channel_url="u", platform="youtube")
    assert channel_folder_name(ch) == "Solo Show"
    old = build_output_path("Solo: Show", "T", "vid12345678", date(2025, 1, 1), 2025, 1,
                            base_dir=str(tmp_path))
    assert _path(ch, tmp_path) == old


async def test_delete_with_files_leaves_shared_folder_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    folder = tmp_path / "Creator"
    season = folder / "Season 2025"
    season.mkdir(parents=True)
    (folder / "tvshow.nfo").write_text("nfo")
    (season / "poster.jpg").write_text("poster")
    async with maker() as db:
        a = await _add(db, "yt1", "Creator", "youtube")
        b = await _add(db, "ru1", "Creator", "rumble")
        files = {}
        for ch, vid in ((a, "a1"), (b, "b1")):
            p = season / f"{vid}.mp4"
            p.write_bytes(vid.encode() * 10)
            (season / f"{vid}.nfo").write_text(vid)
            db.add(Video(channel_id=ch.id, video_id=vid, title=vid, status="completed",
                         file_path=str(p), season=2025, episode=1,
                         upload_date=date(2025, 1, 1)))
            files[vid] = p
        await db.commit()

        keep = {n: p.read_bytes() for n, p in (
            ("b.mp4", files["b1"]), ("b.nfo", season / "b1.nfo"),
            ("tvshow", folder / "tvshow.nfo"), ("poster", season / "poster.jpg"))}
        await ChannelService(db).delete_channel_files(a)

        assert not files["a1"].exists() and not (season / "a1.nfo").exists()
        assert files["b1"].read_bytes() == keep["b.mp4"]
        assert (season / "b1.nfo").read_bytes() == keep["b.nfo"]
        assert (folder / "tvshow.nfo").read_bytes() == keep["tvshow"]
        assert (season / "poster.jpg").read_bytes() == keep["poster"]

        # Last user of the folder: everything goes, including the empty folders
        await db.delete(a)
        await db.commit()
        await ChannelService(db).delete_channel_files(b)
    await eng.dispose()
    assert not folder.exists()


async def test_delete_keeps_untracked_files(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    folder = tmp_path / "Solo"
    folder.mkdir()
    (folder / "notes.txt").write_text("mine")
    async with maker() as db:
        ch = await _add(db, "s1", "Solo", "youtube")
        p = folder / "v.mp4"
        p.write_text("v")
        db.add(Video(channel_id=ch.id, video_id="v", title="v", status="completed",
                     file_path=str(p), season=1, episode=1, upload_date=date(2025, 1, 1)))
        await db.commit()
        await ChannelService(db).delete_channel_files(ch)
    await eng.dispose()
    assert (folder / "notes.txt").read_text() == "mine"
    assert not p.exists()


async def test_migration_adds_column_and_leaves_rows_null():
    eng = _engine()
    database_engine = database.engine
    database.engine = eng
    try:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            await _add(db, "a", "A", "youtube")
        async with eng.begin() as conn:
            await conn.execute(text("ALTER TABLE channels DROP COLUMN folder_name"))
        await database.init_database()
        async with eng.connect() as conn:
            cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(channels)"))).all()}
            rows = (await conn.execute(text("SELECT folder_name FROM channels"))).all()
        assert "folder_name" in cols
        assert rows == [(None,)]
    finally:
        database.engine = database_engine
        await eng.dispose()


async def test_long_name_gets_distinct_candidates_and_attempts_are_capped(monkeypatch, tmp_path):
    import pytest
    import app.services.channel_service as cs

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    long_name = "x" * 250
    async with maker() as db:
        f1 = await pick_channel_folder(db, long_name, "youtube", None)
        await _add(db, "l1", long_name, "youtube", f1)
        f2 = await pick_channel_folder(db, long_name, "rumble", None)
        await _add(db, "l2", long_name, "rumble", f2)
        f3 = await pick_channel_folder(db, long_name, "rumble", None)
        monkeypatch.setattr(cs, "_MAX_FOLDER_ATTEMPTS", 1)
        with pytest.raises(ValueError):
            await pick_channel_folder(db, long_name, "rumble", None)
    await eng.dispose()
    assert len({f1, f2, f3}) == 3
    assert all(len(f) <= 200 for f in (f1, f2, f3))
    assert f2.endswith(" (Rumble)") and f3.endswith(" (Rumble 2)")


async def test_import_folder_name(tmp_path):
    from app.routers.settings import _import_folder_name

    from app.services.channel_service import _folder_key

    assert await _import_folder_name(None, {"channel_name": "A", "folder_name": "A (Rumble)"}, set()) == "A (Rumble)"
    assert await _import_folder_name(None, {"channel_name": "A: B"}, set()) == "A B"
    assert await _import_folder_name(None, {"folder_name": "../x"}, set()) == "x"
    # A different channel that was already installed lives in "A": pick a free folder.
    taken = {_folder_key(None, "A")}
    assert await _import_folder_name(
        None, {"channel_name": "A", "platform": "rumble"}, taken) == "A (Rumble)"


async def test_folder_holding_another_channels_files_is_taken(monkeypatch, tmp_path):
    # After a move that failed part-way, a channel's files can sit in a folder its
    # own setting doesn't point to yet. Nobody else may be given that folder.
    from app.services.channel_service import pick_channel_folder

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    stray = tmp_path / "Creator" / "Season 1"
    stray.mkdir(parents=True)
    (stray / "y1.mp4").write_text("y1")
    async with maker() as db:
        yt = await _add(db, "yt", "Creator", "youtube", "Creator")
        yt.download_dir = str(tmp_path / "elsewhere")  # its setting didn't move yet
        db.add(Video(channel_id=yt.id, video_id="y1", title="t", status="completed",
                     file_path=str(stray / "y1.mp4"), season=1, episode=1,
                     upload_date=date(2025, 1, 1)))
        await db.commit()
        assert await pick_channel_folder(db, "Creator", "rumble", None) == "Creator (Rumble)"
    await eng.dispose()


async def test_restoring_an_old_backup_keeps_shared_folder(monkeypatch, tmp_path):
    # Two channels from one old backup shared "Creator"; both must restore into it,
    # since that's where their files are.
    from app.routers.settings import _import_folder_name

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    pre_existing: set[str] = set()
    a = await _import_folder_name(None, {"channel_name": "Creator", "platform": "youtube"}, pre_existing)
    b = await _import_folder_name(None, {"channel_name": "Creator", "platform": "rumble"}, pre_existing)
    assert a == b == "Creator"


async def test_delete_skips_file_another_channel_records(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng, maker = await _maker()
    folder = tmp_path / "Creator"
    folder.mkdir()
    shared = folder / "shared.mp4"
    shared.write_text("s")
    async with maker() as db:
        a = await _add(db, "a", "Creator", "youtube", "Creator")
        b = await _add(db, "b", "Other", "rumble", "Other")
        for ch in (a, b):
            db.add(Video(channel_id=ch.id, video_id=f"v{ch.id}", title="t", status="completed",
                         file_path=str(shared), season=1, episode=1,
                         upload_date=date(2025, 1, 1)))
        await db.commit()
        await ChannelService(db).delete_channel_files(a)
    await eng.dispose()
    assert shared.read_text() == "s"


async def _two_creators(monkeypatch, tmp_path):
    from app.services.notification_service import NotificationService

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path / "default"))
    messages = []

    async def fake(event, data):
        messages.append(data.get("message"))

    monkeypatch.setattr(NotificationService, "broadcast", fake)
    eng, maker = await _maker()
    monkeypatch.setattr(database, "async_session", maker)
    ids = []
    async with maker() as db:
        for cid, platform, root in (("y", "youtube", "a"), ("r", "rumble", "b")):
            d = tmp_path / root / "Creator" / "Season 1"
            d.mkdir(parents=True)
            p = d / f"{cid}.mp4"
            p.write_text(cid)
            ch = Channel(channel_id=cid, channel_name="Creator", channel_url="u",
                         platform=platform, health_status="unknown",
                         download_dir=str(tmp_path / root))
            db.add(ch)
            await db.flush()
            db.add(Video(channel_id=ch.id, video_id=cid, title=cid, status="completed",
                         file_path=str(p), season=1, episode=1, upload_date=date(2025, 1, 1)))
            ids.append(ch.id)
        await db.commit()
    return eng, maker, ids, messages


async def test_single_move_does_not_merge_into_existing_folder(monkeypatch, tmp_path):
    import app.routers.channels as channels_router

    eng, maker, ids, messages = await _two_creators(monkeypatch, tmp_path)
    target = tmp_path / "c"
    await channels_router._move_channel_task(ids[0], str(target), str(tmp_path / "a"))
    await channels_router._move_channel_task(ids[1], str(target), str(tmp_path / "b"))
    async with maker() as db:
        second = await db.get(Channel, ids[1])
        first = await db.get(Channel, ids[0])
        name2, name1 = second.folder_name, first.folder_name
    await eng.dispose()
    assert name1 is None and name2 == "Creator (Rumble)"
    assert (target / "Creator" / "Season 1" / "y.mp4").read_text() == "y"
    assert (target / "Creator (Rumble)" / "Season 1" / "r.mp4").read_text() == "r"
    assert not (target / "Creator" / "Season 1" / "r.mp4").exists()
    assert "Creator (Rumble)" in messages[-1]


async def test_move_all_does_not_merge_same_named_channels(monkeypatch, tmp_path):
    import app.routers.channels as channels_router

    eng, maker, ids, messages = await _two_creators(monkeypatch, tmp_path)
    target = tmp_path / "c"
    await channels_router._move_all_task_inner(str(target), {})
    await eng.dispose()
    assert (target / "Creator" / "Season 1" / "y.mp4").read_text() == "y"
    assert (target / "Creator (Rumble)" / "Season 1" / "r.mp4").read_text() == "r"
    assert "Creator (Rumble)" in messages[-1]


async def test_fresh_create_all_has_column():
    eng, _ = await _maker()
    async with eng.connect() as conn:
        cols = {r[1] for r in (await conn.execute(text("PRAGMA table_info(channels)"))).all()}
    await eng.dispose()
    assert "folder_name" in cols
