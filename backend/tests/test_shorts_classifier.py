from types import SimpleNamespace

import pytest

from app.routers.channels import _is_likely_short
from app.services.channel_service import is_short


@pytest.mark.parametrize("duration,expected", [(59, True), (60, True), (61, False)])
def test_youtube_duration_boundary(duration, expected):
    assert is_short("youtube", None, "A video", duration) is expected


def test_hashtag_whole_word():
    assert is_short("youtube", None, "Cool #short", 300)
    assert is_short("youtube", None, "Cool #Shorts here", 300)
    assert not is_short("youtube", None, "Cool #shortfilm", 300)


def test_shorts_url():
    assert is_short("youtube", "https://www.youtube.com/shorts/abc", "x", 300)


def test_rumble_short_clip_is_not_a_short():
    assert not is_short("rumble", None, "Clip", 30)
    assert not is_short("rumble", None, "Clip #shorts", 30)


def test_shorts_tab_beats_duration():
    assert is_short("youtube", None, "Long one", 150, True)
    assert not is_short("youtube", None, "Long one", 150, False)


@pytest.mark.parametrize("platform,title,duration,expected", [
    ("youtube", "t", 59, True), ("youtube", "t", 61, False),
    ("youtube", "#shortfilm", 500, False), ("youtube", "#shorts", 500, True),
    ("rumble", "t", 30, False), ("vimeo", "#shorts", 20, False),
])
def test_detect_clean_flags_only_what_the_rule_says(platform, title, duration, expected):
    video = SimpleNamespace(source_url=None, title=title, duration=duration)
    assert _is_likely_short(video, platform) is expected
