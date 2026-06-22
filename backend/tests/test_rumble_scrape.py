from app.services.ytdlp_service import YtdlpService


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
            '<html><head><title>Southern Adventures - Rumble</title></head>'
            '<body><h1>Southern Adventures</h1>'
            '<img class="channel-header--img rounded" '
            'src="https://hugh.cdn.rumble.cloud/video/z8/SouthernAdventures1.png"></body></html>'
        )
        info = YtdlpService._parse_rumble_channel_info(html)
        assert info["title"] == "Southern Adventures"
        assert info["thumbnail"].endswith("SouthernAdventures1.png")
        assert info["description"] == "Southern Adventures"

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
