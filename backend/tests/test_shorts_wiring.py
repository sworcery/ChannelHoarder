"""The Shorts rule as wired into the scan, the reclassify pass and detect & clean."""
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.routers.channels import _detect_clean_shorts_confirm
from app.services.channel_service import ChannelService, _scanning_channels
from app.services.ytdlp_service import YtdlpService


async def _maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return eng, async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "YOUTUBE_API_KEY", "")
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    _scanning_channels.clear()

    async def _noop(*a, **k):
        return 0

    monkeypatch.setattr(ChannelService, "_backfill_channel_art", _noop)
    monkeypatch.setattr(ChannelService, "_auto_import_existing", _noop)
    monkeypatch.setattr(ChannelService, "_rename_existing_files", _noop)
    monkeypatch.setattr(YtdlpService, "get_rss_upload_dates", lambda self, *a, **k: {})
    yield
    _scanning_channels.clear()


async def _channel(db, tmp_path, platform, name):
    ch = Channel(
        channel_id=f"id_{name}", channel_name=name, channel_url=f"https://example.com/{name}",
        platform=platform, auto_download=False, health_status="healthy",
        download_dir=str(tmp_path / name), thumbnail_url="https://example.com/a.jpg",
    )
    db.add(ch)
    await db.flush()
    return ch


def _video(ch, vid, title="Regular", duration=30, status="completed", is_short=False, url=None):
    return Video(
        channel_id=ch.id, video_id=vid, title=title, duration=duration, status=status,
        is_short=is_short, source_url=url, upload_date=date(2026, 1, 1), season=2026, episode=1,
    )


async def _get(db, vid):
    db.expire_all()
    return (await db.execute(select(Video).where(Video.video_id == vid))).scalar_one()


def _listing(monkeypatch, entries):
    async def fake(self, channel):
        return entries

    monkeypatch.setattr(ChannelService, "fetch_video_list", fake)


def _entry(vid, duration):
    return {"id": vid, "title": f"Video {vid}", "upload_date": "20260901", "duration": duration}


async def test_scan_wiring(monkeypatch, tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            rumble = await _channel(db, tmp_path, "rumble", "r")
            yt = await _channel(db, tmp_path, "youtube", "y")
            await db.commit()
            _listing(monkeypatch, [_entry("rumble_clip1", 30)])
            await ChannelService(db)._scan_channel_inner(rumble)
            _listing(monkeypatch, [_entry("yt_clip0001", 30)])
            await ChannelService(db)._scan_channel_inner(yt)
            clip = await _get(db, "rumble_clip1")
            assert not clip.is_short and clip.status != "skipped"
            assert clip.episode != 0
            yt_clip = await _get(db, "yt_clip0001")
            assert yt_clip.is_short and yt_clip.status == "skipped"
    finally:
        await eng.dispose()


async def test_reclassify_leaves_unreliable_signals_alone(tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            yt = await _channel(db, tmp_path, "youtube", "y")
            rumble = await _channel(db, tmp_path, "rumble", "r")
            db.add_all([
                _video(yt, "yt_30s_down", duration=30),
                _video(yt, "yt_url_only", duration=300, url="https://www.youtube.com/shorts/abc"),
                _video(rumble, "rumble_tag1", title="Clip #shorts", duration=20),
                _video(yt, "yt_tab_short", duration=120),
            ])
            await db.commit()
            svc = ChannelService(db)
            await svc._reclassify_existing_videos(
                yt, [{"id": "yt_30s_down"}, {"id": "yt_url_only"},
                     {"id": "yt_tab_short", "_source_tab": "shorts"}],
            )
            await svc._reclassify_existing_videos(rumble, [{"id": "rumble_tag1"}])
            for vid in ("yt_30s_down", "yt_url_only", "rumble_tag1"):
                v = await _get(db, vid)
                assert not v.is_short and v.status == "completed", vid
            assert (await _get(db, "yt_tab_short")).is_short
    finally:
        await eng.dispose()


async def test_detect_clean_confirm_only_touches_sent_ids(tmp_path):
    eng, maker = await _maker()
    try:
        async with maker() as db:
            yt = await _channel(db, tmp_path, "youtube", "y")
            other = await _channel(db, tmp_path, "youtube", "o")
            db.add_all([
                _video(yt, "sent_flagged", title="#shortfilm", duration=900, is_short=True),
                _video(yt, "not_sent_flag", title="Old flag", duration=900, is_short=True),
                _video(other, "other_chan", duration=900, is_short=True),
                _video(yt, "sent_regular", duration=900),
            ])
            await db.commit()
            yt_id = yt.id
            ids = {}
            for vid in ("sent_flagged", "other_chan", "sent_regular"):
                ids[vid] = (await _get(db, vid)).id
            await _detect_clean_shorts_confirm(yt_id, db, set(ids.values()))
            assert (await _get(db, "sent_flagged")).status == "skipped"
            assert (await _get(db, "not_sent_flag")).status == "completed"
            assert (await _get(db, "other_chan")).status == "completed"
            regular = await _get(db, "sent_regular")
            assert not regular.is_short and regular.status == "completed"
    finally:
        await eng.dispose()


async def test_detect_clean_confirm_files_on_disk(tmp_path):
    """Only the sent, flagged row's file goes; a file another channel also records stays."""
    from app.models import DownloadQueue

    def _file(name):
        p = tmp_path / "files" / f"{name}.mp4"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * 10)
        p.with_suffix(".nfo").write_text("nfo")
        return p

    eng, maker = await _maker()
    try:
        async with maker() as db:
            yt = await _channel(db, tmp_path, "youtube", "y")
            other = await _channel(db, tmp_path, "youtube", "o")
            # Both channels resolve to one folder, as on old installs.
            other.download_dir = yt.download_dir
            yt.folder_name = other.folder_name = "Shared"
            sent, unsent, theirs, shared = _file("sent"), _file("unsent"), _file("theirs"), _file("shared")
            rows = {
                "sent": _video(yt, "sent", duration=900, is_short=True),
                "unsent": _video(yt, "unsent", duration=900, is_short=True),
                "theirs": _video(other, "theirs", duration=900, is_short=True),
                "shared": _video(yt, "shared", duration=900, is_short=True),
                "shared_other": _video(other, "shared_other", duration=900),
                "queued_short": _video(yt, "queued_short", title="Clip #shorts", duration=900,
                                       status="queued"),
                "unsent_rule": _video(yt, "unsent_rule", title="Another #shorts", duration=900),
            }
            rows["sent"].file_path = str(sent)
            rows["unsent"].file_path = str(unsent)
            rows["theirs"].file_path = str(theirs)
            rows["shared"].file_path = str(shared)
            rows["shared_other"].file_path = str(shared)  # an old shared folder
            db.add_all(rows.values())
            await db.flush()
            db.add(DownloadQueue(video_id=rows["queued_short"].id))
            await db.commit()
            send = {rows[k].id for k in ("sent", "theirs", "shared", "queued_short")}
            await _detect_clean_shorts_confirm(yt.id, db, send)

            assert not sent.exists() and not sent.with_suffix(".nfo").exists()
            assert unsent.exists() and theirs.exists()
            assert shared.exists()  # another channel's video records it
            assert (await _get(db, "shared")).status == "skipped"
            queued = await _get(db, "queued_short")
            assert queued.is_short and queued.status == "skipped"
            left = (await db.execute(select(DownloadQueue))).scalars().all()
            assert left == []  # the short won't download afterwards
            assert not (await _get(db, "unsent_rule")).is_short  # matches the rule, not sent
    finally:
        await eng.dispose()
