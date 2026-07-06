from app.routers.channels import _is_likely_short
from app.services.channel_service import SHORTS_MAX_DURATION


class _Vid:
    def __init__(self, duration=None, title=None, file_size=None):
        self.duration = duration
        self.title = title
        self.file_size = file_size


class TestIsLikelyShort:
    """Shorts detection is keyed off a fixed <=60s cutoff and reliable signals,
    NOT the min_video_duration download filter (which caused mass deletion)."""

    def test_cutoff_is_60(self):
        assert SHORTS_MAX_DURATION == 60

    def test_short_by_duration(self):
        assert _is_likely_short(_Vid(duration=45), SHORTS_MAX_DURATION) is True

    def test_at_cutoff_is_short(self):
        assert _is_likely_short(_Vid(duration=60), SHORTS_MAX_DURATION) is True

    def test_regular_length_not_short(self):
        # A 3-minute video is not a short, regardless of a large min-duration filter.
        assert _is_likely_short(_Vid(duration=180), SHORTS_MAX_DURATION) is False

    def test_short_by_hashtag_title(self):
        assert _is_likely_short(_Vid(duration=300, title="Cool clip #shorts"), SHORTS_MAX_DURATION) is True

    def test_small_file_long_duration_not_short(self):
        # The removed dead block used to (never) trigger here; a small long video is not a short.
        assert _is_likely_short(_Vid(duration=600, file_size=5 * 1024 * 1024), SHORTS_MAX_DURATION) is False

    def test_unknown_duration_no_signals_not_short(self):
        assert _is_likely_short(_Vid(duration=None, title="Regular video"), SHORTS_MAX_DURATION) is False
