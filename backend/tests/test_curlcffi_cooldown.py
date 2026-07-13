import time

from app.services import ytdlp_service
from app.services.ytdlp_service import (
    YtdlpService,
    _is_curlcffi_connection_error,
    _curlcffi_cooling_down,
)


class _CodeError(Exception):
    def __init__(self, msg, code=None):
        super().__init__(msg)
        self.code = code


class TestConnectionErrorDetection:
    def test_detects_dns_message(self):
        e = Exception("Failed to perform, curl: (6) Could not resolve host: rumble.com")
        assert _is_curlcffi_connection_error(e) is True

    def test_detects_by_curl_code(self):
        assert _is_curlcffi_connection_error(_CodeError("boom", code=6)) is True
        assert _is_curlcffi_connection_error(_CodeError("boom", code=7)) is True

    def test_ignores_generic_error(self):
        assert _is_curlcffi_connection_error(Exception("some HTML parse error")) is False
        assert _is_curlcffi_connection_error(_CodeError("bad", code=42)) is False


class TestScrapeCooldown:
    def test_scrape_skipped_while_cooling_down(self, monkeypatch):
        # Force an active cooldown; the scrape must return [] without touching curl_cffi.
        monkeypatch.setattr(ytdlp_service, "_curlcffi_cooldown_until", time.monotonic() + 60)
        from curl_cffi import requests as cffi_requests

        def boom(*a, **k):
            raise AssertionError("curl_cffi must NOT be called during cooldown")

        monkeypatch.setattr(cffi_requests, "get", boom)
        assert YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test") == []

    def test_connection_error_trips_cooldown(self, monkeypatch):
        monkeypatch.setattr(ytdlp_service, "_curlcffi_cooldown_until", 0.0)
        from curl_cffi import requests as cffi_requests

        def dns_fail(*a, **k):
            raise Exception("Failed to perform, curl: (6) Could not resolve host: rumble.com")

        monkeypatch.setattr(cffi_requests, "get", dns_fail)
        result = YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test")
        assert result == []
        assert _curlcffi_cooling_down() is True

    def test_non_network_error_does_not_trip_cooldown(self, monkeypatch):
        monkeypatch.setattr(ytdlp_service, "_curlcffi_cooldown_until", 0.0)
        from curl_cffi import requests as cffi_requests

        def other_fail(*a, **k):
            raise Exception("unexpected parse failure")

        monkeypatch.setattr(cffi_requests, "get", other_fail)
        YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test")
        assert _curlcffi_cooling_down() is False

    def test_get_video_info_skipped_for_nonyoutube_during_cooldown(self, monkeypatch):
        # The per-video Rumble scan path must not re-enter yt-dlp/curl_cffi while cooling down.
        monkeypatch.setattr(ytdlp_service, "_curlcffi_cooldown_until", time.monotonic() + 60)

        def boom(*a, **k):
            raise AssertionError("yt-dlp must NOT be invoked during cooldown")

        monkeypatch.setattr(ytdlp_service.yt_dlp, "YoutubeDL", boom)
        assert YtdlpService().get_video_info("vabc123", platform="rumble") is None

    def test_get_video_info_youtube_ignores_cooldown(self, monkeypatch):
        # YouTube doesn't use curl_cffi, so the cooldown must not block it.
        monkeypatch.setattr(ytdlp_service, "_curlcffi_cooldown_until", time.monotonic() + 60)
        called = {"n": 0}

        class _FakeYDL:
            def __init__(self, opts):
                called["n"] += 1
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def extract_info(self, url, download=False):
                return {"id": "x"}

        monkeypatch.setattr(ytdlp_service.yt_dlp, "YoutubeDL", _FakeYDL)
        assert YtdlpService().get_video_info("x", platform="youtube") == {"id": "x"}
        assert called["n"] == 1
