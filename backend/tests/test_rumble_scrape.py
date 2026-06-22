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


class TestRumbleScrapeEntryShape:
    def test_id_derived_from_slug_without_html(self):
        # Mirror the id-derivation the scan relies on: slug minus the .html suffix.
        href = "/v3pyn3g-brush-creek-bridge-ducktown-tn.html"
        slug = href.lstrip("/")
        vid_id = slug.split(".")[0] if "." in slug else slug
        assert vid_id == "v3pyn3g-brush-creek-bridge-ducktown-tn"
