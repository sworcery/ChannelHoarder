import pytest


class TestCurlCffiCompatibility:
    def test_curl_cffi_importable(self):
        import curl_cffi
        assert curl_cffi.__version__

    def test_curl_cffi_version_compatible_with_ytdlp(self):
        import curl_cffi
        from packaging.version import Version

        v = Version(curl_cffi.__version__)
        assert v == Version("0.5.10") or (
            Version("0.10.0") <= v < Version("0.15.0")
        ), f"curl_cffi {v} is outside yt-dlp's supported range (0.5.10, 0.10.x-0.14.x)"

    def test_ytdlp_curlcffi_handler_loads(self):
        from yt_dlp.networking._curlcffi import CurlCFFIRH
        assert CurlCFFIRH is not None

    def test_ytdlp_registers_curlcffi_handler(self):
        from yt_dlp import YoutubeDL

        ydl = YoutubeDL({"quiet": True})
        handler_types = [type(h).__name__ for h in ydl._request_director.handlers.values()]
        ydl.close()
        assert "CurlCFFIRH" in handler_types

    def test_chrome_impersonation_targets_available(self):
        from yt_dlp import YoutubeDL

        ydl = YoutubeDL({"quiet": True})
        targets = []
        for rh in ydl._request_director.handlers.values():
            if hasattr(rh, "supported_targets"):
                targets.extend(str(t) for t in rh.supported_targets)
        ydl.close()
        chrome_targets = [t for t in targets if "chrome" in t.lower()]
        assert chrome_targets, f"No chrome targets found. Available: {targets}"

    def test_ytdlp_accepts_impersonate_target(self):
        from yt_dlp import YoutubeDL
        from yt_dlp.networking.impersonate import ImpersonateTarget

        target = ImpersonateTarget.from_str("chrome")
        ydl = YoutubeDL({"quiet": True, "impersonate": target})
        ydl.close()


class TestImpersonationHelper:
    @pytest.fixture(autouse=True)
    def _reset_cache(self):
        import app.services.ytdlp_service as mod
        mod._impersonate_checked = False
        mod._impersonate_target = None

    def test_get_impersonate_target_returns_target(self):
        from app.services.ytdlp_service import _get_impersonate_target

        target = _get_impersonate_target()
        assert target is not None

    def test_get_impersonate_target_type(self):
        from yt_dlp.networking.impersonate import ImpersonateTarget
        from app.services.ytdlp_service import _get_impersonate_target

        target = _get_impersonate_target()
        assert isinstance(target, ImpersonateTarget)

    def test_get_impersonate_target_accepted_by_ytdlp(self):
        from yt_dlp import YoutubeDL
        from app.services.ytdlp_service import _get_impersonate_target

        target = _get_impersonate_target()
        ydl = YoutubeDL({"quiet": True, "impersonate": target})
        ydl.close()
