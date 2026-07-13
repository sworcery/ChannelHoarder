import os
import time
from pathlib import Path

from app.services import download_service
from app.services.download_service import is_download_active
from app.services.ytdlp_service import YtdlpService


class TestActiveDownloadsSet:
    def test_inactive_when_empty(self):
        download_service._active_downloads.clear()
        assert is_download_active() is False

    def test_active_until_last_download_finishes(self):
        s = download_service._active_downloads
        s.clear()
        s.add("vidA")
        s.add("vidB")
        assert is_download_active() is True
        s.discard("vidA")  # first finisher must NOT flip the flag
        assert is_download_active() is True
        s.discard("vidB")
        assert is_download_active() is False

    def test_discard_is_idempotent(self):
        s = download_service._active_downloads
        s.clear()
        s.discard("never-added")  # must not raise
        assert is_download_active() is False


class TestTempDirDownloadOpts:
    """With temp_dir set, yt-dlp must be pointed entirely inside it so an orphaned
    stalled attempt can never write over a retry's files."""

    def _capture_opts(self, monkeypatch, **kwargs):
        import app.services.ytdlp_service as ys
        captured = {}

        class _FakeYDL:
            def __init__(self, opts):
                captured.update(opts)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def extract_info(self, url, download=True):
                return {"id": "x"}

        monkeypatch.setattr(ys.yt_dlp, "YoutubeDL", _FakeYDL)
        YtdlpService().download_video("https://example.com/v", "/downloads/Chan/Season 2024/S2024E001 - T", **kwargs)
        return captured

    def test_temp_dir_roots_outtmpl_and_paths(self, monkeypatch):
        opts = self._capture_opts(monkeypatch, temp_dir="/downloads/.channelhoarder-tmp/abc123")
        assert opts["paths"] == {"home": "/downloads/.channelhoarder-tmp/abc123",
                                 "temp": "/downloads/.channelhoarder-tmp/abc123"}
        assert opts["outtmpl"] == "/downloads/.channelhoarder-tmp/abc123/S2024E001 - T.%(ext)s"

    def test_without_temp_dir_behavior_unchanged(self, monkeypatch):
        opts = self._capture_opts(monkeypatch)
        assert "paths" not in opts
        assert opts["outtmpl"] == "/downloads/Chan/Season 2024/S2024E001 - T.%(ext)s"


class TestTempCleanup:
    async def _run_cleanup(self, monkeypatch, tmp_path):
        from app.tasks import temp_cleanup

        async def no_db_roots():
            class _Empty:
                async def __aenter__(self):
                    raise RuntimeError("skip db")
                async def __aexit__(self, *a):
                    return False
            return _Empty()

        monkeypatch.setattr(temp_cleanup.settings, "DOWNLOAD_DIR", str(tmp_path))
        # Make the DB lookup a no-op failure path (logged, tolerated)
        monkeypatch.setattr(temp_cleanup, "async_session", lambda: (_ for _ in ()).throw(RuntimeError))
        await temp_cleanup.cleanup_download_temp()

    def test_sweeps_old_keeps_fresh(self, tmp_path, monkeypatch):
        import asyncio
        tmp_root = tmp_path / ".channelhoarder-tmp"
        old = tmp_root / "old-attempt"
        fresh = tmp_root / "fresh-attempt"
        old.mkdir(parents=True)
        fresh.mkdir(parents=True)
        (old / "video.part").write_bytes(b"x")
        stale_time = time.time() - 7 * 60 * 60
        os.utime(old, (stale_time, stale_time))

        asyncio.run(self._run_cleanup(monkeypatch, tmp_path))

        assert not old.exists(), "7h-old attempt dir must be swept"
        assert fresh.exists(), "fresh attempt dir must be kept"

    def test_no_temp_root_is_noop(self, tmp_path, monkeypatch):
        import asyncio
        asyncio.run(self._run_cleanup(monkeypatch, tmp_path))  # must not raise
        assert not (Path(tmp_path) / ".channelhoarder-tmp").exists()
