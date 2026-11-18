import os
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
import app.routers.channels as channels_router
from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.utils.file_utils import move_video_files as real_move


async def _setup(monkeypatch, tmp_path, names=("a", "b")):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)

    root = tmp_path / "downloads"
    chan_dir = root / "Chan"
    (chan_dir / "Season 1").mkdir(parents=True)
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u",
                     platform="youtube", health_status="unknown")
        db.add(ch)
        await db.flush()
        for n in names:
            p = chan_dir / "Season 1" / f"{n}.mp4"
            p.write_text(n)
            db.add(Video(channel_id=ch.id, video_id=n, title=n, status="completed",
                         file_path=str(p), season=1, episode=1,
                         upload_date=date(2025, 1, 1)))
        await db.commit()
        cid = ch.id
    return maker, root, cid


async def _paths(maker):
    async with maker() as db:
        rows = (await db.execute(select(Video.video_id, Video.file_path))).all()
    return dict(rows)


async def test_failed_video_keeps_files_and_db_path(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    new_root = tmp_path / "other"

    def flaky(old, new, overwrite=False):
        if os.path.basename(old) == "a.mp4":
            raise OSError("disk full")
        return real_move(old, new, overwrite)

    monkeypatch.setattr(channels_router, "move_video_files", flaky)
    await channels_router._move_channel_task(cid, str(new_root), str(root))

    paths = await _paths(maker)
    old_a = root / "Chan" / "Season 1" / "a.mp4"
    assert old_a.read_text() == "a"
    assert paths["a"] == str(old_a)
    assert paths["b"] == str(new_root / "Chan" / "Season 1" / "b.mp4")
    assert os.path.exists(paths["b"])
    async with maker() as db:
        assert (await db.get(Channel, cid)).download_dir is None  # not switched on failure


async def test_move_all_trailing_slash_deletes_nothing(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    (root / "Chan" / "tvshow.nfo").write_text("x")

    await channels_router._move_all_task(str(root) + "/", {cid: str(root)})

    assert (root / "Chan" / "Season 1" / "a.mp4").read_text() == "a"
    assert (root / "Chan" / "Season 1" / "b.mp4").read_text() == "b"
    assert (root / "Chan" / "tvshow.nfo").exists()
    paths = await _paths(maker)
    assert paths["a"] == str(root / "Chan" / "Season 1" / "a.mp4")


async def test_metadata_moves_user_file_stays(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    new_root = tmp_path / "other"
    (root / "Chan" / "tvshow.nfo").write_text("nfo")
    (root / "Chan" / "poster.jpg").write_text("img")
    (root / "Chan" / "Season 1" / "poster.jpg").write_text("sp")
    (root / "Chan" / "notes.txt").write_text("mine")

    await channels_router._move_channel_task(cid, str(new_root), str(root))

    assert (new_root / "Chan" / "tvshow.nfo").read_text() == "nfo"
    assert (new_root / "Chan" / "poster.jpg").read_text() == "img"
    assert (new_root / "Chan" / "Season 1" / "poster.jpg").read_text() == "sp"
    assert (root / "Chan" / "notes.txt").read_text() == "mine"
    assert (root / "Chan").is_dir()
    assert not (root / "Chan" / "Season 1").exists()  # emptied folder removed
    assert not (root / "Chan" / "tvshow.nfo").exists()


async def test_clean_move_removes_empty_old_folders(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    new_root = tmp_path / "other"

    await channels_router._move_channel_task(cid, str(new_root), str(root))

    assert not (root / "Chan").exists()
    assert root.is_dir()
    paths = await _paths(maker)
    assert all(os.path.exists(p) and p.startswith(str(new_root)) for p in paths.values())
    async with maker() as db:
        assert (await db.get(Channel, cid)).download_dir == str(new_root)


def _capture_broadcasts(monkeypatch):
    from app.services.notification_service import NotificationService

    sent = []

    async def fake(event, payload):
        sent.append((event, payload))

    monkeypatch.setattr(NotificationService, "broadcast", staticmethod(fake))
    return sent


def _flaky_a(monkeypatch):
    def flaky(old, new, overwrite=False):
        if os.path.basename(old) == "a.mp4":
            raise OSError("disk full")
        return real_move(old, new, overwrite)

    monkeypatch.setattr(channels_router, "move_video_files", flaky)


async def test_move_all_failure_keeps_download_dir_and_reports(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    sent = _capture_broadcasts(monkeypatch)
    _flaky_a(monkeypatch)
    new_root = tmp_path / "other"

    await channels_router._move_all_task(str(new_root), {cid: str(root)})

    async with maker() as db:
        assert (await db.get(Channel, cid)).download_dir is None
    (event, payload), = sent
    assert event == "move_complete"
    assert payload["failed"] is True
    assert "FAILED" in payload["message"]
    assert "disk full" in payload["message"]
    assert "a.mp4" in payload["message"]
    assert (root / "Chan" / "Season 1" / "a.mp4").exists()


async def test_retry_after_partial_failure_heals(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    sent = _capture_broadcasts(monkeypatch)
    new_root = tmp_path / "other"
    _flaky_a(monkeypatch)
    await channels_router._move_channel_task(cid, str(new_root), str(root))
    assert sent[-1][1]["failed"] is True

    monkeypatch.setattr(channels_router, "move_video_files", real_move)
    await channels_router._move_channel_task(cid, str(new_root), str(root))

    paths = await _paths(maker)
    assert all(p.startswith(str(new_root)) and os.path.exists(p) for p in paths.values())
    assert not (root / "Chan").exists()
    assert not sent[-1][1].get("failed")
    async with maker() as db:
        assert (await db.get(Channel, cid)).download_dir == str(new_root)


async def test_retry_heals_file_moved_but_not_recorded(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path, names=("a",))
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    _capture_broadcasts(monkeypatch)
    new_root = tmp_path / "other"
    old_dir = root / "Chan" / "Season 1"
    new_dir = new_root / "Chan" / "Season 1"
    new_dir.mkdir(parents=True)
    os.rename(old_dir / "a.mp4", new_dir / "a.mp4")  # moved, DB not updated
    (old_dir / "a.nfo").write_text("leftover")

    await channels_router._move_channel_task(cid, str(new_root), str(root))

    assert (await _paths(maker))["a"] == str(new_dir / "a.mp4")
    assert (new_dir / "a.nfo").read_text() == "leftover"
    assert not (root / "Chan").exists()


async def test_each_video_is_committed_right_after_its_move(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    new_root = tmp_path / "other"
    async with maker() as db:
        channel = await db.get(Channel, cid)
        real_commit = db.commit
        calls = {"n": 0}

        async def commit_once():
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("db locked")
            await real_commit()

        monkeypatch.setattr(db, "commit", commit_once)
        moved, errors, _left = await channels_router._move_channel_contents(
            db, channel, str(root), str(new_root)
        )

    assert moved == 1 and len(errors) == 1 and "db locked" in errors[0]
    paths = await _paths(maker)
    assert sum(p.startswith(str(new_root)) for p in paths.values()) == 1  # first one persisted

    # Retry heals the one whose file moved but whose path was not recorded.
    async with maker() as db:
        channel = await db.get(Channel, cid)
        moved, errors, _left = await channels_router._move_channel_contents(
            db, channel, str(root), str(new_root)
        )
    assert errors == []
    paths = await _paths(maker)
    assert all(p.startswith(str(new_root)) and os.path.exists(p) for p in paths.values())


def test_same_file_destination_is_never_deleted(tmp_path):
    from app.utils.file_utils import move_video_files

    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)  # two names for one folder, like overlapping bind mounts
    (real / "a.mp4").write_text("video")
    (real / "a.nfo").write_text("nfo")

    move_video_files(str(real / "a.mp4"), str(link / "a.mp4"), True)

    assert (real / "a.mp4").read_text() == "video"
    assert (real / "a.nfo").read_text() == "nfo"
    assert not any(p.name.endswith(".chmove-tmp") for p in real.iterdir())


def test_different_file_with_other_size_is_not_overwritten(tmp_path):
    import pytest
    from app.utils.file_utils import move_video_files

    (tmp_path / "src").mkdir()
    (tmp_path / "dst").mkdir()
    (tmp_path / "src" / "a.mp4").write_text("short")
    (tmp_path / "dst" / "a.mp4").write_text("a much longer different video")

    with pytest.raises(FileExistsError):
        move_video_files(str(tmp_path / "src" / "a.mp4"), str(tmp_path / "dst" / "a.mp4"), True)

    assert (tmp_path / "src" / "a.mp4").read_text() == "short"
    assert (tmp_path / "dst" / "a.mp4").read_text() == "a much longer different video"


async def test_move_all_to_alias_of_same_folder_deletes_nothing(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    _capture_broadcasts(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(root)

    await channels_router._move_all_task(str(alias), {cid: str(root)})

    assert (root / "Chan" / "Season 1" / "a.mp4").read_text() == "a"
    assert (root / "Chan" / "Season 1" / "b.mp4").read_text() == "b"
    assert all(os.path.exists(p) for p in (await _paths(maker)).values())


async def test_move_to_default_dir_with_trailing_slash_stores_none(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    other = tmp_path / "other"
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(other))
    _capture_broadcasts(monkeypatch)
    async with maker() as db:
        (await db.get(Channel, cid)).download_dir = str(root)
        await db.commit()

    await channels_router._move_channel_task(cid, str(other) + "/", str(root))

    async with maker() as db:
        assert (await db.get(Channel, cid)).download_dir is None


def _cross_device(monkeypatch, src_dir):
    """Make renames out of src_dir fail like a move between two bind mounts."""
    import errno

    real_replace = os.replace

    def fake(a, b, *args, **kw):
        if str(a).startswith(str(src_dir)):
            raise OSError(errno.EXDEV, "cross-device")
        return real_replace(a, b, *args, **kw)

    monkeypatch.setattr(os, "replace", fake)


def test_failed_cross_device_copy_leaves_no_partial_file_and_retry_works(monkeypatch, tmp_path):
    import errno
    import shutil

    from app.utils.file_utils import move_video_files

    src_dir, dst_dir = tmp_path / "src", tmp_path / "dst"
    src_dir.mkdir()
    dst_dir.mkdir()
    (src_dir / "a.mp4").write_bytes(b"x" * 1000)
    _cross_device(monkeypatch, src_dir)
    real_copy = shutil.copyfile

    def half_copy(s, d, *a, **kw):
        with open(d, "wb") as fh:
            fh.write(b"x" * 300)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", half_copy)
    try:
        move_video_files(str(src_dir / "a.mp4"), str(dst_dir / "a.mp4"), True)
    except OSError:
        pass
    assert not (dst_dir / "a.mp4").exists()
    assert not list(dst_dir.glob("*.chmove"))
    assert (src_dir / "a.mp4").stat().st_size == 1000

    monkeypatch.setattr(shutil, "copyfile", real_copy)
    move_video_files(str(src_dir / "a.mp4"), str(dst_dir / "a.mp4"), True)
    assert (dst_dir / "a.mp4").stat().st_size == 1000
    assert not (src_dir / "a.mp4").exists()


def test_hard_link_destination_keeps_the_data(tmp_path):
    from app.utils.file_utils import move_video_files

    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    src = tmp_path / "old" / "a.mp4"
    src.write_text("video")
    os.link(src, tmp_path / "new" / "a.mp4")

    move_video_files(str(src), str(tmp_path / "new" / "a.mp4"), True)

    assert (tmp_path / "new" / "a.mp4").read_text() == "video"


async def test_metadata_stays_when_a_video_fails(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    (root / "Chan" / "tvshow.nfo").write_text("show")
    (root / "Chan" / "poster.jpg").write_text("img")
    _capture_broadcasts(monkeypatch)
    _flaky_a(monkeypatch)
    new_root = tmp_path / "other"

    await channels_router._move_channel_task(cid, str(new_root), str(root))

    assert (root / "Chan" / "tvshow.nfo").read_text() == "show"
    assert (root / "Chan" / "poster.jpg").read_text() == "img"
    assert not (new_root / "Chan" / "tvshow.nfo").exists()


async def test_second_move_of_same_channel_is_refused(monkeypatch, tmp_path):
    import pytest
    from fastapi import HTTPException

    MoveFilesRequest = channels_router.MoveFilesRequest

    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    monkeypatch.setattr(settings, "EXTRA_DOWNLOAD_DIRS", str(tmp_path))
    monkeypatch.setattr(channels_router, "_moving_channels", {cid})
    monkeypatch.setattr("app.services.channel_service._moving_channels", {cid})
    async with maker() as db:
        with pytest.raises(HTTPException) as ei:
            await channels_router.move_channel_files(
                cid, MoveFilesRequest(new_download_dir=str(tmp_path / "other")), db)
        assert ei.value.status_code == 409
        with pytest.raises(HTTPException) as ei:
            await channels_router.move_all_channels(MoveFilesRequest(new_download_dir=str(tmp_path / "other")), db)
        assert ei.value.status_code == 409


async def test_move_to_same_folder_alias_tells_the_user(monkeypatch, tmp_path):
    maker, root, cid = await _setup(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    sent = _capture_broadcasts(monkeypatch)
    alias = tmp_path / "alias"
    alias.symlink_to(root)

    await channels_router._move_channel_task(cid, str(alias), str(root))

    assert sent and "already in that folder" in sent[-1][1]["message"]
    assert cid not in channels_router._moving_channels
