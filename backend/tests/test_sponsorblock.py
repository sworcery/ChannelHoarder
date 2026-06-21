from app.services.ytdlp_service import YtdlpService


def _keys(pps):
    return [p["key"] for p in pps]


class TestSponsorBlockPostprocessors:
    def test_off_adds_no_sponsorblock(self):
        pps = YtdlpService._build_postprocessors(False, "off", "youtube")
        assert "SponsorBlock" not in _keys(pps)
        assert "ModifyChapters" not in _keys(pps)

    def test_mark_adds_sponsorblock_and_chapters_metadata(self):
        pps = YtdlpService._build_postprocessors(False, "mark", "youtube")
        keys = _keys(pps)
        assert "SponsorBlock" in keys
        assert "ModifyChapters" not in keys  # mark must not remove
        assert "FFmpegMetadata" in keys  # marked segments embedded as chapters

    def test_remove_adds_modifychapters(self):
        pps = YtdlpService._build_postprocessors(False, "remove", "youtube")
        keys = _keys(pps)
        assert "SponsorBlock" in keys
        assert "ModifyChapters" in keys
        mc = next(p for p in pps if p["key"] == "ModifyChapters")
        assert mc["remove_sponsor_segments"]  # non-empty category list

    def test_sponsorblock_runs_before_download_via_when(self):
        pps = YtdlpService._build_postprocessors(False, "mark", "youtube")
        sb = next(p for p in pps if p["key"] == "SponsorBlock")
        assert sb["when"] == "after_filter"

    def test_skipped_for_non_youtube(self):
        pps = YtdlpService._build_postprocessors(False, "remove", "rumble")
        keys = _keys(pps)
        assert "SponsorBlock" not in keys
        assert "ModifyChapters" not in keys

    def test_chapters_only_without_sponsorblock(self):
        pps = YtdlpService._build_postprocessors(True, "off", "youtube")
        keys = _keys(pps)
        assert "FFmpegMetadata" in keys
        assert "SponsorBlock" not in keys

    def test_base_postprocessors_always_present(self):
        pps = YtdlpService._build_postprocessors(False, "off", "youtube")
        keys = _keys(pps)
        assert "FFmpegVideoConvertor" in keys
        assert "EmbedThumbnail" in keys
