from app.services.channel_service import _is_unavailable_entry


class TestUnavailableEntryByTitle:
    def test_private_video_marker(self):
        assert _is_unavailable_entry({"id": "x", "title": "[Private video]"}) is True

    def test_deleted_video_marker(self):
        assert _is_unavailable_entry({"id": "x", "title": "[Deleted video]"}) is True

    def test_unavailable_video_marker(self):
        assert _is_unavailable_entry({"id": "x", "title": "[Unavailable video]"}) is True

    def test_marker_case_insensitive(self):
        assert _is_unavailable_entry({"id": "x", "title": "[private VIDEO]"}) is True

    def test_marker_with_surrounding_whitespace(self):
        assert _is_unavailable_entry({"id": "x", "title": "  [Deleted video]  "}) is True

    def test_normal_title_kept(self):
        assert _is_unavailable_entry({"id": "x", "title": "My Real Video"}) is False

    def test_title_containing_word_private_kept(self):
        assert _is_unavailable_entry({"id": "x", "title": "Private Server Tour"}) is False


class TestUnavailableEntryByAvailability:
    def test_availability_private(self):
        assert _is_unavailable_entry({"id": "x", "title": "T", "availability": "private"}) is True

    def test_availability_deleted(self):
        assert _is_unavailable_entry({"id": "x", "title": "T", "availability": "deleted"}) is True

    def test_availability_public_kept(self):
        assert _is_unavailable_entry({"id": "x", "title": "T", "availability": "public"}) is False

    def test_availability_subscriber_only_kept(self):
        # Members-only content may be downloadable with cookies, so don't skip it.
        assert _is_unavailable_entry({"id": "x", "title": "T", "availability": "subscriber_only"}) is False


class TestUnavailableEntryEdgeCases:
    def test_missing_title_and_availability(self):
        assert _is_unavailable_entry({"id": "x"}) is False

    def test_none_title(self):
        assert _is_unavailable_entry({"id": "x", "title": None}) is False
