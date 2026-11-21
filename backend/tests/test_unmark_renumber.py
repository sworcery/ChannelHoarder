import os
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
import app.routers.channels as ch_router
import app.services.channel_service as cs
from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.services import channel_activity as ca
from app.services import download_service


@pytest.fixture(autouse=True)
def clean_state():
    for s in (cs._moving_channels, cs._scanning_channels, cs._working_channels):
        s.clear()
    ca.set_move_all_running(False)
    download_service._active_downloads.clear()
    yield
    for s in (cs._moving_channels, cs._scanning_channels, cs._working_channels):
        s.clear()


async def _setup(monkeypatch, tmp_path):
    """Regular a,b,c = episodes 1..3. Shorts: s1 (Feb) skipped/no file, s2 (Apr) and s3 (Jun) downloaded."""
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
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u1",
                     platform="youtube", health_status="unknown", download_dir=str(root))
        db.add(ch)
        await db.flush()
        d = root / "Chan" / "Season 2025"
        d.mkdir(parents=True)
        specs = [("a", 1, 1, False, True), ("s1", 2, 0, True, False), ("b", 3, 2, False, True),
                 ("s2", 4, 0, True, True), ("c", 5, 3, False, True), ("s3", 6, 0, True, True)]
        for vid, month, ep, short, has_file in specs:
            p = d / f"{vid}.mp4"
            if has_file:
                p.write_text(vid)
                (d / f"{vid}.nfo").write_text("old")
            db.add(Video(channel_id=ch.id, video_id=vid, title=vid,
                         status="completed" if has_file else "skipped",
                         file_path=str(p) if has_file else None, season=2025, episode=ep,
                         is_short=short, short_locked=short,
                         upload_date=date(2025, month, 1)))
        await db.commit()
        cid = ch.id
    return maker, cid


async def _videos(db, cid):
    rows = await db.execute(select(Video).where(Video.channel_id == cid))
    return {v.video_id: v for v in rows.scalars().all()}


def _unmark(db, cid, v):
    return ch_router.toggle_video_short(cid, v.id, ch_router.ShortRequest(is_short=False), db)


async def test_unmark_skipped_short_gets_next_episode(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        before = {k: (v.episode, v.file_path) for k, v in (await _videos(db, cid)).items()}
        r = await _unmark(db, cid, (await _videos(db, cid))["s1"])
        assert "episode 4" in r["message"] and r["renamed"] == 0
        v = await _videos(db, cid)
        assert v["s1"].episode == 4 and v["s1"].file_path is None
        for k in ("a", "b", "c", "s2", "s3"):
            assert (v[k].episode, v[k].file_path) == before[k]
            assert os.path.exists(v[k].file_path)


async def test_unmark_downloaded_short_renames_only_its_file(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        before = {k: v.file_path for k, v in (await _videos(db, cid)).items()}
        r = await _unmark(db, cid, (await _videos(db, cid))["s2"])
        assert r["renamed"] == 1 and "episode 4" in r["message"]
        v = await _videos(db, cid)
        new = v["s2"].file_path
        assert new != before["s2"] and "E004" in os.path.basename(new)
        assert os.path.exists(new) and not os.path.exists(before["s2"])
        nfo = os.path.splitext(new)[0] + ".nfo"
        assert os.path.exists(nfo) and open(nfo).read() != "old"
        assert not os.path.exists(os.path.splitext(before["s2"])[0] + ".nfo")
        for k in ("a", "b", "c", "s3"):
            assert v[k].file_path == before[k] and os.path.exists(before[k])


async def test_two_unmarks_get_distinct_numbers(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        v = await _videos(db, cid)
        await _unmark(db, cid, v["s1"])
        await _unmark(db, cid, v["s2"])
        v = await _videos(db, cid)
        assert (v["s1"].episode, v["s2"].episode) == (4, 5)


async def test_mark_short_leaves_others_alone(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        before = {k: (v.episode, v.file_path) for k, v in (await _videos(db, cid)).items()}
        v = await _videos(db, cid)
        r = await ch_router.toggle_video_short(
            cid, v["a"].id, ch_router.ShortRequest(is_short=True), db)
        assert r["renamed"] == 0
        v = await _videos(db, cid)
        assert v["a"].episode == 0 and v["a"].file_path == before["a"][1]
        for k in ("b", "c", "s1", "s2", "s3"):
            assert (v[k].episode, v[k].file_path) == before[k]


async def test_busy_channel_saves_number_and_scan_renames(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    cs._working_channels.add(cid)
    async with maker() as db:
        old = (await _videos(db, cid))["s2"].file_path
        r = await _unmark(db, cid, (await _videos(db, cid))["s2"])
        assert "next scan" in r["message"] and r["renamed"] == 0
        v = (await _videos(db, cid))["s2"]
        assert v.episode == 4 and v.file_path == old and os.path.exists(old)

    cs._working_channels.clear()
    async with maker() as db:
        svc = cs.ChannelService(db)
        channel = await db.get(Channel, cid)
        assert await svc._rename_existing_files(channel) >= 1
        v = (await _videos(db, cid))["s2"]
        assert "E004" in os.path.basename(v.file_path) and os.path.exists(v.file_path)
        assert not os.path.exists(old)


async def test_scan_numbers_leftover_episode_zero_only(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        # Put every file at its template name first, so only the leftover is expected to move
        await cs.ChannelService(db)._rename_existing_files(await db.get(Channel, cid))
        # A leftover: regular video at episode 0 with a file (the old behaviour)
        s2 = (await _videos(db, cid))["s2"]
        s2.is_short = False
        s2.short_locked = True
        await db.commit()
        before = {k: (v.episode, v.file_path) for k, v in (await _videos(db, cid)).items()}

        svc = cs.ChannelService(db)
        channel = await db.get(Channel, cid)

        async def noop(*a, **k):
            return 0
        monkeypatch.setattr(svc, "_backfill_channel_art", noop)
        monkeypatch.setattr(svc, "_reclassify_existing_videos", noop)
        monkeypatch.setattr(svc, "_auto_import_existing", noop)
        entries = [{"id": k, "title": k, "url": f"https://x/{k}"} for k in before]
        await svc._scan_channel_inner(channel, entries)

        v = await _videos(db, cid)
        assert v["s2"].episode == 4
        assert "E004" in os.path.basename(v["s2"].file_path) and os.path.exists(v["s2"].file_path)
        for k in ("a", "b", "c", "s1", "s3"):
            assert (v[k].episode, v[k].file_path) == before[k]


async def test_bulk_unmark_numbers_in_date_order(monkeypatch, tmp_path):
    maker, cid = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        v = await _videos(db, cid)
        # Listed newest first; numbering must still follow upload date
        r = await ch_router.bulk_reclassify_videos(
            cid, ch_router.BulkReclassifyRequest(
                video_ids=[v["s3"].id, v["s1"].id], set_short=False, set_livestream=False), db)
        assert r["renamed"] == 1
        v = await _videos(db, cid)
        assert (v["s1"].episode, v["s3"].episode) == (4, 5)
        assert "E005" in os.path.basename(v["s3"].file_path)
        assert v["s2"].episode == 0
