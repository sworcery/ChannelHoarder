import os
from datetime import date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.services import library_repair as lr
from app.services.naming_service import build_output_path

A_ID, B_ID = "aaaaaaaaaaa", "bbbbbbbbbbb"
BYTES = b"video-A-bytes"


def _path(ch, v):
    return build_output_path(
        channel_name=ch.channel_name, video_title=v.title, video_id=v.video_id,
        upload_date=v.upload_date, season=v.season, episode=v.episode,
        base_dir=ch.download_dir,
    ) + ".mp4"


async def _setup(monkeypatch, tmp_path, probe_tags=True):
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)

    async def _no_broadcast(*a, **k):
        return None

    monkeypatch.setattr(lr.NotificationService, "broadcast", _no_broadcast)
    probed = []

    def fake_probe(path):
        probed.append(path)
        tags = {"purl": f"https://www.youtube.com/watch?v={A_ID}"} if probe_tags else {}
        return {"duration": 100.0, "tags": tags}

    monkeypatch.setattr(lr, "_probe", fake_probe)

    root = tmp_path / "downloads"
    root.mkdir()
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u", platform="youtube",
                     health_status="unknown", download_dir=str(root), quality="1080p")
        db.add(ch)
        await db.flush()
        a = Video(channel_id=ch.id, video_id=A_ID, title="Week 11", status="completed",
                  season=1, episode=1, upload_date=date(2025, 1, 1), duration=100)
        b = Video(channel_id=ch.id, video_id=B_ID, title="Week 12", status="completed",
                  season=1, episode=2, upload_date=date(2025, 1, 8), duration=100)
        db.add_all([a, b])
        await db.flush()
        b_path = _path(ch, b)
        os.makedirs(os.path.dirname(b_path))
        with open(b_path, "wb") as f:
            f.write(BYTES)
        b.file_path = b_path
        await db.commit()
        a_path = _path(ch, a)
        return maker, a.id, b.id, a_path, b_path, probed


async def _vid(maker, vid):
    async with maker() as db:
        return await db.get(Video, vid)


def _by_kind(items):
    return {i["kind"] + ":" + str(i["video_id"]): i for i in items}


def _iid(key):
    """The real item id (it carries the run's start time) for a "kind:video_id" key."""
    return _by_kind(lr.get_state()["items"])[key]["id"]


@pytest.mark.asyncio
async def test_check_lists_and_changes_nothing(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    await lr._run_check(maker)
    items = _by_kind(lr.get_state()["items"])
    assert set(items) == {f"wrong_file:{b_id}", f"lost:{a_id}"}
    assert items[f"wrong_file:{b_id}"]["real_video_id"] == a_id
    assert os.path.exists(b_path) and not os.path.exists(a_path)
    b = await _vid(maker, b_id)
    assert b.file_path == b_path and b.status == "completed"
    async with maker() as db:
        assert (await db.execute(select(DownloadQueue))).first() is None


@pytest.mark.asyncio
async def test_apply_moves_file_and_queues(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    await lr._run_check(maker)
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert [r["state"] for r in results] == ["fixed"]
    assert open(a_path, "rb").read() == BYTES
    assert not os.path.exists(b_path)
    a, b = await _vid(maker, a_id), await _vid(maker, b_id)
    assert a.status == "completed" and a.file_path == a_path
    assert b.status == "queued" and b.file_path is None
    async with maker() as db:
        assert (await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == b_id))).first()
    # The stale lost item for A is recognised as already fixed.
    results = await lr.apply_fixes([_iid(f"lost:{a_id}")], maker=maker)
    assert results[0]["state"] == "skipped"
    assert open(a_path, "rb").read() == BYTES
    # A second run reports nothing (B is queued, A has its file, which now holds A).
    await lr._run_check(maker)
    assert lr.get_state()["items"] == []


@pytest.mark.asyncio
async def test_clean_library_reports_nothing(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    # Make it clean: A owns a file at its own path, and B's file name carries B's id.
    os.makedirs(os.path.dirname(a_path), exist_ok=True)
    with open(a_path, "wb") as f:
        f.write(b"a")
    async with maker() as db:
        a = await db.get(Video, a_id)
        a.file_path = a_path
        await db.commit()
    await lr._run_check(maker)
    assert lr.get_state()["items"] == []


@pytest.mark.asyncio
async def test_taken_target_is_skipped(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    await lr._run_check(maker)
    os.makedirs(os.path.dirname(a_path), exist_ok=True)
    with open(a_path, "wb") as f:
        f.write(b"someone else")
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert results[0]["state"] == "skipped" and "taken" in results[0]["message"]
    assert open(a_path, "rb").read() == b"someone else"
    assert open(b_path, "rb").read() == BYTES
    b = await _vid(maker, b_id)
    assert b.status == "completed" and b.file_path == b_path


@pytest.mark.asyncio
async def test_retention_removed_not_reported(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    async with maker() as db:
        a = await db.get(Video, a_id)
        a.status = "skipped"
        a.monitored = False
        a.downloaded_at = datetime(2025, 1, 2)
        await db.commit()
    await lr._run_check(maker)
    kinds = {i["kind"] for i in lr.get_state()["items"]}
    assert "lost" not in kinds


def _set_probe(monkeypatch, tags, duration=100.0):
    monkeypatch.setattr(lr, "_probe", lambda p: {"duration": duration, "tags": tags})


@pytest.mark.asyncio
async def test_comment_holding_single_url_is_certain(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    _set_probe(monkeypatch, {"comment": f"https://youtu.be/{A_ID}"})
    await lr._run_check(maker)
    assert f"wrong_file:{b_id}" in _by_kind(lr.get_state()["items"])
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert results[0]["state"] == "fixed"
    assert open(a_path, "rb").read() == BYTES


@pytest.mark.asyncio
async def test_comment_description_with_other_url_not_certain(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    _set_probe(monkeypatch, {"comment": f"Missed last week? https://youtu.be/{A_ID}"})
    await lr._run_check(maker)
    assert {i["kind"] for i in lr.get_state()["items"]} == {"lost"}


@pytest.mark.asyncio
async def test_stale_results_are_skipped(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    await lr._run_check(maker)
    with open(b_path, "wb") as f:
        f.write(b"replaced after the check")
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert results[0]["state"] == "skipped" and "changed" in results[0]["message"]
    assert not os.path.exists(a_path)
    assert open(b_path, "rb").read() == b"replaced after the check"


@pytest.mark.asyncio
async def test_sibling_info_json_is_certain(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    _set_probe(monkeypatch, {})
    with open(os.path.splitext(b_path)[0] + ".info.json", "w") as f:
        f.write('{"id": "%s"}' % A_ID)
    await lr._run_check(maker)
    item = _by_kind(lr.get_state()["items"])[f"wrong_file:{b_id}"]
    assert item["real_video_id"] == a_id


@pytest.mark.asyncio
async def test_default_install_leftover_sidecars_are_likely_not_certain(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    _set_probe(monkeypatch, {})
    # A's own sidecars were left behind at the path A still records.
    os.makedirs(os.path.dirname(a_path), exist_ok=True)
    a_base = os.path.splitext(a_path)[0]
    with open(a_base + ".info.json", "w") as f:
        f.write('{"id": "%s"}' % A_ID)
    with open(a_base + ".nfo", "w") as f:
        f.write("<episodedetails/>")
    async with maker() as db:
        a = await db.get(Video, a_id)
        a.file_path = a_path
        await db.commit()
    await lr._run_check(maker)
    items = _by_kind(lr.get_state()["items"])
    assert f"likely:{b_id}" in items and items[f"likely:{b_id}"]["real_video_id"] == a_id
    assert not any(i["kind"] == "wrong_file" for i in items.values())
    assert await lr.apply_fixes(None, all_certain=True, maker=maker) == []


@pytest.mark.asyncio
async def test_sponsorblock_remove_skips_length_tests(monkeypatch, tmp_path):
    from app.models import AppSetting
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    _set_probe(monkeypatch, {}, duration=600.0)
    async with maker() as db:
        a = await db.get(Video, a_id)
        a.duration = 600
        db.add(AppSetting(key="sponsorblock_mode", value='"remove"'))
        await db.commit()
    await lr._run_check(maker)
    assert {i["kind"] for i in lr.get_state()["items"]} == {"lost"}
    assert lr.get_state()["notes"]


@pytest.mark.asyncio
async def test_fix_moves_only_video_and_writes_nfo(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    b_base = os.path.splitext(b_path)[0]
    with open(b_base + ".nfo", "w") as f:
        f.write("old nfo for B")
    with open(b_base + ".jpg", "wb") as f:
        f.write(b"thumb")
    await lr._run_check(maker)
    results = await lr.apply_fixes(None, all_certain=True, maker=maker)
    assert results[0]["state"] == "fixed"
    assert open(a_path, "rb").read() == BYTES
    assert open(b_base + ".nfo").read() == "old nfo for B"
    assert os.path.exists(b_base + ".jpg")
    nfo = open(os.path.splitext(a_path)[0] + ".nfo").read()
    assert "Week 11" in nfo and A_ID in nfo


@pytest.mark.asyncio
async def test_non_video_file_not_probed(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, probed = await _setup(monkeypatch, tmp_path)
    junk = tmp_path / "downloads" / "notes.txt"
    junk.write_text("x")
    async with maker() as db:
        b = await db.get(Video, b_id)
        b.file_path = str(junk)
        await db.commit()
    await lr._run_check(maker)
    assert probed == []


@pytest.mark.asyncio
async def test_wrong_length_likely_and_manual(monkeypatch, tmp_path):
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    monkeypatch.setattr(lr, "_probe", lambda p: {"duration": 600.0, "tags": {}})
    async with maker() as db:
        a = await db.get(Video, a_id)
        a.duration = 600
        await db.commit()
    await lr._run_check(maker)
    items = _by_kind(lr.get_state()["items"])
    assert f"likely:{b_id}" in items and items[f"likely:{b_id}"]["real_video_id"] == a_id
    # "all certain" never touches a likely item.
    assert await lr.apply_fixes(None, all_certain=True, maker=maker) == []
    # Fixed one at a time.
    results = await lr.apply_fixes([_iid(f"likely:{b_id}")], maker=maker)
    assert results[0]["state"] == "fixed"
    assert open(a_path, "rb").read() == BYTES

    # No fitting missing video: listed for manual check, never fixed.
    monkeypatch.setattr(lr, "_probe", lambda p: {"duration": 5000.0, "tags": {}})
    await lr._run_check(maker)
    manual = [i for i in lr.get_state()["items"] if i["kind"] == "manual"]
    assert manual
    assert await lr.apply_fixes([manual[0]["id"]], maker=maker) == []


@pytest.mark.asyncio
async def test_mismatched_info_json_is_not_evidence_for_likely(monkeypatch, tmp_path):
    # Rumble's .info.json holds an embed id that never equals the stored page id, so a
    # mismatched .info.json beside a file must not make it "likely" for a lost video.
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    monkeypatch.setattr(lr, "_probe", lambda p: {"duration": 100.0, "tags": {}})
    with open(os.path.splitext(b_path)[0] + ".info.json", "w") as f:
        f.write('{"id": "embed123"}')
    os.makedirs(os.path.dirname(a_path), exist_ok=True)
    with open(os.path.splitext(a_path)[0] + ".nfo", "w") as f:
        f.write("left behind")  # A's sidecars left at its old path
    async with maker() as db:
        (await db.get(Video, a_id)).file_path = a_path
        await db.commit()
    await lr._run_check(maker)
    kinds = {i["kind"] for i in lr.get_state()["items"] if i["video_id"] == b_id}
    assert "likely" not in kinds and "wrong_file" not in kinds


@pytest.mark.asyncio
async def test_comment_holding_the_description_is_ignored(monkeypatch, tmp_path):
    # youtube-dl wrote the description into `comment`; one that is just a URL must not
    # be read as the file's own source.
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path, probe_tags=False)
    url = f"https://youtu.be/{A_ID}"
    monkeypatch.setattr(lr, "_probe", lambda p: {
        "duration": 100.0, "tags": {"comment": url, "description": url}})
    await lr._run_check(maker)
    assert not [i for i in lr.get_state()["items"] if i["kind"] == "wrong_file"]


@pytest.mark.asyncio
async def test_likely_never_targets_a_video_a_certain_item_restores(monkeypatch, tmp_path):
    # B's file is certainly A (tag). C's wrong-length file also fits A: it must not be
    # offered as "likely A", or fixing it first would block the certain fix.
    maker, a_id, b_id, a_path, b_path, _ = await _setup(monkeypatch, tmp_path)
    async with maker() as db:
        ch = (await db.execute(select(Channel))).scalar_one()
        c = Video(channel_id=ch.id, video_id="ccccccccccc", title="Week 13", status="completed",
                  season=1, episode=3, upload_date=date(2025, 1, 15), duration=300)
        db.add(c)
        await db.flush()
        c_path = _path(ch, c)
        with open(c_path, "wb") as f:
            f.write(b"c-bytes")
        c.file_path = c_path
        a = await db.get(Video, a_id)
        a.file_path = a_path
        await db.commit()
        c_id = c.id

    def probe(p):
        if p == b_path:
            return {"duration": 100.0, "tags": {"purl": f"https://www.youtube.com/watch?v={A_ID}"}}
        return {"duration": 100.0, "tags": {}}  # C: 100 s, but its row says 300 s

    monkeypatch.setattr(lr, "_probe", probe)
    await lr._run_check(maker)
    items = lr.get_state()["items"]
    assert any(i["kind"] == "wrong_file" and i["real_video_id"] == a_id for i in items)
    assert not any(i["kind"] == "likely" and i["video_id"] == c_id and i["real_video_id"] == a_id
                   for i in items)
