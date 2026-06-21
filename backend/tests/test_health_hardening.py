from app.services.ytdlp_service import YtdlpService


class TestJsRuntimeStatus:
    def test_none_when_no_runtime(self, monkeypatch):
        monkeypatch.setattr("app.services.ytdlp_service.shutil.which", lambda n: None)
        assert YtdlpService.get_js_runtime_status() is None

    def test_detects_deno_with_version(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ytdlp_service.shutil.which",
            lambda n: "/usr/local/bin/deno" if n == "deno" else None,
        )

        class _Res:
            stdout = "deno 2.8.3 (stable, release, x86_64)\nv8 14.0\ntypescript 5.9"
            stderr = ""

        monkeypatch.setattr("app.services.ytdlp_service.subprocess.run", lambda *a, **k: _Res())
        assert YtdlpService.get_js_runtime_status() == "deno 2.8.3 (stable, release, x86_64)"

    def test_falls_back_to_name_when_version_probe_fails(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.ytdlp_service.shutil.which",
            lambda n: "/usr/local/bin/bun" if n == "bun" else None,
        )

        def _boom(*a, **k):
            raise OSError("nope")

        monkeypatch.setattr("app.services.ytdlp_service.subprocess.run", _boom)
        assert YtdlpService.get_js_runtime_status() == "bun"


class TestHealthFailureMessage:
    def test_flags_missing_runtime(self, monkeypatch):
        monkeypatch.setattr(YtdlpService, "get_js_runtime_status", staticmethod(lambda: None))
        msg = YtdlpService()._format_health_failure("No downloadable video formats resolved")
        assert "Deno" in msg
        assert "No downloadable video formats resolved" in msg

    def test_includes_runtime_when_present(self, monkeypatch):
        monkeypatch.setattr(YtdlpService, "get_js_runtime_status", staticmethod(lambda: "deno 2.8.3"))
        msg = YtdlpService()._format_health_failure("boom")
        assert "deno 2.8.3" in msg


class TestVersionComparison:
    def test_zero_padding_treated_equal(self):
        # Loaded module form ('2026.06.09') vs PyPI's normalized form ('2026.6.9')
        # must NOT be seen as outdated, or the updater would loop forever.
        assert YtdlpService.is_outdated("2026.06.09", "2026.6.9") is False

    def test_identical_not_outdated(self):
        assert YtdlpService.is_outdated("2026.06.09", "2026.06.09") is False

    def test_newer_patch_is_outdated(self):
        # String comparison would wrongly call "2026.6.20" < "2026.6.9"; tuple compare is correct.
        assert YtdlpService.is_outdated("2026.6.9", "2026.6.20") is True

    def test_newer_month_is_outdated(self):
        assert YtdlpService.is_outdated("2026.06.09", "2026.7.1") is True

    def test_current_ahead_not_outdated(self):
        assert YtdlpService.is_outdated("2026.6.20", "2026.6.9") is False

    def test_unknown_current_not_outdated(self):
        assert YtdlpService.is_outdated("unknown", "2026.6.9") is False

    def test_empty_latest_not_outdated(self):
        assert YtdlpService.is_outdated("2026.6.9", "") is False
