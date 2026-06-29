from app.services import ytdlp_service
from app.services.ytdlp_service import YtdlpService


class TestLoadCookiesForDomain:
    def _write_cookies(self, path):
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            ".rumble.com\tTRUE\t/\tTRUE\t9999999999\tsess\tabc123\n"
            ".youtube.com\tTRUE\t/\tTRUE\t9999999999\tSID\tyt456\n"
        )

    def test_filters_by_domain(self, tmp_path, monkeypatch):
        cookie_file = tmp_path / "cookies.txt"
        self._write_cookies(cookie_file)
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: cookie_file))
        assert YtdlpService._load_cookies_for_domain("rumble") == {"sess": "abc123"}
        assert YtdlpService._load_cookies_for_domain("youtube") == {"SID": "yt456"}

    def test_empty_when_no_cookie_file(self, tmp_path, monkeypatch):
        missing = tmp_path / "nope.txt"
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: missing))
        assert YtdlpService._load_cookies_for_domain("rumble") == {}


class TestRumbleHrefParsing:
    def test_extracts_relative_urls(self):
        html = (
            'foo,"relative_url":"/v3pyn3g-brush-creek-bridge-ducktown-tn.html","by":{"x":1} '
            'bar,"relative_url":"/vqi3qe-sea-creek-falls.html","by":{"y":2}'
        )
        hrefs = YtdlpService._parse_rumble_video_hrefs(html)
        assert hrefs == [
            "/v3pyn3g-brush-creek-bridge-ducktown-tn.html",
            "/vqi3qe-sea-creek-falls.html",
        ]

    def test_dedupes_and_preserves_order(self):
        html = (
            '"relative_url":"/vaaa-one.html" "relative_url":"/vbbb-two.html" '
            '"relative_url":"/vaaa-one.html"'
        )
        assert YtdlpService._parse_rumble_video_hrefs(html) == ["/vaaa-one.html", "/vbbb-two.html"]

    def test_ignores_channel_and_category_urls(self):
        html = '"relative_url":"/c/SomeChannel" "relative_url":"/category/news"'
        assert YtdlpService._parse_rumble_video_hrefs(html) == []

    def test_empty_when_no_match(self):
        assert YtdlpService._parse_rumble_video_hrefs("<html>nothing here</html>") == []


class TestRumbleChannelInfoParsing:
    def test_extracts_name_art_and_description(self):
        html = (
            '<html><head><title>Southern Adventures - the outdoors - Rumble</title></head>'
            '<body><h1>Southern Adventures</h1>'
            '<div class="channel-header--backsplash"><img src="https://cdn.rumble.cloud/banner.png"></div>'
            '<img class="channel-header--img rounded" '
            'src="https://hugh.cdn.rumble.cloud/video/z8/SouthernAdventures1.png"></body></html>'
        )
        info = YtdlpService._parse_rumble_channel_info(html)
        assert info["title"] == "Southern Adventures"
        assert info["thumbnail"].endswith("SouthernAdventures1.png")
        assert info["banner_url"] == "https://cdn.rumble.cloud/banner.png"
        assert info["description"] == "Southern Adventures - the outdoors"

    def test_skips_description_when_title_is_just_the_name(self):
        html = '<title>Southern Adventures - Rumble</title><h1>Southern Adventures</h1>'
        info = YtdlpService._parse_rumble_channel_info(html)
        assert "description" not in info

    def test_prefers_channel_header_title_class(self):
        html = '<span class="channel-header--title">Real Name</span><h1>Page Heading</h1>'
        assert YtdlpService._parse_rumble_channel_info(html)["title"] == "Real Name"

    def test_empty_when_nothing_present(self):
        assert YtdlpService._parse_rumble_channel_info("<html></html>") == {}


class TestRumbleScrapeEntryShape:
    def test_id_derived_from_slug_without_html(self):
        # Mirror the id-derivation the scan relies on: slug minus the .html suffix.
        href = "/v3pyn3g-brush-creek-bridge-ducktown-tn.html"
        slug = href.lstrip("/")
        vid_id = slug.split(".")[0] if "." in slug else slug
        assert vid_id == "v3pyn3g-brush-creek-bridge-ducktown-tn"


class TestCookieUserAgent:
    """The exporter embeds the browser UA as a '# User-Agent:' comment so a
    cf_clearance token (bound to that UA) can be replayed. Verify parsing."""

    def _use_cookie_file(self, tmp_path, monkeypatch, text):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(text)
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: cookie_file))

    def test_reads_embedded_user_agent(self, tmp_path, monkeypatch):
        self._use_cookie_file(tmp_path, monkeypatch,
            "# Netscape HTTP Cookie File\n"
            "# User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0\n"
            "\n.rumble.com\tTRUE\t/\tTRUE\t9999999999\tsess\tabc\n")
        assert YtdlpService._get_cookie_user_agent() == (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:140.0) Gecko/20100101 Firefox/140.0")

    def test_none_when_no_ua_comment(self, tmp_path, monkeypatch):
        self._use_cookie_file(tmp_path, monkeypatch,
            "# Netscape HTTP Cookie File\n.rumble.com\tTRUE\t/\tTRUE\t9999999999\tsess\tabc\n")
        assert YtdlpService._get_cookie_user_agent() is None

    def test_none_when_no_cookie_file(self, tmp_path, monkeypatch):
        missing = tmp_path / "nope.txt"
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: missing))
        assert YtdlpService._get_cookie_user_agent() is None


class TestRumbleScrapeImpersonation:
    """The cookie exporter only reads Firefox, and a cf_clearance token is bound to
    the UA that solved the challenge - so the scrape must impersonate Firefox AND
    replay the captured UA for exported cookies to validate. Lock both in."""

    class _Resp:
        def __init__(self):
            self.status_code = 200
            self.text = "<html>no videos</html>"

    def _capture_requests(self, monkeypatch):
        from curl_cffi import requests as cffi_requests
        calls = []

        def fake_get(*args, **kwargs):
            calls.append(kwargs)
            return self._Resp()

        monkeypatch.setattr(cffi_requests, "get", fake_get)
        return calls

    def test_channel_scrape_impersonates_firefox(self, monkeypatch):
        calls = self._capture_requests(monkeypatch)
        YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test")
        assert calls and all(c.get("impersonate") == "firefox" for c in calls)

    def test_channel_info_scrape_impersonates_firefox(self, monkeypatch):
        calls = self._capture_requests(monkeypatch)
        YtdlpService()._scrape_rumble_channel_info("https://rumble.com/c/Test")
        assert [c.get("impersonate") for c in calls] == ["firefox"]

    def test_scrape_replays_captured_user_agent(self, tmp_path, monkeypatch):
        cookie_file = tmp_path / "cookies.txt"
        cookie_file.write_text(
            "# Netscape HTTP Cookie File\n# User-Agent: FF-UA-140\n"
            "\n.rumble.com\tTRUE\t/\tTRUE\t9999999999\tsess\tabc\n")
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: cookie_file))
        calls = self._capture_requests(monkeypatch)
        YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test")
        assert calls and all(c.get("headers") == {"User-Agent": "FF-UA-140"} for c in calls)

    def test_scrape_sends_no_ua_header_without_cookies(self, tmp_path, monkeypatch):
        missing = tmp_path / "nope.txt"
        monkeypatch.setattr(type(ytdlp_service.settings), "cookies_path",
                            property(lambda self: missing))
        calls = self._capture_requests(monkeypatch)
        YtdlpService()._scrape_rumble_channel("https://rumble.com/c/Test")
        assert calls and all(c.get("headers") is None for c in calls)
