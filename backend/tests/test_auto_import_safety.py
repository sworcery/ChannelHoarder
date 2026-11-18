import asyncio
import json
import os
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.services.channel_service import ChannelService
from app.services.import_service import _match_score, import_matched_files, scan_folder_for_imports
from app.services.naming_service import build_output_path

NAME = "Show"
TITLE = "Grace Community Church Sunday Sermon Series Week {n} Romans"


async def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DOWNLOAD_DIR", str(tmp_path))
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    return eng, maker


async def _channel(db, tmp_path):
    ch = Channel(channel_id="c", channel_name=NAME, channel_url="u", platform="youtube",
                 download_dir=str(tmp_path), health_status="unknown")
    db.add(ch)
    await db.flush()
    return ch


def _video(ch, n, status, path=None):
    return Video(
        channel_id=ch.id, video_id=f"vid{n}", title=TITLE.format(n=n),
        status=status, upload_date=date(2024, 1, n), season=2024, episode=n,
        file_path=path, file_size=10 if path else None,
    )


def _season_dir(tmp_path):
    d = tmp_path / NAME / "Season 2024"
    d.mkdir(parents=True)
    return d


async def _run_auto(db, ch):
    return await ChannelService(db)._auto_import_existing(ch)


def test_completed_serial_files_survive_new_episode(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            paths = {}
            for n in range(9, 13):
                p = d / f"S2024E{n:03d} - {TITLE.format(n=n)} - 2024010{n % 10} - [vid{n}].mp4"
                p.write_bytes(f"content-{n}".encode())
                paths[n] = str(p)
                db.add(_video(ch, n, "completed", str(p)))
            w13 = _video(ch, 13, "queued")
            db.add(w13)
            await db.flush()
            db.add(DownloadQueue(video_id=w13.id))
            await db.commit()

            # Guard: the old fuzzy matcher scored these names above its threshold.
            for n, p in paths.items():
                assert _match_score(os.path.basename(p), w13.title) >= 0.75

            imported = await _run_auto(db, ch)
            assert imported == 0

            for n, p in paths.items():
                assert open(p, "rb").read() == f"content-{n}".encode()
            vids = {v.video_id: v for v in (await db.execute(select(Video))).scalars().all()}
            for n, p in paths.items():
                assert vids[f"vid{n}"].file_path == p
                assert vids[f"vid{n}"].status == "completed"
            assert vids["vid13"].status == "queued"
            assert vids["vid13"].file_path is None
            q = (await db.execute(select(DownloadQueue))).scalars().all()
            assert len(q) == 1 and q[0].video_id == vids["vid13"].id
        await eng.dispose()

    asyncio.run(run())


def test_id_tag_and_info_json_are_imported(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            db.add(_video(ch, 13, "queued"))
            db.add(_video(ch, 14, "pending"))
            await db.commit()
            (d / "totally different name [vid13].mp4").write_bytes(b"w13")
            (d / "another name.mp4").write_bytes(b"w14")
            (d / "another name.info.json").write_text(json.dumps({"id": "vid14"}))

            imported = await _run_auto(db, ch)
            assert imported == 2
            vids = {v.video_id: v for v in (await db.execute(select(Video))).scalars().all()}
            assert vids["vid13"].status == "completed"
            assert open(vids["vid13"].file_path, "rb").read() == b"w13"
            assert vids["vid14"].status == "completed"
            assert open(vids["vid14"].file_path, "rb").read() == b"w14"
        await eng.dispose()

    asyncio.run(run())


def test_existing_target_is_never_overwritten(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "queued")
            db.add(v)
            await db.commit()
            target = build_output_path(
                channel_name=NAME, video_title=v.title, video_id=v.video_id,
                upload_date=v.upload_date, season=v.season, episode=v.episode,
                base_dir=str(tmp_path),
            ) + ".mp4"
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(b"precious")
            stray = tmp_path / NAME / "stray [vid13].mp4"
            stray.write_bytes(b"stray")

            imported = await _run_auto(db, ch)
            # The file already at the target is adopted in place; the stray
            # copy is skipped rather than moved over it.
            assert imported == 1
            assert open(target, "rb").read() == b"precious"
            assert stray.read_bytes() == b"stray"
            row = (await db.execute(select(Video))).scalar_one()
            assert row.file_path == target
        await eng.dispose()

    asyncio.run(run())


def test_quality_upgrade_requeue_keeps_state(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            p = d / "S2024E013 - own file - [vid13].mp4"
            p.write_bytes(b"old-quality")
            v = _video(ch, 13, "queued", str(p))
            v.quality_downloaded = "720p"
            db.add(v)
            await db.flush()
            db.add(DownloadQueue(video_id=v.id))
            await db.commit()

            assert await _run_auto(db, ch) == 0
            row = (await db.execute(select(Video))).scalar_one()
            assert row.status == "queued" and row.quality_downloaded == "720p"
            assert row.file_path == str(p)
            assert len((await db.execute(select(DownloadQueue))).scalars().all()) == 1
            assert p.read_bytes() == b"old-quality"
        await eng.dispose()

    asyncio.run(run())


def test_manual_mode_skips_and_refuses_files_owned_by_other_videos(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            p12 = d / f"S2024E012 - {TITLE.format(n=12)} - 20240112 - [vid12].mp4"
            p12.write_bytes(b"week12")
            db.add(_video(ch, 12, "completed", str(p12)))
            w13 = _video(ch, 13, "queued")
            db.add(w13)
            await db.commit()

            assert await scan_folder_for_imports(db, ch.id, str(d)) == []
            result = await import_matched_files(
                db, ch.id, [{"file_path": str(p12), "matched_video_id": w13.id}],
            )
            assert result["imported"] == 0 and result["errors"]
            assert p12.read_bytes() == b"week12"
            await db.refresh(w13)
            assert w13.status == "queued"
        await eng.dispose()

    asyncio.run(run())


def test_owned_file_reached_through_another_path_is_refused(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        alias = tmp_path / "alias"
        alias.symlink_to(d)  # a second mount of the same share
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            p12 = d / f"S2024E012 - {TITLE.format(n=12)} - 20240112 - [vid12].mp4"
            p12.write_bytes(b"week12")
            db.add(_video(ch, 12, "completed", str(p12)))
            w13 = _video(ch, 13, "queued")
            db.add(w13)
            await db.commit()

            result = await import_matched_files(
                db, ch.id, [{"file_path": str(alias / p12.name), "matched_video_id": w13.id}],
            )
            assert result["imported"] == 0 and result["errors"]
            assert p12.read_bytes() == b"week12"
        await eng.dispose()

    asyncio.run(run())


def test_video_with_its_own_file_does_not_adopt_a_stray(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            own = d / "old name [vid13].mp4"
            own.write_bytes(b"own")
            v = _video(ch, 13, "queued", str(own))  # queued for a quality upgrade
            db.add(v)
            await db.commit()
            stray = tmp_path / "stray [vid13].mp4"
            stray.write_bytes(b"stray")

            result = await import_matched_files(
                db, ch.id, [{"file_path": str(stray), "matched_video_id": v.id}],
            )
            assert result["imported"] == 0
            assert own.read_bytes() == b"own" and stray.read_bytes() == b"stray"
            await db.refresh(v)
            assert v.status == "queued" and v.file_path == str(own)
        await eng.dispose()

    asyncio.run(run())


def test_sidecars_stay_while_a_same_name_video_remains(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        src_dir = tmp_path / "incoming"
        src_dir.mkdir()
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "queued")
            db.add(v)
            await db.commit()
            (src_dir / "Z.mp4").write_bytes(b"a")
            (src_dir / "Z.mkv").write_bytes(b"b")
            (src_dir / "Z.info.json").write_text("{}")

            result = await import_matched_files(
                db, ch.id, [{"file_path": str(src_dir / "Z.mp4"), "matched_video_id": v.id}],
            )
            assert result["imported"] == 1
            assert (src_dir / "Z.info.json").exists()  # may belong to Z.mkv
        await eng.dispose()

    asyncio.run(run())


def test_partial_and_empty_files_are_not_adopted(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            db.add(_video(ch, 13, "queued"))
            await db.commit()
            (d / "x [vid13].f137.mp4").write_bytes(b"part")
            (d / "x [vid13].temp.mp4").write_bytes(b"part")
            (d / "x [vid13].f251-drc.webm").write_bytes(b"part")
            (d / "x [vid13].fhls-1080p.mp4").write_bytes(b"part")
            (d / "x [vid13].mp4").write_bytes(b"")

            assert await _run_auto(db, ch) == 0
            row = (await db.execute(select(Video))).scalar_one()
            assert row.status == "queued" and row.file_path is None
        await eng.dispose()

    asyncio.run(run())


def test_one_file_per_video_and_no_double_count(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "queued")
            db.add(v)
            await db.commit()
            target = build_output_path(
                channel_name=NAME, video_title=v.title, video_id=v.video_id,
                upload_date=v.upload_date, season=v.season, episode=v.episode,
                base_dir=str(tmp_path),
            ) + ".mp4"
            (d / "a copy [vid13].mp4").write_bytes(b"copy")
            with open(target, "wb") as f:
                f.write(b"at-target")

            assert await _run_auto(db, ch) == 1
            await db.refresh(v)
            await db.refresh(ch)
            assert v.file_path == target
            assert open(target, "rb").read() == b"at-target"
            assert (d / "a copy [vid13].mp4").read_bytes() == b"copy"
            assert ch.downloaded_count == 1

            # Already completed: a second pass imports nothing more.
            assert await _run_auto(db, ch) == 0
            result = await import_matched_files(
                db, ch.id, [{"file_path": str(d / "a copy [vid13].mp4"),
                             "matched_video_id": v.id}],
            )
            assert result["imported"] == 0
        await eng.dispose()

    asyncio.run(run())


def test_downloading_video_is_not_imported(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        d = _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "downloading")
            db.add(v)
            await db.commit()
            f = d / "x [vid13].mp4"
            f.write_bytes(b"data")
            result = await import_matched_files(
                db, ch.id, [{"file_path": str(f), "matched_video_id": v.id}],
            )
            assert result["imported"] == 0 and f.exists()
        await eng.dispose()

    asyncio.run(run())


def test_last_tag_wins_and_sidecars_move(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v13 = _video(ch, 13, "queued")
            db.add(v13)
            db.add(_video(ch, 14, "queued"))
            await db.commit()
            root = tmp_path / NAME
            (root / "[vid14] odd name [vid13].mp4").write_bytes(b"v")
            (root / "[vid14] odd name [vid13].info.json").write_text("{}")

            assert await _run_auto(db, ch) == 1
            await db.refresh(v13)
            assert v13.status == "completed"
            assert os.path.exists(os.path.splitext(v13.file_path)[0] + ".info.json")
            assert not (root / "[vid14] odd name [vid13].info.json").exists()
        await eng.dispose()

    asyncio.run(run())


def test_existing_nfo_blocks_import(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        _season_dir(tmp_path)
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "queued")
            db.add(v)
            await db.commit()
            base = build_output_path(
                channel_name=NAME, video_title=v.title, video_id=v.video_id,
                upload_date=v.upload_date, season=v.season, episode=v.episode,
                base_dir=str(tmp_path),
            )
            with open(base + ".nfo", "w") as f:
                f.write("keep")
            stray = tmp_path / NAME / "stray [vid13].mp4"
            stray.write_bytes(b"stray")

            assert await _run_auto(db, ch) == 0
            assert stray.exists() and open(base + ".nfo").read() == "keep"
        await eng.dispose()

    asyncio.run(run())


def test_manual_import_still_fuzzy_matches(tmp_path, monkeypatch):
    async def run():
        eng, maker = await _setup(tmp_path, monkeypatch)
        src = tmp_path / "inbox"
        src.mkdir()
        async with maker() as db:
            ch = await _channel(db, tmp_path)
            v = _video(ch, 13, "pending")
            db.add(v)
            await db.commit()
            (src / "grace community church sunday sermon series week 13 romans.mp4").write_bytes(b"manual")

            matches = await scan_folder_for_imports(db, ch.id, str(src))
            assert len(matches) == 1 and matches[0]["matched_video_id"] == v.id
            result = await import_matched_files(db, ch.id, matches)
            assert result["imported"] == 1
            await db.refresh(v)
            assert v.status == "completed"
            assert open(v.file_path, "rb").read() == b"manual"
        await eng.dispose()

    asyncio.run(run())
