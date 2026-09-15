import pytest
from pydantic import ValidationError

from app.schemas import ChannelUpdate
from app.services.download_service import _ChannelData
from app.services.ytdlp_service import YtdlpService


class TestNoCodecIsByteIdentical:
    @pytest.mark.parametrize("quality", ["best", "2160p", "1080p", "720p", "480p", "999p"])
    def test_no_codec_matches_none(self, quality):
        assert YtdlpService._quality_to_format(quality) == YtdlpService._quality_to_format(quality, None)

    def test_best_literal(self):
        assert YtdlpService._quality_to_format("best") == "bestvideo*+bestaudio/bestvideo+bestaudio/best"

    def test_1080p_literal(self):
        assert YtdlpService._quality_to_format("1080p") == (
            "bestvideo*[height<=1080]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        )

    def test_unknown_codec_falls_back(self):
        assert YtdlpService._quality_to_format("1080p", "av1") == YtdlpService._quality_to_format("1080p")


class TestCodecSelectors:
    def test_best_h264_literal(self):
        assert YtdlpService._quality_to_format("best", "h264") == (
            "bestvideo*[vcodec^=avc1]+bestaudio/bestvideo[vcodec^=avc1]+bestaudio/"
            "bestvideo*+bestaudio/bestvideo+bestaudio/best"
        )

    def test_best_vp9_literal(self):
        assert YtdlpService._quality_to_format("best", "vp9") == (
            "bestvideo*[vcodec~='^vp0?9']+bestaudio/bestvideo[vcodec~='^vp0?9']+bestaudio/"
            "bestvideo*+bestaudio/bestvideo+bestaudio/best"
        )

    def test_1080p_h264_literal(self):
        assert YtdlpService._quality_to_format("1080p", "h264") == (
            "bestvideo*[height<=1080][vcodec^=avc1]+bestaudio/bestvideo[height<=1080][vcodec^=avc1]+bestaudio/"
            "bestvideo*[height<=1080]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        )

    def test_1080p_vp9_literal(self):
        assert YtdlpService._quality_to_format("1080p", "vp9") == (
            "bestvideo*[height<=1080][vcodec~='^vp0?9']+bestaudio/bestvideo[height<=1080][vcodec~='^vp0?9']+bestaudio/"
            "bestvideo*[height<=1080]+bestaudio/bestvideo[height<=1080]+bestaudio/best[height<=1080]/best"
        )

    @pytest.mark.parametrize("quality", ["best", "2160p", "1080p", "720p", "480p"])
    @pytest.mark.parametrize("codec", ["h264", "vp9"])
    def test_matrix_shape(self, quality, codec):
        cfilter = YtdlpService._CODEC_FILTERS[codec]
        result = YtdlpService._quality_to_format(quality, codec)
        assert result.endswith(YtdlpService._quality_to_format(quality))
        assert result.startswith("bestvideo*")
        assert result.count(cfilter) == 2
        assert result.count("bestvideo*") == 2


class TestDownloadOptsWiring:
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

    def test_codec_wired_into_format(self, monkeypatch):
        opts = self._capture_opts(monkeypatch, quality="1080p", codec="h264")
        assert opts["format"] == YtdlpService._quality_to_format("1080p", "h264")
        assert opts["merge_output_format"] == "mp4"

    def test_no_codec_wired_unchanged(self, monkeypatch):
        opts = self._capture_opts(monkeypatch, quality="1080p")
        assert opts["format"] == YtdlpService._quality_to_format("1080p")


class TestChannelDataCarriesCodec:
    def test_slot_present(self):
        assert "preferred_codec" in _ChannelData.__slots__

    def test_value_roundtrips(self):
        cdata = _ChannelData(preferred_codec="vp9")
        assert cdata.preferred_codec == "vp9"


class TestSchemaPattern:
    def test_h264_valid(self):
        assert ChannelUpdate(preferred_codec="h264").preferred_codec == "h264"

    def test_vp9_valid(self):
        assert ChannelUpdate(preferred_codec="vp9").preferred_codec == "vp9"

    def test_invalid_rejected(self):
        with pytest.raises(ValidationError):
            ChannelUpdate(preferred_codec="av1")

    def test_default_is_none(self):
        assert ChannelUpdate().preferred_codec is None
