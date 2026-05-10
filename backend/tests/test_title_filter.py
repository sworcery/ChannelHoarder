import re

import pytest
from pydantic import ValidationError

from app.schemas import ChannelCreate, ChannelUpdate


# --- Schema validation tests ---

class TestChannelCreateTitleFilter:
    def test_no_filter(self):
        ch = ChannelCreate(url="https://youtube.com/@test")
        assert ch.title_filter is None
        assert ch.title_filter_is_regex is False

    def test_keyword_filter(self):
        ch = ChannelCreate(url="https://youtube.com/@test", title_filter="Rust, Minecraft")
        assert ch.title_filter == "Rust, Minecraft"
        assert ch.title_filter_is_regex is False

    def test_valid_regex(self):
        ch = ChannelCreate(
            url="https://youtube.com/@test",
            title_filter="rust|minecraft",
            title_filter_is_regex=True,
        )
        assert ch.title_filter == "rust|minecraft"
        assert ch.title_filter_is_regex is True

    def test_invalid_regex_rejected(self):
        with pytest.raises(ValidationError, match="Invalid regex pattern"):
            ChannelCreate(
                url="https://youtube.com/@test",
                title_filter="[unclosed",
                title_filter_is_regex=True,
            )

    def test_invalid_regex_ok_when_keyword_mode(self):
        ch = ChannelCreate(
            url="https://youtube.com/@test",
            title_filter="[unclosed",
            title_filter_is_regex=False,
        )
        assert ch.title_filter == "[unclosed"

    def test_empty_filter_with_regex_mode(self):
        ch = ChannelCreate(
            url="https://youtube.com/@test",
            title_filter=None,
            title_filter_is_regex=True,
        )
        assert ch.title_filter is None


class TestChannelUpdateTitleFilter:
    def test_valid_regex_update(self):
        update = ChannelUpdate(
            title_filter="^(rust|ark)",
            title_filter_is_regex=True,
        )
        assert update.title_filter == "^(rust|ark)"

    def test_invalid_regex_update_rejected(self):
        with pytest.raises(ValidationError, match="Invalid regex pattern"):
            ChannelUpdate(
                title_filter="(unclosed",
                title_filter_is_regex=True,
            )

    def test_clear_filter(self):
        update = ChannelUpdate(title_filter=None)
        data = update.model_dump(exclude_unset=True)
        assert data["title_filter"] is None


# --- Filtering logic tests (unit-level, no DB) ---

def keyword_matches(title_filter: str, title: str) -> bool:
    keywords = [k.strip() for k in title_filter.split(",") if k.strip()]
    title_lower = title.lower()
    return any(kw.lower() in title_lower for kw in keywords)


def regex_matches(pattern: str, title: str) -> bool:
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
        return bool(compiled.search(title))
    except re.error:
        return False


class TestKeywordMatching:
    def test_single_keyword_match(self):
        assert keyword_matches("Rust", "Playing Rust with friends") is True

    def test_single_keyword_no_match(self):
        assert keyword_matches("Rust", "Playing Minecraft today") is False

    def test_multiple_keywords_first_matches(self):
        assert keyword_matches("Rust, Minecraft", "Rust Gameplay #5") is True

    def test_multiple_keywords_second_matches(self):
        assert keyword_matches("Rust, Minecraft", "New Minecraft Update") is True

    def test_multiple_keywords_none_match(self):
        assert keyword_matches("Rust, Minecraft", "Fortnite Season 12") is False

    def test_case_insensitive(self):
        assert keyword_matches("rust", "RUST IS AMAZING") is True

    def test_substring_match(self):
        assert keyword_matches("craft", "Minecraft Update") is True

    def test_whitespace_handling(self):
        assert keyword_matches("  Rust  ,  Minecraft  ", "Rust stream") is True

    def test_empty_keywords_after_split(self):
        assert keyword_matches(",,,,", "Any Title") is False

    def test_comma_in_title(self):
        assert keyword_matches("hello", "hello, world") is True


class TestRegexMatching:
    def test_simple_alternation(self):
        assert regex_matches("rust|minecraft", "Playing Rust") is True

    def test_alternation_second_branch(self):
        assert regex_matches("rust|minecraft", "Minecraft Update") is True

    def test_no_match(self):
        assert regex_matches("rust|minecraft", "Fortnite stream") is False

    def test_case_insensitive(self):
        assert regex_matches("rust", "RUST SERVER WIPE") is True

    def test_negative_lookahead(self):
        assert regex_matches(r"rust(?!\s*bucket)", "Rust Gameplay") is True
        assert regex_matches(r"rust(?!\s*bucket)", "Rust Bucket Show") is False

    def test_anchored_pattern(self):
        assert regex_matches("^\\[LIVE\\]", "[LIVE] Rust Stream") is True
        assert regex_matches("^\\[LIVE\\]", "Rust [LIVE] Stream") is False

    def test_invalid_regex_fails_closed(self):
        assert regex_matches("[unclosed", "Any Title") is False

    def test_word_boundary(self):
        assert regex_matches(r"\brust\b", "Rust Gameplay") is True
        assert regex_matches(r"\brust\b", "Rusty Adventures") is False

    def test_empty_pattern_matches_everything(self):
        assert regex_matches("", "Any Title") is True

    def test_dot_star(self):
        assert regex_matches("rust.*raid", "Rust Base Raiding Tips") is True
