import asyncio
import os
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.models import Channel, DownloadQueue, Video
from app.services.channel_service import ChannelService


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _old():
    return (datetime.now(timezone.utc) - timedelta(days=60)).replace(tzinfo=None)


def _video(ch, vid_id, path, status="completed", downloaded_at=None, monitored=True):
    return Video(
        channel_id=ch.id, video_id=vid_id, title=vid_id, status=status,
        upload_date=date(2024, 1, 1), season=2024, episode=1, monitored=monitored,
        downloaded_at=downloaded_at, file_path=path, file_size=10,
    )


async def _setup(monkeypatch):
    from app.tasks import retention
    eng = _engine()
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(retention, "async_session", maker)
    return eng, maker


def test_retention_ignores_non_completed_statuses(tmp_path, monkeypatch):
    async def run():
        from app.tasks import retention
        eng, maker = await _setup(monkeypatch)
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         retention_days=30, health_status="unknown")
            db.add(ch)
            await db.flush()
            for st in ("queued", "downloading", "failed", "skipped"):
                p = tmp_path / f"{st}.mp4"
                p.write_bytes(b"x")
                db.add(_video(ch, st, str(p), status=st, downloaded_at=_old()))
            await db.commit()
        await retention.apply_retention()
        async with maker() as db:
            vids = {v.video_id: v for v in (await db.execute(select(Video))).scalars().all()}
        await eng.dispose()
        for st in ("queued", "downloading", "failed", "skipped"):
            assert vids[st].status == st
            assert vids[st].file_path is not None
            assert os.path.exists(tmp_path / f"{st}.mp4")

    asyncio.run(run())


def test_undeletable_file_leaves_row_completed(tmp_path, monkeypatch):
    async def run():
        from app.tasks import retention
        eng, maker = await _setup(monkeypatch)
        monkeypatch.setattr(retention, "delete_video_files", lambda p: 0)
        p = tmp_path / "stuck.mp4"
        p.write_bytes(b"x")
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         retention_days=30, health_status="unknown")
            db.add(ch)
            await db.flush()
            db.add(_video(ch, "stuck", str(p), downloaded_at=_old()))
            await db.commit()
        await retention.apply_retention()
        async with maker() as db:
            v = (await db.execute(select(Video))).scalar_one()
        await eng.dispose()
        assert v.status == "completed" and v.file_path == str(p)

    asyncio.run(run())


def test_missing_file_leaves_row_completed(tmp_path, monkeypatch):
    # e.g. the media share isn't mounted: don't forget a file that may come back
    async def run():
        from app.tasks import retention
        eng, maker = await _setup(monkeypatch)
        p = tmp_path / "unmounted" / "gone.mp4"
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         retention_days=30, health_status="unknown")
            db.add(ch)
            await db.flush()
            db.add(_video(ch, "gone", str(p), downloaded_at=_old()))
            await db.commit()
        await retention.apply_retention()
        async with maker() as db:
            v = (await db.execute(select(Video))).scalar_one()
        await eng.dispose()
        assert v.status == "completed" and v.file_path == str(p)

    asyncio.run(run())


def test_retention_preview_counts_expired_only(tmp_path, monkeypatch):
    async def run():
        from app.routers.channels import retention_preview
        eng, maker = await _setup(monkeypatch)
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         health_status="unknown")
            db.add(ch)
            await db.flush()
            db.add(_video(ch, "old1", "/x/a.mp4", downloaded_at=_old()))
            db.add(_video(ch, "old2", "/x/b.mp4", downloaded_at=_old()))
            db.add(_video(ch, "new", "/x/c.mp4", downloaded_at=datetime.now(timezone.utc).replace(tzinfo=None)))
            db.add(_video(ch, "nodate", "/x/d.mp4", downloaded_at=None))
            await db.commit()
            res = await retention_preview(ch.id, days=30, db=db)
            res_big = await retention_preview(ch.id, days=90, db=db)
        await eng.dispose()
        assert res == {"count": 2, "total_bytes": 20}
        assert res_big["count"] == 0

    asyncio.run(run())


def test_force_rescan_keeps_retention_deleted_video_skipped(tmp_path, monkeypatch):
    async def run():
        from app.routers.channels import force_rescan
        eng, maker = await _setup(monkeypatch)

        async def fake_scan(self, channel):
            for vid in ("gone", "fresh"):
                self.db.add(Video(
                    channel_id=channel.id, video_id=vid, title=vid, status="pending",
                    upload_date=date(2024, 1, 1), season=2024, episode=1,
                ))
            await self.db.commit()
            return 2

        monkeypatch.setattr(ChannelService, "scan_channel", fake_scan)
        async with maker() as db:
            ch = Channel(channel_id="c", channel_name="C", channel_url="u", platform="youtube",
                         download_dir=str(tmp_path), health_status="unknown")
            db.add(ch)
            await db.flush()
            db.add(_video(ch, "gone", None, status="skipped", downloaded_at=_old(), monitored=False))
            await db.commit()
            await force_rescan(ch.id, db=db)
            vids = {v.video_id: v for v in (await db.execute(select(Video))).scalars().all()}
            assert (await db.execute(select(DownloadQueue))).scalars().all() == []
        await eng.dispose()
        assert vids["gone"].status == "skipped" and vids["gone"].monitored is False
        assert vids["fresh"].status == "pending"

    asyncio.run(run())


def test_migration_skips_out_of_range_years(monkeypatch):
    async def run():
        eng = _engine()
        monkeypatch.setattr(database, "engine", eng)
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            for i in range(1, 5):
                db.add(Channel(id=i, channel_id=f"c{i}", channel_name="n", channel_url="u",
                               platform="youtube", health_status="unknown"))
            await db.commit()
        async with eng.begin() as conn:
            await conn.execute(text("ALTER TABLE channels DROP COLUMN download_from_date"))
            await conn.execute(text("ALTER TABLE channels DROP COLUMN retention_days"))
            await conn.execute(text("ALTER TABLE channels ADD COLUMN download_from_year INTEGER"))
            await conn.execute(text(
                "UPDATE channels SET download_from_year = CASE id WHEN 1 THEN 0 WHEN 2 THEN -5 "
                "WHEN 3 THEN 20000 ELSE 2020 END"
            ))
        await database.init_database()
        async with eng.connect() as conn:
            rows = (await conn.execute(text(
                "SELECT id, download_from_date FROM channels ORDER BY id"
            ))).all()
        await eng.dispose()
        assert [r[1] for r in rows] == [None, None, None, "2020-01-01"]

    asyncio.run(run())
