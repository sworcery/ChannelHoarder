import time

from app.services import storage_service


def _reset_cache(monkeypatch):
    monkeypatch.setattr(storage_service, "_cached_usage", None)
    monkeypatch.setattr(storage_service, "_cache_computed_at", 0.0)
    monkeypatch.setattr(storage_service, "_refresh_in_flight", False)


class TestStorageUsageCache:
    def test_first_call_computes_then_serves_cached(self, tmp_path, monkeypatch):
        _reset_cache(monkeypatch)
        monkeypatch.setattr(storage_service.settings, "DOWNLOAD_DIR", str(tmp_path))
        (tmp_path / "ChanA").mkdir()
        (tmp_path / "ChanA" / "v.mp4").write_bytes(b"x" * 100)

        calls = {"n": 0}
        real_compute = storage_service._compute_storage_usage

        def counting_compute(custom_dirs=None):
            calls["n"] += 1
            return real_compute(custom_dirs)

        monkeypatch.setattr(storage_service, "_compute_storage_usage", counting_compute)

        first = storage_service.get_storage_usage()
        assert first["channels"] == {"ChanA": 100}
        assert calls["n"] == 1

        # Second call within TTL: served from cache, no recompute.
        second = storage_service.get_storage_usage()
        assert second["channels"] == {"ChanA": 100}
        assert calls["n"] == 1

    def test_stale_cache_serves_immediately_and_refreshes_in_background(self, tmp_path, monkeypatch):
        _reset_cache(monkeypatch)
        monkeypatch.setattr(storage_service.settings, "DOWNLOAD_DIR", str(tmp_path))

        storage_service.get_storage_usage()  # prime
        # Age the cache past the TTL
        monkeypatch.setattr(storage_service, "_cache_computed_at",
                            time.monotonic() - storage_service._CACHE_TTL_SECONDS - 1)

        # Add new content the stale cache doesn't know about
        (tmp_path / "ChanB").mkdir()
        (tmp_path / "ChanB" / "v.mp4").write_bytes(b"y" * 50)

        stale = storage_service.get_storage_usage()  # returns stale instantly
        assert "ChanB" not in stale["channels"]

        # Background refresh should land shortly
        for _ in range(50):
            time.sleep(0.05)
            if storage_service._cached_usage and "ChanB" in storage_service._cached_usage["channels"]:
                break
        assert storage_service._cached_usage["channels"].get("ChanB") == 50

    def test_hidden_dirs_excluded_from_breakdown(self, tmp_path, monkeypatch):
        _reset_cache(monkeypatch)
        monkeypatch.setattr(storage_service.settings, "DOWNLOAD_DIR", str(tmp_path))
        (tmp_path / ".channelhoarder-tmp" / "attempt1").mkdir(parents=True)
        (tmp_path / ".channelhoarder-tmp" / "attempt1" / "v.part").write_bytes(b"z" * 10)
        (tmp_path / "RealChan").mkdir()

        result = storage_service.get_storage_usage()
        assert ".channelhoarder-tmp" not in result["channels"]
        assert "RealChan" in result["channels"]

    def test_disk_numbers_always_live(self, tmp_path, monkeypatch):
        _reset_cache(monkeypatch)
        monkeypatch.setattr(storage_service.settings, "DOWNLOAD_DIR", str(tmp_path))
        storage_service.get_storage_usage()
        result = storage_service.get_storage_usage()  # cached path
        assert result["disk_total"] > 0
