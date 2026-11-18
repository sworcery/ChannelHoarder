import pathlib
import re

import pytest

from app.utils.error_codes import ERROR_CATALOG, SETTINGS_TABS, ErrorCode

_INDEX = pathlib.Path(__file__).resolve().parents[2] / "frontend/src/components/settings/searchIndex.ts"


def _frontend_tabs() -> set[str]:
    """The tab names Settings shows, read from the frontend's TAB_LABELS."""
    src = _INDEX.read_text(encoding="utf-8")
    block = re.search(r"TAB_LABELS[^=]*=\s*\{(.*?)\}", src, re.S).group(1)
    return set(re.findall(r':\s*"([^"]+)"', block))


REAL_TABS = _frontend_tabs()


def _texts(info):
    return {
        "summary": info.summary,
        "explanation": info.explanation,
        "suggested_fix": info.suggested_fix,
        "label": info.label,
        "tip": info.tip,
        "failed_tip": info.failed_text,
    }


def test_tab_list_matches_settings_page():
    assert set(SETTINGS_TABS) == REAL_TABS


def test_every_code_has_a_catalogue_entry_with_words():
    for code in ErrorCode:
        info = ERROR_CATALOG[code]
        assert info.label and info.tip, code
        # Shown as words: neither the label nor the tip is the code itself
        assert code.value not in info.label
        assert "_" not in info.label


@pytest.mark.parametrize("code", list(ErrorCode))
def test_no_text_names_a_settings_tab_that_does_not_exist(code):
    for field, text in _texts(ERROR_CATALOG[code]).items():
        for match in re.finditer(r"Settings\s*>\s*([\w-]+(?: [A-Z][\w-]*)*)", text):
            assert match.group(1) in REAL_TABS, f"{code} {field}: {match.group(0)!r}"


@pytest.mark.parametrize("code", list(ErrorCode))
def test_youtube_only_in_text_for_youtube_only_codes(code):
    info = ERROR_CATALOG[code]
    if info.youtube_only:
        return
    for field, text in _texts(info).items():
        # A YouTube-specific sentence may start "On YouTube, ..." after the {site} part
        text = re.sub(r"On YouTube,[^.]*\.?", "", text)
        assert "youtube" not in text.lower(), f"{code} {field} names YouTube"


def test_tips_start_with_the_site_placeholder_when_they_use_it():
    # A mixed-platform group fills {site} with "The site", so it must open the sentence
    for info in ERROR_CATALOG.values():
        if "{site}" in info.tip:
            assert info.tip.startswith("{site}"), info.code
            assert info.tip.count("{site}") == 1


def test_tips_that_name_a_site_use_the_platform_placeholder():
    # A tip for a code that applies to every platform names the site through {site}
    site_tips = [i for i in ERROR_CATALOG.values() if "{site}" in i.tip]
    assert site_tips
    for info in site_tips:
        assert not info.youtube_only


def test_upgrade_advice_is_the_container_image():
    for info in ERROR_CATALOG.values():
        for text in _texts(info).values():
            assert "update yt-dlp" not in text.lower()
            assert "yt-dlp > update" not in text.lower()


async def test_retry_all_failed_retryable_only_skips_unfixable_causes(tmp_path):
    from datetime import date

    from sqlalchemy import select

    from app.models import Video
    from app.routers import downloads
    from tests.test_enqueue import _maker

    maker, (vid, ch_id) = await _maker(tmp_path)
    async with maker() as db:
        first = await db.get(Video, vid)
        first.status, first.error_code = "failed", "NETWORK_ERROR"
        db.add(Video(channel_id=ch_id, video_id="v2", title="Gone", status="failed",
                     error_code="VIDEO_PRIVATE", season=1, episode=2,
                     upload_date=date(2025, 1, 2)))
        await db.commit()

    async with maker() as db:
        out = await downloads.retry_all_failed(retryable_only=True, db=db)
    assert out["count"] == 1

    async with maker() as db:
        rows = (await db.execute(select(Video.video_id, Video.status))).all()
    assert dict(rows) == {"v1": "queued", "v2": "failed"}


async def test_retry_all_failed_by_error_code_includes_causes_needing_a_change(tmp_path):
    from datetime import date

    from sqlalchemy import select

    from app.models import Video
    from app.routers import downloads
    from tests.test_enqueue import _maker

    maker, (vid, ch_id) = await _maker(tmp_path)
    async with maker() as db:
        first = await db.get(Video, vid)
        first.status, first.error_code = "failed", "AUTH_EXPIRED"
        db.add(Video(channel_id=ch_id, video_id="v2", title="Net", status="failed",
                     error_code="NETWORK_ERROR", season=1, episode=2,
                     upload_date=date(2025, 1, 2)))
        await db.commit()

    async with maker() as db:
        out = await downloads.retry_all_failed(db=db, error_code="AUTH_EXPIRED")
    assert out["count"] == 1

    async with maker() as db:
        rows = (await db.execute(select(Video.video_id, Video.status))).all()
    assert dict(rows) == {"v1": "queued", "v2": "failed"}


async def test_held_channels_lists_only_channels_with_waiting_rows(tmp_path):
    from app.models import DownloadQueue, Video
    from app.routers import downloads
    from app.services import channel_activity
    from tests.test_enqueue import _maker

    maker, (vid, ch_id) = await _maker(tmp_path)
    channel_activity.moving_channels.add(ch_id)
    try:
        async with maker() as db:
            assert await downloads.get_held_channels(db=db) == []  # nothing waiting yet
            db.add(DownloadQueue(video_id=vid))
            await db.commit()
        async with maker() as db:
            held = await downloads.get_held_channels(db=db)
    finally:
        channel_activity.moving_channels.discard(ch_id)
    assert [h["id"] for h in held] == [ch_id]
    assert "moved" in held[0]["reason"]


@pytest.mark.parametrize("code", list(ErrorCode))
def test_failed_tab_tip_never_promises_an_automatic_retry(code):
    # A video reaches Failed only after its automatic retries are used up
    info = ERROR_CATALOG[code]
    for text in (info.failed_text.lower(), info.failed_fix_text.lower()):
        for phrase in ("on their own", "on its own", "retry after", "retries after", "tries again",
                       "tries a few more", "retried automatically", "automatically", "retry these"):
            assert phrase not in text, f"{code}: {phrase!r}"


def test_failed_tips_name_the_button_as_it_reads():
    for info in ERROR_CATALOG.values():
        assert "Retry these" not in info.failed_text and "Retry these" not in info.failed_fix_text


def test_failed_tip_keeps_the_site_placeholder_first():
    for info in ERROR_CATALOG.values():
        if "{site}" in info.failed_text:
            assert info.failed_text.startswith("{site}"), info.code


def test_permanent_causes_are_not_retryable():
    for info in ERROR_CATALOG.values():
        if info.permanent:
            assert not info.retryable, info.code


async def test_failed_counts_cover_every_page_and_fold_no_code_into_unknown(tmp_path):
    from datetime import date

    from app.models import Video
    from app.routers import downloads
    from tests.test_enqueue import _maker

    maker, (vid, ch_id) = await _maker(tmp_path)
    async with maker() as db:
        first = await db.get(Video, vid)
        first.status, first.error_code = "failed", None
        db.add(Video(channel_id=ch_id, video_id="v2", title="B", status="failed",
                     error_code="UNKNOWN", season=1, episode=2, upload_date=date(2025, 1, 2)))
        db.add(Video(channel_id=ch_id, video_id="v3", title="C", status="failed",
                     error_code="NETWORK_ERROR", season=1, episode=3, upload_date=date(2025, 1, 3)))
        await db.commit()
    async with maker() as db:
        assert await downloads.get_failed_counts(db=db) == {"UNKNOWN": 2, "NETWORK_ERROR": 1}


def test_retry_rule_follows_the_strategy():
    assert not ERROR_CATALOG[ErrorCode.VIDEO_PRIVATE].retryable
    assert not ERROR_CATALOG[ErrorCode.GEO_BLOCKED].retryable
    assert ERROR_CATALOG[ErrorCode.NETWORK_ERROR].retryable
    assert ERROR_CATALOG[ErrorCode.FORMAT_UNAVAILABLE].retryable
