import asyncio
import os
from datetime import date

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
import app.routers.channels as channels_router
import app.services.channel_service as cs
from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.schemas import ImportConfirmRequest, RepairDatesConfirm
from app.services import download_service
from app.services.channel_service import channel_busy


@pytest.fixture(autouse=True)
def clean_state():
    cs._moving_channels.clear()
    cs._scanning_channels.clear()
    cs._working_channels.clear()
    cs._move_all_running = False
    download_service._active_downloads.clear()
    yield
    cs._moving_channels.clear()
    cs._scanning_channels.clear()
    cs._working_channels.clear()
    cs._move_all_running = False
    download_service._active_downloads.clear()


async def _setup(monkeypatch, tmp_path, names=("one",)):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(database, "async_session", maker)
    root = tmp_path / "downloads"
    root.mkdir()
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(root))
    monkeypatch.setattr(settings, "EXTRA_DOWNLOAD_DIRS", str(tmp_path))
    ids = []
    async with maker() as db:
        for n in names:
            ch = Channel(channel_id=f"c_{n}", channel_name=f"Chan {n}", channel_url=f"u{n}",
                         platform="youtube", health_status="unknown")
            db.add(ch)
            await db.flush()
            d = root / f"Chan {n}" / "Season 1"
            d.mkdir(parents=True)
            p = d / f"{n}.mp4"
            p.write_text(n)
            db.add(Video(channel_id=ch.id, video_id=f"vid_{n}", title=n, status="completed",
                         file_path=str(p), season=1, episode=1, upload_date=date(2025, 1, 1)))
            ids.append(ch.id)
        await db.commit()
    return maker, root, ids


def _make_busy(kind, cid):
    if kind == "moving":
        cs._moving_channels.add(cid)
    elif kind == "scanning":
        cs._scanning_channels.add(cid)
    elif kind == "working":
        cs._working_channels.add(cid)
    elif kind == "move_all":
        cs._move_all_running = True
    elif kind == "active_download":
        download_service._active_downloads.add("vid_one")


async def test_each_busy_reason_is_detected(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        assert await channel_busy(db, cid) is None
        for kind in ("moving", "scanning", "working", "move_all", "active_download"):
            for s in (cs._moving_channels, cs._scanning_channels, cs._working_channels,
                      download_service._active_downloads):
                s.clear()
            cs._move_all_running = False
            _make_busy(kind, cid)
            reason = await channel_busy(db, cid)
            assert reason, kind
            if kind == "working":
                assert reason == "Another operation on this channel is running; try again when it finishes"
            if kind == "move_all":
                assert reason == "Move All is running; try again when it finishes"

        download_service._active_downloads.clear()
        cs._move_all_running = True
        assert await channel_busy(db, cid, from_move_all=True) is None
        cs._move_all_running = False
        download_service._active_downloads.clear()

        v = (await db.execute(select(Video))).scalar_one()
        v.status = "downloading"
        await db.commit()
        assert "download" in await channel_busy(db, cid)


async def _expect_409(coro):
    with pytest.raises(HTTPException) as ei:
        await coro
    assert ei.value.status_code == 409
    return ei.value.detail


@pytest.mark.parametrize("kind", ["moving", "scanning", "working", "move_all", "active_download"])
async def test_endpoints_refuse_while_busy(monkeypatch, tmp_path, kind):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    _make_busy(kind, cid)
    async with maker() as db:
        await _expect_409(channels_router.delete_video_file(cid, 1, db))
        await _expect_409(channels_router.rename_video_file(cid, 1, db))
        await _expect_409(channels_router.delete_video(cid, 1, delete_files=True, db=db))
        await _expect_409(channels_router.bulk_delete_videos(
            cid, channels_router.BulkDeleteRequest(video_ids=[1], delete_files=True), db))
        await _expect_409(channels_router.delete_channel_shorts(cid, db))
        await _expect_409(channels_router.delete_channel_livestreams(cid, db))
        await _expect_409(channels_router.detect_clean_shorts_confirm(cid, db))
        await _expect_409(channels_router.force_rescan(cid, db))
        await _expect_409(channels_router.move_channel_files(
            cid, channels_router.MoveFilesRequest(new_download_dir=str(tmp_path / "other")), db))
        await _expect_409(channels_router.delete_channel(cid, delete_files=True, db=db))
        await _expect_409(channels_router.renumber_confirm(cid, db))
        await _expect_409(channels_router.repair_dates_confirm(cid, RepairDatesConfirm(changes=[]), db))
        await _expect_409(channels_router.confirm_import(
            cid, ImportConfirmRequest.model_construct(matches=[]), db))
        await _expect_409(channels_router.bulk_rename_videos(
            cid, channels_router.BulkRenameRequest(video_ids=[1]), db))
    # Nothing was touched and the channel is still there.
    assert os.path.exists(root / "Chan one" / "Season 1" / "one.mp4")
    async with maker() as db:
        assert await db.get(Channel, cid) is not None


async def test_delete_without_files_is_not_refused(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    cs._scanning_channels.add(cid)
    async with maker() as db:
        await channels_router.delete_channel(cid, delete_files=False, db=db)
    async with maker() as db:
        assert await db.get(Channel, cid) is None


async def test_move_all_skips_busy_channel_and_moves_the_rest(monkeypatch, tmp_path):
    maker, root, (busy_id, free_id) = await _setup(monkeypatch, tmp_path, names=("busy", "free"))
    sent = []

    async def fake_broadcast(event, payload):
        sent.append(payload)

    from app.services.notification_service import NotificationService
    monkeypatch.setattr(NotificationService, "broadcast", fake_broadcast)
    cs._scanning_channels.add(busy_id)
    new_root = tmp_path / "other"

    await channels_router._move_all_task(str(new_root), {busy_id: str(root), free_id: str(root)})

    assert (root / "Chan busy" / "Season 1" / "busy.mp4").exists()
    assert (new_root / "Chan free" / "Season 1" / "free.mp4").exists()
    assert sent and sent[-1]["skipped"] and "Chan busy" in sent[-1]["skipped"][0]
    assert "Chan busy" in sent[-1]["message"]
    assert not cs._moving_channels
    assert cs._move_all_running is False


async def test_move_all_does_not_block_itself(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    new_root = tmp_path / "other"
    cs._move_all_running = True  # as the endpoint sets it before starting the task

    await channels_router._move_all_task(str(new_root), {cid: str(root)})

    assert (new_root / "Chan one" / "Season 1" / "one.mp4").exists()
    assert cs._move_all_running is False


async def test_move_all_409_reads_move_all(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    cs._move_all_running = True
    async with maker() as db:
        detail = await _expect_409(channels_router.move_channel_files(
            cid, channels_router.MoveFilesRequest(new_download_dir=str(tmp_path / "o")), db))
    assert detail == "Move All is running; try again when it finishes"


async def test_concurrent_force_rescans_one_gets_409(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    runs = []

    async def slow(channel, db):
        runs.append(1)
        await asyncio.sleep(0.1)
        return {}

    monkeypatch.setattr(channels_router, "_force_rescan_locked", slow)

    async def call():
        async with maker() as db:
            return await channels_router.force_rescan(cid, db)

    results = await asyncio.gather(call(), call(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert len(runs) == 1
    assert cid not in cs._scanning_channels


async def test_concurrent_repair_dates_one_gets_409(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)

    async def call():
        async with maker() as db:
            return await channels_router.repair_dates_confirm(cid, RepairDatesConfirm(changes=[]), db)

    results = await asyncio.gather(call(), call(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409
    assert cid not in cs._scanning_channels


async def test_concurrent_moves_one_gets_409(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)

    async def noop_task(*a, **k):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(channels_router, "_move_channel_task", noop_task)

    async def call():
        async with maker() as db:
            return await channels_router.move_channel_files(
                cid, channels_router.MoveFilesRequest(new_download_dir=str(tmp_path / "o")), db)

    results = await asyncio.gather(call(), call(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409


async def test_concurrent_move_alls_one_gets_409(monkeypatch, tmp_path):
    maker, root, _ = await _setup(monkeypatch, tmp_path)

    async def noop_task(*a, **k):
        await asyncio.sleep(0.1)

    monkeypatch.setattr(channels_router, "_move_all_task", noop_task)

    async def call():
        async with maker() as db:
            return await channels_router.move_all_channels(
                channels_router.MoveFilesRequest(new_download_dir=str(tmp_path / "o")), db)

    results = await asyncio.gather(call(), call(), return_exceptions=True)
    errors = [r for r in results if isinstance(r, HTTPException)]
    assert len(errors) == 1 and errors[0].status_code == 409


async def test_per_video_actions_wait_only_for_their_own_download(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        db.add(Video(channel_id=cid, video_id="vid_other", title="other", status="downloading",
                     season=1, episode=2, upload_date=date(2025, 1, 2)))
        await db.commit()
        other_id = (await db.execute(select(Video.id).where(Video.video_id == "vid_other"))).scalar_one()
    # Another video downloading doesn't block renaming or deleting this one...
    async with maker() as db:
        await channels_router.rename_video_file(cid, 1, db)
    async with maker() as db:
        await channels_router.delete_video_file(cid, 1, db)
    # ...but the downloading video itself is refused, and channel-wide work still waits.
    async with maker() as db:
        detail = await _expect_409(channels_router.delete_video(cid, other_id, delete_files=True, db=db))
        assert "video is downloading" in detail
        await _expect_409(channels_router.renumber_confirm(cid, db))
    assert not cs._working_channels


async def test_move_refused_while_renumber_holds_the_claim(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    release = asyncio.Event()

    async def held(channel_id, db):
        await release.wait()
        return {}

    monkeypatch.setattr(channels_router, "_renumber_confirm", held)

    async def renumber():
        async with maker() as db:
            return await channels_router.renumber_confirm(cid, db)

    task = asyncio.create_task(renumber())
    for _ in range(100):
        if cid in cs._working_channels:
            break
        await asyncio.sleep(0.01)
    assert cid in cs._working_channels
    async with maker() as db:
        await _expect_409(channels_router.move_channel_files(
            cid, channels_router.MoveFilesRequest(new_download_dir=str(tmp_path / "o")), db))
    release.set()
    await task
    assert cid not in cs._working_channels


@pytest.mark.parametrize("kind", ["moving", "working"])
async def test_scheduled_scan_skips_claimed_channel(monkeypatch, tmp_path, kind):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    called = []

    async def inner(self, channel):
        called.append(channel.id)
        return 5

    monkeypatch.setattr(cs.ChannelService, "_scan_channel_inner", inner)
    _make_busy(kind, cid)
    async with maker() as db:
        channel = await db.get(Channel, cid)
        assert await cs.ChannelService(db).scan_channel(channel) == 0
        assert called == []
        cs._moving_channels.clear()
        cs._working_channels.clear()
        assert await cs.ChannelService(db).scan_channel(channel) == 5


async def test_move_all_marks_channel_moving_and_clears_it(monkeypatch, tmp_path):
    maker, root, (cid,) = await _setup(monkeypatch, tmp_path)
    seen = []
    real = channels_router._move_channel_contents

    async def spy(db, channel, old_dir, new_dir):
        seen.append(channel.id in cs._moving_channels)
        return await real(db, channel, old_dir, new_dir)

    monkeypatch.setattr(channels_router, "_move_channel_contents", spy)
    await channels_router._move_all_task(str(tmp_path / "other"), {cid: str(root)})
    assert seen == [True]
    assert cid not in cs._moving_channels


async def test_library_fix_on_busy_channel_is_skipped(monkeypatch, tmp_path):
    from app.services import library_repair as lr
    from tests.test_library_repair import _setup as lr_setup

    maker, a_id, b_id, a_path, b_path, _ = await lr_setup(monkeypatch, tmp_path)
    await lr._run_check(maker)
    async with maker() as db:
        cid = (await db.get(Video, b_id)).channel_id
    cs._scanning_channels.add(cid)

    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert [r["state"] for r in results] == ["skipped"]
    assert results[0]["message"].startswith("skipped: ")
    assert os.path.exists(b_path) and not os.path.exists(a_path)

    # A busy item stays open so it can be retried.
    assert [i["state"] for i in lr.get_state()["items"] if i["kind"] == "wrong_file"] == ["open"]

    cs._scanning_channels.clear()
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert [r["state"] for r in results] == ["fixed"]


def test_retention_skips_busy_channel(monkeypatch, tmp_path):
    from app.tasks import retention
    from tests.test_retention_safety import _old, _setup as ret_setup, _video

    async def run():
        eng, maker = await ret_setup(monkeypatch)
        p = tmp_path / "old.mp4"
        p.write_bytes(b"x")
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         retention_days=30, health_status="unknown")
            db.add(ch)
            await db.flush()
            db.add(_video(ch, "old", str(p), downloaded_at=_old()))
            await db.commit()
            cid = ch.id
        cs._moving_channels.add(cid)
        await retention.apply_retention()
        async with maker() as db:
            v = (await db.execute(select(Video))).scalar_one()
        assert v.status == "completed" and p.exists()

        cs._moving_channels.clear()
        await retention.apply_retention()
        async with maker() as db:
            v = (await db.execute(select(Video))).scalar_one()
        await eng.dispose()
        assert v.status == "skipped" and not p.exists()

    asyncio.run(run())
