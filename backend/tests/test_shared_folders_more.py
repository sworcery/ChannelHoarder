from datetime import date

import pytest
from fastapi import HTTPException

import app.routers.system as system_router
from app.models import Channel, Video
from app.services import channel_service, shared_folder_service as sfs
from app.services.channel_service import ChannelService, _moving_channels, _scanning_channels

from tests.test_shared_folders import _paths, _setup


@pytest.fixture(autouse=True)
def _clean_sets():
    yield
    _moving_channels.clear()
    _scanning_channels.clear()


async def _add_video(maker, channel_pk, vid, path=None, status="completed"):
    async with maker() as db:
        db.add(Video(channel_id=channel_pk, video_id=vid, title=vid, status=status,
                     file_path=str(path) if path else None, season=1, episode=1,
                     upload_date=date(2025, 1, 1)))
        await db.commit()


async def test_endpoints_and_409_guards(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    started = []

    async def fake_task(cid):
        started.append(cid)
        _moving_channels.discard(cid)

    monkeypatch.setattr(sfs, "move_task", fake_task)

    async with maker() as db:
        listing = await system_router.list_shared_folders(db)
        assert len(listing["groups"]) == 1
        prev = await system_router.preview_shared_folder_move(ids["b"], db)
        assert prev["new_folder"] == "Chan (Rumble)"

        body = system_router.SharedFolderMoveRequest(channel_id=ids["b"])

        _scanning_channels.add(ids["a"])
        with pytest.raises(HTTPException) as e:
            await system_router.move_shared_folder_channel(body, db)
        assert e.value.status_code == 409
        _scanning_channels.clear()

        _moving_channels.add(ids["b"])
        with pytest.raises(HTTPException) as e:
            await system_router.move_shared_folder_channel(body, db)
        assert e.value.status_code == 409
        _moving_channels.clear()

        b1 = await db.get(Video, 4)  # first video of channel b
        assert b1.channel_id == ids["b"]
        b1.status = "downloading"
        await db.commit()
        with pytest.raises(HTTPException) as e:
            await system_router.move_shared_folder_channel(body, db)
        assert e.value.status_code == 409
        b1.status = "completed"
        await db.commit()

        res = await system_router.move_shared_folder_channel(body, db)
        assert "Chan (Rumble)" in res["message"]
        with pytest.raises(HTTPException) as e:
            await system_router.preview_shared_folder_move(99999, db)
        assert e.value.status_code == 404
    import asyncio
    await asyncio.sleep(0)
    assert started == [ids["b"]]


async def test_third_channel(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        c = Channel(channel_id="c-c", channel_name="Chan", channel_url="u-c",
                    platform="twitch", health_status="unknown")
        db.add(c)
        await db.flush()
        ids["c"] = c.id
        await db.commit()
    p = root / "Chan" / "Season 1" / "c1.mp4"
    p.write_text("video c1")
    await _add_video(maker, ids["c"], "c1", p)

    async with maker() as db:
        groups = await sfs.find_shared_groups(db)
    assert len(groups[0]["channels"]) == 3
    assert groups[0]["default_channel_id"] == ids["c"]

    await sfs.move_task(ids["b"])
    async with maker() as db:
        groups = await sfs.find_shared_groups(db)
        assert {c["id"] for c in groups[0]["channels"]} == {ids["a"], ids["c"]}
    await sfs.move_task(ids["c"])
    paths = await _paths(maker)
    assert paths["c1"] == str(root / "Chan (Twitch)" / "Season 1" / "c1.mp4")
    assert (root / "Chan (Twitch)" / "Season 1" / "c1.mp4").read_text() == "video c1"
    assert paths["a1"] == str(root / "Chan" / "Season 1" / "a1.mp4")
    assert paths["b1"] == str(root / "Chan (Rumble)" / "Season 1" / "b1.mp4")


async def test_untracked_files(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    await _add_video(maker, ids["b"], "b9", None, status="pending")
    season = root / "Chan" / "Season 1"
    tagged = season / "Some title [b9].mp4"
    tagged.write_text("tagged")
    (season / "Some title [b9].nfo").write_text("tagged nfo")
    other = season / "Stranger.mp4"
    other.write_text("stranger")

    async with maker() as db:
        prev = await sfs.preview_move(db, ids["b"])
    assert prev["untracked_move"] == [str(tagged)]
    assert prev["untracked_stay"] == [str(other)]

    await sfs.move_task(ids["b"])
    new_season = root / "Chan (Rumble)" / "Season 1"
    assert (new_season / "Some title [b9].mp4").read_text() == "tagged"
    assert (new_season / "Some title [b9].nfo").read_text() == "tagged nfo"
    assert not tagged.exists()
    assert other.read_text() == "stranger"


async def test_same_stem_sidecars_stay(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    season = root / "Chan" / "Season 1"
    ax, bx = season / "x.webm", season / "x.mp4"
    ax.write_text("ax")
    bx.write_text("bx")
    (season / "x.nfo").write_text("shared nfo")
    await _add_video(maker, ids["a"], "ax", ax)
    await _add_video(maker, ids["b"], "bx", bx)

    async with maker() as db:
        prev = await sfs.preview_move(db, ids["b"])
    assert prev["shared_sidecar_files"] == [str(bx)]

    await sfs.move_task(ids["b"])
    new_bx = root / "Chan (Rumble)" / "Season 1" / "x.mp4"
    assert new_bx.read_text() == "bx" and not bx.exists()
    assert (season / "x.nfo").read_text() == "shared nfo"
    assert not (root / "Chan (Rumble)" / "Season 1" / "x.nfo").exists()
    assert ax.read_text() == "ax"


async def test_scan_rename_step_skips_file_other_channel_records(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)
    b3 = root / "Chan" / "Season 1" / "b3.mp4"
    await _add_video(maker, ids["a"], "dup", b3)

    async with maker() as db:
        channel = await db.get(Channel, ids["b"])
        await ChannelService(db)._rename_existing_files(channel)

    paths = await _paths(maker)
    assert paths["b3"] == str(b3) and b3.read_text() == "video b3"
    assert paths["dup"] == str(b3)
    assert paths["b1"] != str(root / "Chan" / "Season 1" / "b1.mp4")  # unprotected ones do rename


async def test_scan_skips_channel_being_moved(monkeypatch, tmp_path):
    maker, root, ids = await _setup(monkeypatch, tmp_path)

    async def boom(self, channel):
        raise AssertionError("scan must not run")

    monkeypatch.setattr(ChannelService, "_scan_channel_inner", boom)
    _moving_channels.add(ids["b"])
    async with maker() as db:
        channel = await db.get(Channel, ids["b"])
        assert await ChannelService(db).scan_channel(channel) == 0
    assert channel_service._moving_channels is _moving_channels
