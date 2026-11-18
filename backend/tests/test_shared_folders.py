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
from app.services import shared_folder_service as sfs
from app.utils.file_utils import move_video_files as real_move


async def _setup(monkeypatch, tmp_path):
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
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    season = root / "Chan" / "Season 1"
    season.mkdir(parents=True)
    (root / "Chan" / "tvshow.nfo").write_text("shared show")
    (root / "Chan" / "poster.jpg").write_text("shared poster")

    ids = {}
    async with maker() as db:
        for key, platform in (("a", "youtube"), ("b", "rumble")):
            ch = Channel(channel_id=f"c-{key}", channel_name="Chan", channel_url=f"u-{key}",
                         platform=platform, health_status="unknown")
            db.add(ch)
            await db.flush()
            ids[key] = ch.id
            for n in (1, 2, 3):
                name = f"{key}{n}"
                p = season / f"{name}.mp4"
                p.write_text(f"video {name}")
                (season / f"{name}.nfo").write_text(f"nfo {name}")
                db.add(Video(channel_id=ch.id, video_id=name, title=name, status="completed",
                             file_path=str(p), season=1, episode=n,
                             upload_date=date(2025, 1, 1)))
        await db.commit()
    return maker, root, ids


async def _paths(maker):
    async with maker() as db:
        rows = (await db.execute(select(Video.video_id, Video.file_path))).all()
    return dict(rows)


async def _snapshot(maker, root):
    files = {}
    for dp, _, fns in os.walk(root):
        for fn in fns:
            fp = os.path.join(dp, fn)
            with open(fp) as f:
                files[fp] = f.read()
    return files, await _paths(maker)


async def test_check_lists_shared_group(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        groups = await sfs.find_shared_groups(db)
    assert len(groups) == 1
    g = groups[0]
    assert {c["id"] for c in g["channels"]} == {ids["a"], ids["b"]}
    assert g["default_channel_id"] == ids["b"]


async def test_preview_changes_nothing(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    before = await _snapshot(maker, root)
    async with maker() as db:
        p = await sfs.preview_move(db, ids["b"])
    assert p["new_folder"] == "Chan (Rumble)"
    assert p["video_files"] == 3 and p["sidecar_files"] == 3
    assert p["staying_files"] == []
    assert await _snapshot(maker, root) == before
    async with maker() as db:
        ch = await db.get(Channel, ids["b"])
        assert ch.folder_name is None


async def test_move_separates_channels(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    await sfs.move_task(ids["b"])

    paths = await _paths(maker)
    for n in (1, 2, 3):
        a = root / "Chan" / "Season 1" / f"a{n}.mp4"
        b = root / "Chan (Rumble)" / "Season 1" / f"b{n}.mp4"
        assert paths[f"a{n}"] == str(a) and a.read_text() == f"video a{n}"
        assert paths[f"b{n}"] == str(b) and b.read_text() == f"video b{n}"
        assert (b.with_suffix(".nfo")).read_text() == f"nfo b{n}"
        assert (a.with_suffix(".nfo")).read_text() == f"nfo a{n}"
    async with maker() as db:
        assert (await db.get(Channel, ids["b"])).folder_name == "Chan (Rumble)"
        assert await sfs.find_shared_groups(db) == []
    # shared show files stay with the channel that keeps the folder
    assert (root / "Chan" / "poster.jpg").read_text() == "shared poster"
    assert (root / "Chan" / "tvshow.nfo").read_text() == "shared show"
    assert (root / "Chan (Rumble)" / "tvshow.nfo").exists()


async def test_failed_move_keeps_files_and_retry_finishes(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)

    def flaky(old, new, overwrite=False):
        if os.path.basename(old) == "b2.mp4":
            raise OSError("disk full")
        return real_move(old, new, overwrite)

    monkeypatch.setattr(channels_router, "move_video_files", flaky)
    await sfs.move_task(ids["b"])

    paths = await _paths(maker)
    old_b2 = root / "Chan" / "Season 1" / "b2.mp4"
    assert old_b2.read_text() == "video b2"
    assert paths["b2"] == str(old_b2)
    for k in ("b1", "b3"):
        assert os.path.exists(paths[k])
    async with maker() as db:
        assert (await db.get(Channel, ids["b"])).folder_name is None

    monkeypatch.setattr(channels_router, "move_video_files", real_move)
    await sfs.move_task(ids["b"])
    paths = await _paths(maker)
    for n in (1, 2, 3):
        b = root / "Chan (Rumble)" / "Season 1" / f"b{n}.mp4"
        assert paths[f"b{n}"] == str(b) and b.read_text() == f"video b{n}"
        assert not (root / "Chan" / "Season 1" / f"b{n}.mp4").exists()
    async with maker() as db:
        assert (await db.get(Channel, ids["b"])).folder_name == "Chan (Rumble)"


async def test_file_recorded_by_other_channel_never_moves(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    b3 = root / "Chan" / "Season 1" / "b3.mp4"
    async with maker() as db:
        db.add(Video(channel_id=ids["a"], video_id="dup", title="dup", status="completed",
                     file_path=str(b3), season=1, episode=9, upload_date=date(2025, 1, 1)))
        await db.commit()
        p = await sfs.preview_move(db, ids["b"])
    assert p["staying_files"] == [str(b3)]
    assert p["video_files"] == 2

    await sfs.move_task(ids["b"])
    paths = await _paths(maker)
    assert b3.read_text() == "video b3"
    assert paths["b3"] == str(b3) and paths["dup"] == str(b3)
    assert (root / "Chan (Rumble)" / "Season 1" / "b1.mp4").exists()
