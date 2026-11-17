import asyncio
import errno
import os
import shutil
import time

import pytest

from app.services import download_service
from app.services.download_service import (
    attempt_temp_dir,
    create_attempt_dir,
    move_finished_files,
)


class TestAttemptTempDir:
    def test_default_under_download_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr(download_service.settings, "TEMP_DIR", "")
        d = attempt_temp_dir(str(tmp_path / "dl"))
        assert os.path.dirname(d) == str(tmp_path / "dl" / ".channelhoarder-tmp")

    def test_temp_dir_setting_used(self, tmp_path, monkeypatch):
        monkeypatch.setattr(download_service.settings, "TEMP_DIR", str(tmp_path / "ssd"))
        d = attempt_temp_dir(str(tmp_path / "dl"))
        assert os.path.dirname(d) == str(tmp_path / "ssd" / ".channelhoarder-tmp")

    def test_unique_per_attempt(self, tmp_path):
        assert attempt_temp_dir(str(tmp_path)) != attempt_temp_dir(str(tmp_path))


class TestMove:
    def _setup(self, tmp_path):
        tmp = tmp_path / "tmp"
        final = tmp_path / "final"
        tmp.mkdir()
        final.mkdir()
        src = tmp / "v.mp4"
        src.write_bytes(b"video-data")
        return tmp, final, src

    def _exdev(self, monkeypatch, src):
        real = os.replace

        def fake(a, b, *args, **kw):
            if str(a) == str(src):
                raise OSError(errno.EXDEV, "cross-device")
            return real(a, b, *args, **kw)

        monkeypatch.setattr(os, "replace", fake)

    def test_same_fs_rename(self, tmp_path):
        tmp, final, src = self._setup(tmp_path)
        move_finished_files(str(tmp), str(final))
        assert (final / "v.mp4").read_bytes() == b"video-data"
        assert not src.exists()

    def test_cross_device_move(self, tmp_path, monkeypatch):
        tmp, final, src = self._setup(tmp_path)
        self._exdev(monkeypatch, src)
        move_finished_files(str(tmp), str(final))
        assert (final / "v.mp4").read_bytes() == b"video-data"
        assert not src.exists()
        assert not list(final.glob("*.chmove"))

    def test_failed_copy_leaves_nothing(self, tmp_path, monkeypatch):
        tmp, final, src = self._setup(tmp_path)
        self._exdev(monkeypatch, src)

        def bad_copy(s, d, *a, **kw):
            with open(d, "wb") as fh:
                fh.write(b"half")
            raise OSError(errno.ENOSPC, "disk full")

        monkeypatch.setattr(shutil, "copyfile", bad_copy)
        with pytest.raises(OSError):
            move_finished_files(str(tmp), str(final))
        assert not (final / "v.mp4").exists()
        assert not list(final.glob("*.chmove"))
        assert src.read_bytes() == b"video-data"

    def test_drive_refusing_times_and_permissions_still_moves(self, tmp_path, monkeypatch):
        # NTFS/exFAT external drives can reject chmod/utime; the video must still land.
        tmp, final, src = self._setup(tmp_path)
        self._exdev(monkeypatch, src)

        def no_stat(*a, **kw):
            raise PermissionError(errno.EPERM, "operation not permitted")

        monkeypatch.setattr(shutil, "copystat", no_stat)
        move_finished_files(str(tmp), str(final))
        assert (final / "v.mp4").read_bytes() == b"video-data"
        assert not src.exists()
        assert not list(final.glob("*.chmove"))

    def test_cross_device_move_keeps_mtime(self, tmp_path, monkeypatch):
        tmp, final, src = self._setup(tmp_path)
        os.utime(src, (1_600_000_000, 1_600_000_000))
        self._exdev(monkeypatch, src)
        move_finished_files(str(tmp), str(final))
        assert int((final / "v.mp4").stat().st_mtime) == 1_600_000_000


class TestSweeper:
    def test_sweeps_old_attempt_under_temp_dir(self, tmp_path, monkeypatch):
        from app.tasks import temp_cleanup

        ssd = tmp_path / "ssd"
        old = ssd / ".channelhoarder-tmp" / "old"
        old.mkdir(parents=True)
        stale = time.time() - 7 * 60 * 60
        os.utime(old, (stale, stale))
        other = ssd / "keepme"
        other.mkdir()

        monkeypatch.setattr(temp_cleanup.settings, "DOWNLOAD_DIR", str(tmp_path / "dl"))
        monkeypatch.setattr(temp_cleanup.settings, "TEMP_DIR", str(ssd))
        monkeypatch.setattr(temp_cleanup, "async_session", lambda: (_ for _ in ()).throw(RuntimeError))
        asyncio.run(temp_cleanup.cleanup_download_temp())

        assert not old.exists()
        assert other.exists()


class TestUnwritableTempDir:
    def test_clear_error_names_setting_and_path(self, tmp_path, monkeypatch):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        monkeypatch.setattr(download_service.settings, "TEMP_DIR", str(blocker))
        d = attempt_temp_dir(str(tmp_path))
        with pytest.raises(Exception) as ei:
            create_attempt_dir(d)
        msg = str(ei.value)
        assert "TEMP_DIR" in msg and d in msg


class TestDownloadWithTempDir:
    """Run DownloadService.download_video end to end with a fake yt-dlp."""

    async def _run(self, tmp_path, monkeypatch, fail_move=False):
        from datetime import date

        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
        from sqlalchemy.pool import StaticPool

        import app.services.settings_service as settings_service
        from app.database import Base
        from app.models import Channel, DownloadQueue, Video

        eng = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                  connect_args={"check_same_thread": False})
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        monkeypatch.setattr(download_service, "async_session", maker)
        monkeypatch.setattr(settings_service, "async_session", maker, raising=False)
        monkeypatch.setattr("app.database.async_session", maker)

        dl, ssd = tmp_path / "dl", tmp_path / "ssd"
        dl.mkdir()
        ssd.mkdir()
        monkeypatch.setattr(download_service.settings, "DOWNLOAD_DIR", str(dl))
        monkeypatch.setattr(download_service.settings, "TEMP_DIR", str(ssd))

        async def no_wait():
            return None

        monkeypatch.setattr(download_service, "wait_for_rate_limit", no_wait)
        real_sleep = asyncio.sleep
        monkeypatch.setattr(asyncio, "sleep", lambda s: real_sleep(0))
        if fail_move:
            def bad_move(temp_dir, final_parent):
                raise OSError(errno.ENOSPC, "No space left on device")
            monkeypatch.setattr(download_service, "move_finished_files", bad_move)

        async with maker() as db:
            ch = Channel(channel_id="UC1", channel_name="Chan", channel_url="https://www.youtube.com/channel/UC1",
                         platform="youtube", health_status="healthy")
            db.add(ch)
            await db.flush()
            v = Video(channel_id=ch.id, video_id="abc", title="A video", upload_date=date(2026, 1, 2),
                      season=2026, episode=1, status="queued")
            db.add(v)
            await db.flush()
            q = DownloadQueue(video_id=v.id)
            db.add(q)
            await db.commit()
            ids = (v.id, ch.id, q.id)

        svc = download_service.DownloadService()

        def fake_download(url, output_path, temp_dir=None, **kw):
            with open(os.path.join(temp_dir, os.path.basename(output_path) + ".mp4"), "wb") as fh:
                fh.write(b"video-bytes")
            return {"id": "abc", "height": 1080, "ext": "mp4"}

        svc.ytdlp.download_video = fake_download
        ok = await svc.download_video(*ids)
        async with maker() as db:
            status = (await db.execute(select(Video.status).where(Video.id == ids[0]))).scalar_one()
        await eng.dispose()
        return ok, status, dl, ssd

    def test_finished_video_lands_in_download_folder(self, tmp_path, monkeypatch):
        ok, status, dl, ssd = asyncio.run(self._run(tmp_path, monkeypatch))
        assert ok and status == "completed"
        assert [p.name for p in dl.rglob("*.mp4")] and not list(dl.rglob("*.chmove"))
        assert not any((ssd / ".channelhoarder-tmp").iterdir())

    def test_failed_move_leaves_nothing_on_the_temp_drive(self, tmp_path, monkeypatch):
        ok, status, dl, ssd = asyncio.run(self._run(tmp_path, monkeypatch, fail_move=True))
        assert not ok and status != "completed"
        assert not list(dl.rglob("*.mp4"))
        assert not any((ssd / ".channelhoarder-tmp").iterdir())
