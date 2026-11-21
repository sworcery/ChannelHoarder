import pytest
from yt_dlp import YoutubeDL
from yt_dlp.extractor.rumble import RumbleEmbedIE
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.utils import ExtractorError

from app.services import rumble_fallback

EMBEDJS = "https://rumble.com/embedJS/u3/?request=video&ver=2&v=abc123"

PAGE = (
    '<script>var x=1;Zq$.f["abc123"]={"fps":30,"w":1920,"h":1080,'
    '"u":{"mp4":{"url":"https://x/a.mp4"}},"ua":{"tar":{"360":{"url":"https://x/b.mp4"}}},'
    'loaded:v()};Zq$.f["other"]={"ua":{}}</script>'
)


def _http_error(status):
    class _Resp:
        def __init__(self):
            self.status = status
            self.reason = "x"
            self.url = EMBEDJS
            self.headers = {}

        def read(self, *a):
            return b""

        def close(self):
            pass

    return ExtractorError("Unable to download JSON metadata", cause=HTTPError(_Resp()))


@pytest.fixture
def ie(monkeypatch):
    state = {"error": _http_error(403)}

    def fake_original(self, url_or_request, video_id, *a, **kw):
        raise state["error"]

    monkeypatch.setattr(RumbleEmbedIE, "_download_json", fake_original)
    monkeypatch.setattr(rumble_fallback, "_installed", False)
    rumble_fallback.install()
    ydl = YoutubeDL({"quiet": True})
    inst = RumbleEmbedIE(ydl)
    inst._state = state
    yield inst
    ydl.close()


def _serve(monkeypatch, ie, page):
    calls = []

    def fake_webpage(url, video_id, *a, **kw):
        calls.append(url)
        return page

    monkeypatch.setattr(ie, "_download_webpage", fake_webpage)
    return calls


def test_fallback_parses_embed_page(monkeypatch, ie):
    calls = _serve(monkeypatch, ie, PAGE)
    data = ie._download_json(EMBEDJS, "abc123")
    assert calls == ["https://rumble.com/embed/abc123/"]
    assert data["w"] == 1920
    assert data["ua"]["tar"]["360"]["url"] == "https://x/b.mp4"


def test_non_403_reraises(monkeypatch, ie):
    ie._state["error"] = _http_error(500)
    calls = _serve(monkeypatch, ie, PAGE)
    with pytest.raises(ExtractorError):
        ie._download_json(EMBEDJS, "abc123")
    assert calls == []


def test_non_embedjs_url_reraises(monkeypatch, ie):
    calls = _serve(monkeypatch, ie, PAGE)
    with pytest.raises(ExtractorError):
        ie._download_json("https://rumble.com/other", "abc123")
    assert calls == []


def test_page_without_data_reraises_original(monkeypatch, ie):
    _serve(monkeypatch, ie, "<html>nothing here</html>")
    with pytest.raises(ExtractorError) as exc:
        ie._download_json(EMBEDJS, "abc123")
    assert exc.value is ie._state["error"]


def test_data_without_formats_reraises(monkeypatch, ie):
    _serve(monkeypatch, ie, 'a.f["abc123"]={"fps":30,loaded:v()}')
    with pytest.raises(ExtractorError):
        ie._download_json(EMBEDJS, "abc123")


def test_install_twice_patches_once(monkeypatch):
    monkeypatch.setattr(rumble_fallback, "_installed", False)
    monkeypatch.setattr(RumbleEmbedIE, "_download_json", RumbleEmbedIE._download_json)
    rumble_fallback.install()
    first = RumbleEmbedIE._download_json
    rumble_fallback.install()
    assert RumbleEmbedIE._download_json is first


def test_quick_download_info_opts_impersonate_rumble(monkeypatch):
    pytest.importorskip("curl_cffi")
    from app.services import ytdlp_service

    seen = {}

    class FakeYDL:
        def __init__(self, opts):
            seen.update(opts)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"id": "x"}

    monkeypatch.setattr(ytdlp_service.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(ytdlp_service, "_get_impersonate_target", lambda: "firefox")
    ytdlp_service.YtdlpService().get_video_info_by_url("https://rumble.com/v711u2y")
    assert seen.get("impersonate") == "firefox"
