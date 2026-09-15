import asyncio
import os
import tempfile
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import Channel, Video
from app.routers.channels import repair_dates_confirm, repair_dates_preview
from app.schemas import RepairDateChange, RepairDatesConfirm
from app.services.channel_service import ChannelService, _scanning_channels
from app.services.naming_service import build_output_path
from app.services.ytdlp_service import YtdlpService


class _FakeYtApi:
    """get_video_dates returns production format: {video_id: "YYYYMMDD" | None}."""

    def __init__(self, dates=None):
        self.dates = dates or {}
        self.calls = []

    async def get_video_dates(self, video_ids):
        self.calls.append(list(video_ids))
        return {vid: self.dates.get(vid) for vid in video_ids}


def _make_ytdlp_fetcher(calls, responses, errors=None):
    errors = errors or {}

    def _fetch(vid_id, platform="youtube"):
        calls.append(vid_id)
        if vid_id in responses:
            return responses[vid_id], None
        return None, errors.get(vid_id, "Sign in to confirm you're not a bot")

    return _fetch


def _channel_kwargs(**overrides):
    kwargs = {
        "channel_id": "UC_test", "channel_name": "Test Channel",
        "channel_url": "https://www.youtube.com/@test", "platform": "youtube",
        "auto_download": False, "health_status": "healthy",
    }
    kwargs.update(overrides)
    return kwargs


def _video(channel_id, video_id, *, upload_date, discovered_at, season, episode,
           title=None, status="completed", file_path=None, is_short=False, is_livestream=False):
    return Video(
        video_id=video_id, channel_id=channel_id, title=title or f"Video {video_id}",
        upload_date=upload_date, discovered_at=discovered_at, season=season,
        episode=episode, status=status, file_path=file_path,
        is_short=is_short, is_livestream=is_livestream,
    )


def _wire_no_network(service, rss=None):
    """Default a service to a network-free resolver: empty RSS, no API key."""
    service.yt_api = None
    service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: dict(rss or {})


async def _make_db():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return engine, maker


async def _run(fn):
    engine, maker = await _make_db()
    try:
        async with maker() as db:
            return await fn(db)
    finally:
        await engine.dispose()


def _run_sync(fn):
    return asyncio.run(_run(fn))


def _make_file(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("x")


class TestResolverListsOnlyDiffering:
    def test_only_changed_suspects_are_listed(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v1 = _video(channel.id, "v1", upload_date=today,
                         discovered_at=datetime(today.year, today.month, today.day, 10, 0),
                         season=today.year, episode=1)
            v2 = _video(channel.id, "v2", upload_date=today,
                         discovered_at=datetime(today.year, today.month, today.day, 11, 0),
                         season=today.year, episode=2)
            v3 = _video(channel.id, "v3", upload_date=date(2020, 1, 1),
                         discovered_at=datetime(today.year, today.month, today.day, 12, 0),
                         season=2020, episode=1)
            db.add_all([v1, v2, v3])
            await db.commit()

            calls = []
            service = ChannelService(db)
            _wire_no_network(service)
            service.ytdlp.get_video_info_or_error = _make_ytdlp_fetcher(calls, {
                "v1": {"upload_date": "20190314"},
                "v2": {"upload_date": today.strftime("%Y%m%d")},
            })
            return await service.resolve_fallback_dates(channel), calls

        (changes, checked, stopped_early, capped), calls = _run_sync(scenario)
        assert calls == ["v1", "v2"]
        assert checked == 2
        assert stopped_early is False
        assert capped is False
        assert [c["video_id"] for c in changes] == [1]
        assert changes[0]["new_date"] == "2019-03-14"
        assert changes[0]["source_id"] == "v1"


class TestBreakerBehavior:
    def test_success_resets_streak_and_permanent_failure_not_counted(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            videos = []
            for i in range(1, 7):
                vid = f"v{i}"
                videos.append(_video(
                    channel.id, vid, upload_date=today,
                    discovered_at=datetime(today.year, today.month, today.day, i, 0),
                    season=today.year, episode=i,
                ))
            db.add_all(videos)
            await db.commit()

            calls = []
            errors = {
                "v1": "Sign in to confirm you're not a bot",
                "v3": "Private video",
                "v4": "Sign in to confirm you're not a bot",
                "v5": "Sign in to confirm you're not a bot",
                "v6": "Sign in to confirm you're not a bot",
            }
            responses = {"v2": {"upload_date": "20180101"}}
            service = ChannelService(db)
            _wire_no_network(service)
            service.ytdlp.get_video_info_or_error = _make_ytdlp_fetcher(calls, responses, errors)
            return await service.resolve_fallback_dates(channel), calls

        (changes, checked, stopped_early, capped), calls = _run_sync(scenario)
        assert calls == ["v1", "v2", "v3", "v4", "v5", "v6"]
        assert stopped_early is True
        assert capped is False
        assert checked == 5
        assert [c["video_id"] for c in changes] == [2]


class TestSourceOrder:
    def test_rss_covered_suspects_are_never_fetched(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v_rss = _video(channel.id, "v_rss", upload_date=today,
                            discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                            season=today.year, episode=1)
            v_ytdlp = _video(channel.id, "v_ytdlp", upload_date=today,
                              discovered_at=datetime(today.year, today.month, today.day, 9, 0),
                              season=today.year, episode=2)
            db.add_all([v_rss, v_ytdlp])
            await db.commit()

            calls = []
            service = ChannelService(db)
            service.yt_api = None
            service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {"v_rss": "20180101"}
            service.ytdlp.get_video_info_or_error = _make_ytdlp_fetcher(
                calls, {"v_ytdlp": {"upload_date": "20170101"}}
            )
            return await service.resolve_fallback_dates(channel), calls

        (changes, checked, stopped_early, capped), calls = _run_sync(scenario)
        assert calls == ["v_ytdlp"]
        assert checked == 2
        assert {c["video_id"]: c["new_date"] for c in changes} == {1: "2018-01-01", 2: "2017-01-01"}

    def test_api_falsy_value_falls_through_to_ytdlp(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v_dated = _video(channel.id, "v_dated", upload_date=today,
                              discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                              season=today.year, episode=1)
            v_undated = _video(channel.id, "v_undated", upload_date=today,
                                discovered_at=datetime(today.year, today.month, today.day, 9, 0),
                                season=today.year, episode=2)
            db.add_all([v_dated, v_undated])
            await db.commit()

            calls = []
            service = ChannelService(db)
            service.ytdlp.get_rss_upload_dates = lambda cid, platform, is_playlist: {}
            service.yt_api = _FakeYtApi({"v_dated": "20160101", "v_undated": None})
            service.ytdlp.get_video_info_or_error = _make_ytdlp_fetcher(
                calls, {"v_undated": {"upload_date": "20150101"}}
            )
            return await service.resolve_fallback_dates(channel), calls, service.yt_api.calls

        (changes, checked, stopped_early, capped), calls, api_calls = _run_sync(scenario)
        assert calls == ["v_undated"]
        assert api_calls == [["v_dated", "v_undated"]]
        assert {c["video_id"]: c["new_date"] for c in changes} == {1: "2016-01-01", 2: "2015-01-01"}


class TestCap:
    def test_more_than_250_suspects_are_capped(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            videos = [
                _video(channel.id, f"v{i}", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, 0, 0) + timedelta(seconds=i),
                       season=today.year, episode=1)
                for i in range(251)
            ]
            db.add_all(videos)
            await db.commit()

            calls = []
            service = ChannelService(db)
            _wire_no_network(service)
            # Cheap stub: every fetch succeeds with the same (unchanged) date so the
            # breaker never trips and only the cap determines stopped_early.
            service.ytdlp.get_video_info_or_error = _make_ytdlp_fetcher(
                calls, {f"v{i}": {"upload_date": today.strftime("%Y%m%d")} for i in range(251)}
            )
            return await service.resolve_fallback_dates(channel), calls

        (changes, checked, stopped_early, capped), calls = _run_sync(scenario)
        assert len(calls) == 250
        assert checked == 250
        # The breaker never tripped - only the cap stopped iteration early. The
        # endpoint folds capped into stopped_early for the API response; at the
        # resolver level the two are reported separately.
        assert stopped_early is False
        assert capped is True
        assert changes == []


class TestConfirmValidation:
    def test_invalid_items_are_skipped_and_do_not_change_anything(self):
        today = date.today()

        async def scenario(db):
            channel_a = Channel(**_channel_kwargs())
            channel_b = Channel(**_channel_kwargs(channel_id="UC_other", channel_name="Other Channel"))
            db.add_all([channel_a, channel_b])
            await db.commit()
            await db.refresh(channel_a)
            await db.refresh(channel_b)

            other_channel_video = _video(channel_b.id, "vb", upload_date=today,
                                          discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                                          season=today.year, episode=1)
            non_suspect = _video(channel_a.id, "vn", upload_date=date(2019, 1, 1),
                                  discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                                  season=2019, episode=1)
            unchanged = _video(channel_a.id, "vu", upload_date=today,
                                discovered_at=datetime(today.year, today.month, today.day, 9, 0),
                                season=today.year, episode=2)
            valid = _video(channel_a.id, "vv", upload_date=today,
                            discovered_at=datetime(today.year, today.month, today.day, 10, 0),
                            season=today.year, episode=3)
            db.add_all([other_channel_video, non_suspect, unchanged, valid])
            await db.commit()
            await db.refresh(other_channel_video)
            await db.refresh(non_suspect)
            await db.refresh(unchanged)
            await db.refresh(valid)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=other_channel_video.id, source_id="vb", new_date=date(2017, 1, 1)),
                RepairDateChange(video_id=non_suspect.id, source_id="vn", new_date=date(2016, 1, 1)),
                RepairDateChange(video_id=unchanged.id, source_id="vu", new_date=today),
                RepairDateChange(video_id=valid.id, source_id="vv", new_date=date(2015, 6, 1)),
            ])
            result = await repair_dates_confirm(channel_a.id, body, db=db)

            await db.refresh(other_channel_video)
            await db.refresh(non_suspect)
            await db.refresh(unchanged)
            await db.refresh(valid)
            return result, other_channel_video, non_suspect, unchanged, valid

        result, other_channel_video, non_suspect, unchanged, valid = _run_sync(scenario)
        assert result["skipped"] == 3
        assert result["dates_corrected"] == 1
        assert other_channel_video.upload_date == today
        assert non_suspect.upload_date == date(2019, 1, 1)
        assert unchanged.upload_date == today
        assert valid.upload_date == date(2015, 6, 1)
        assert valid.season == 2015

    def test_source_id_mismatch_is_skipped(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v = _video(channel.id, "vreal", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                       season=today.year, episode=1)
            db.add(v)
            await db.commit()
            await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id="wrong_id", new_date=date(2018, 1, 1)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)
            await db.refresh(v)
            return result, v

        result, v = _run_sync(scenario)
        assert result["dates_corrected"] == 0
        assert result["skipped"] == 1
        assert v.upload_date == today

    def test_out_of_range_year_is_skipped(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v = _video(channel.id, "vold", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                       season=today.year, episode=1)
            db.add(v)
            await db.commit()
            await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id="vold", new_date=date(1800, 1, 1)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)
            await db.refresh(v)
            return result, v

        result, v = _run_sync(scenario)
        assert result["dates_corrected"] == 0
        assert result["skipped"] == 1
        assert v.upload_date == today

    def test_duplicate_video_id_applies_once(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v = _video(channel.id, "vdup", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                       season=today.year, episode=1)
            db.add(v)
            await db.commit()
            await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id="vdup", new_date=date(2018, 1, 1)),
                RepairDateChange(video_id=v.id, source_id="vdup", new_date=date(2018, 1, 1)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)
            await db.refresh(v)
            return result, v

        result, v = _run_sync(scenario)
        assert result["dates_corrected"] == 1
        assert result["skipped"] == 0
        assert v.upload_date == date(2018, 1, 1)


class TestConfirmLock:
    def test_locked_channel_returns_409_and_lock_is_unchanged(self):
        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            _scanning_channels.add(channel.id)
            try:
                with pytest.raises(HTTPException) as exc:
                    await repair_dates_confirm(channel.id, RepairDatesConfirm(changes=[]), db=db)
                status_code = exc.value.status_code
            finally:
                still_locked = channel.id in _scanning_channels
                _scanning_channels.discard(channel.id)
            return status_code, still_locked

        status_code, still_locked = _run_sync(scenario)
        assert status_code == 409
        assert still_locked is True


class TestConfirmEmptyChanges:
    def test_empty_changes_list_leaves_seasons_and_episodes_untouched(self):
        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            # Deliberately non-chronological so a no-op renumber would be obvious.
            va = _video(channel.id, "va", upload_date=date(2020, 6, 1),
                        discovered_at=datetime(2020, 6, 2), season=2020, episode=5)
            vb = _video(channel.id, "vb", upload_date=date(2019, 1, 1),
                        discovered_at=datetime(2019, 1, 2), season=2019, episode=9)
            db.add_all([va, vb])
            await db.commit()
            await db.refresh(va)
            await db.refresh(vb)

            result = await repair_dates_confirm(channel.id, RepairDatesConfirm(changes=[]), db=db)
            await db.refresh(va)
            await db.refresh(vb)
            return result, va, vb

        result, va, vb = _run_sync(scenario)
        assert result["dates_corrected"] == 0
        assert result["skipped"] == 0
        assert result["renamed"] == 0
        assert (va.season, va.episode) == (2020, 5)
        assert (vb.season, vb.episode) == (2019, 9)


class TestConfirmRenumbers:
    def test_corrected_dates_are_renumbered_chronologically(self):
        today = date.today()

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            va = _video(channel.id, "va", upload_date=today,
                        discovered_at=datetime(today.year, today.month, today.day, 8, 0),
                        season=today.year, episode=1)
            vb = _video(channel.id, "vb", upload_date=today,
                        discovered_at=datetime(today.year, today.month, today.day, 9, 0),
                        season=today.year, episode=2)
            db.add_all([va, vb])
            await db.commit()
            await db.refresh(va)
            await db.refresh(vb)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=va.id, source_id="va", new_date=date(2019, 3, 14)),
                RepairDateChange(video_id=vb.id, source_id="vb", new_date=date(2018, 1, 1)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)

            await db.refresh(va)
            await db.refresh(vb)
            return result, va, vb

        result, va, vb = _run_sync(scenario)
        assert result["dates_corrected"] == 2
        assert result["renamed"] == 0
        assert (vb.upload_date, vb.season, vb.episode) == (date(2018, 1, 1), 2018, 1)
        assert (va.upload_date, va.season, va.episode) == (date(2019, 3, 14), 2019, 1)


class TestConfirmWithRealFiles:
    def test_realistic_nine_video_corruption_is_fully_repaired(self):
        today = date.today()

        async def scenario(db, tmpdir):
            channel = Channel(**_channel_kwargs(download_dir=tmpdir))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            new_dates = [
                date(2019, 5, 1), date(2019, 6, 1), date(2019, 7, 1),
                date(2020, 1, 1), date(2020, 2, 1), date(2020, 3, 1),
                date(2021, 1, 1), date(2021, 2, 1), date(2021, 3, 1),
            ]
            videos = []
            for i in range(1, 10):
                video_id = f"v{i}"
                title = f"Video {i}"
                old_path = build_output_path(
                    channel_name=channel.channel_name, video_title=title, video_id=video_id,
                    upload_date=today, season=today.year, episode=i,
                    naming_template=None, base_dir=tmpdir,
                ) + ".mp4"
                _make_file(old_path)
                v = _video(channel.id, video_id, upload_date=today,
                           discovered_at=datetime(today.year, today.month, today.day, i, 0),
                           season=today.year, episode=i, title=title, file_path=old_path)
                db.add(v)
                videos.append(v)
            await db.commit()
            for v in videos:
                await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id=v.video_id, new_date=nd)
                for v, nd in zip(videos, new_dates)
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)

            for v in videos:
                await db.refresh(v)
            return result, videos

        with tempfile.TemporaryDirectory() as tmpdir:
            result, videos = _run_sync(lambda db: scenario(db, tmpdir))

            assert result["dates_corrected"] == 9
            assert result["renamed"] == 9

            for v in videos:
                expected_base = build_output_path(
                    channel_name="Test Channel", video_title=v.title, video_id=v.video_id,
                    upload_date=v.upload_date, season=v.season, episode=v.episode,
                    naming_template=None, base_dir=tmpdir,
                )
                expected_path = expected_base + ".mp4"
                assert v.file_path == expected_path
                assert os.path.exists(expected_path)

                nfo_path = expected_base + ".nfo"
                assert os.path.exists(nfo_path)
                with open(nfo_path) as f:
                    nfo_content = f.read()
                assert f"<aired>{v.upload_date.isoformat()}</aired>" in nfo_content

    def test_same_year_date_only_correction_renames_and_rewrites_nfo(self):
        async def scenario(db, tmpdir):
            channel = Channel(**_channel_kwargs(download_dir=tmpdir))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            old_path = build_output_path(
                channel_name=channel.channel_name, video_title="Only Video", video_id="only1",
                upload_date=date(2026, 9, 14), season=2026, episode=1,
                naming_template=None, base_dir=tmpdir,
            ) + ".mp4"
            _make_file(old_path)
            v = _video(channel.id, "only1", upload_date=date(2026, 9, 14),
                       discovered_at=datetime(2026, 9, 14, 8, 0),
                       season=2026, episode=1, title="Only Video", file_path=old_path)
            db.add(v)
            await db.commit()
            await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id="only1", new_date=date(2026, 1, 5)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)
            await db.refresh(v)
            return result, v, old_path

        with tempfile.TemporaryDirectory() as tmpdir:
            result, v, old_path = _run_sync(lambda db: scenario(db, tmpdir))

            assert result["dates_corrected"] == 1
            assert result["renamed"] == 1
            assert v.season == 2026
            assert v.episode == 1
            assert v.upload_date == date(2026, 1, 5)
            assert not os.path.exists(old_path)

            expected_base = build_output_path(
                channel_name="Test Channel", video_title="Only Video", video_id="only1",
                upload_date=date(2026, 1, 5), season=2026, episode=1,
                naming_template=None, base_dir=tmpdir,
            )
            assert v.file_path == expected_base + ".mp4"
            assert os.path.exists(v.file_path)
            with open(expected_base + ".nfo") as f:
                assert "<aired>2026-01-05</aired>" in f.read()

    def test_short_with_file_moves_to_correct_season_and_regenerates_nfo(self):
        async def scenario(db, tmpdir):
            channel = Channel(**_channel_kwargs(download_dir=tmpdir))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            old_path = build_output_path(
                channel_name=channel.channel_name, video_title="A Short", video_id="short1",
                upload_date=date(2026, 9, 14), season=2026, episode=0,
                naming_template=None, base_dir=tmpdir,
            ) + ".mp4"
            _make_file(old_path)
            v = _video(channel.id, "short1", upload_date=date(2026, 9, 14),
                       discovered_at=datetime(2026, 9, 14, 8, 0),
                       season=2026, episode=0, title="A Short", file_path=old_path, is_short=True)
            db.add(v)
            await db.commit()
            await db.refresh(v)

            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v.id, source_id="short1", new_date=date(2019, 3, 1)),
            ])
            result = await repair_dates_confirm(channel.id, body, db=db)
            await db.refresh(v)
            return result, v

        with tempfile.TemporaryDirectory() as tmpdir:
            result, v = _run_sync(lambda db: scenario(db, tmpdir))

            assert result["dates_corrected"] == 1
            assert v.episode == 0
            assert v.season == 2019
            assert "Season 2019" in v.file_path
            assert os.path.exists(v.file_path)

            nfo_path = os.path.splitext(v.file_path)[0] + ".nfo"
            assert os.path.exists(nfo_path)
            with open(nfo_path) as f:
                assert "<aired>2019-03-01</aired>" in f.read()

    def test_partial_failure_commits_dates_and_prior_moves_then_raises_500(self, monkeypatch):
        today = date.today()

        async def scenario(db, tmpdir):
            channel = Channel(**_channel_kwargs(download_dir=tmpdir))
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            v1_path = build_output_path(
                channel_name=channel.channel_name, video_title="Video 1", video_id="v1",
                upload_date=today, season=today.year, episode=1,
                naming_template=None, base_dir=tmpdir,
            ) + ".mp4"
            v2_path = build_output_path(
                channel_name=channel.channel_name, video_title="Video 2", video_id="v2",
                upload_date=today, season=today.year, episode=2,
                naming_template=None, base_dir=tmpdir,
            ) + ".mp4"
            _make_file(v1_path)
            _make_file(v2_path)
            v1 = _video(channel.id, "v1", upload_date=today,
                        discovered_at=datetime(today.year, today.month, today.day, 1, 0),
                        season=today.year, episode=1, title="Video 1", file_path=v1_path)
            v2 = _video(channel.id, "v2", upload_date=today,
                        discovered_at=datetime(today.year, today.month, today.day, 2, 0),
                        season=today.year, episode=2, title="Video 2", file_path=v2_path)
            db.add_all([v1, v2])
            await db.commit()
            await db.refresh(v1)
            await db.refresh(v2)

            move_calls = []

            def _fake_move(old_path, new_path, overwrite=False):
                move_calls.append((old_path, new_path))
                if len(move_calls) == 2:
                    raise PermissionError("simulated failure")
                return 1

            monkeypatch.setattr("app.utils.renumber.move_video_files", _fake_move)

            # v1 (corrected to 2019) sorts before v2 (corrected to 2020), so v1's
            # move succeeds before v2's raises.
            body = RepairDatesConfirm(changes=[
                RepairDateChange(video_id=v1.id, source_id="v1", new_date=date(2019, 1, 1)),
                RepairDateChange(video_id=v2.id, source_id="v2", new_date=date(2020, 1, 1)),
            ])

            status_code = None
            try:
                await repair_dates_confirm(channel.id, body, db=db)
            except HTTPException as exc:
                status_code = exc.status_code

            await db.refresh(v1)
            await db.refresh(v2)
            still_locked = channel.id in _scanning_channels
            return status_code, v1, v2, v1_path, v2_path, still_locked

        with tempfile.TemporaryDirectory() as tmpdir:
            status_code, v1, v2, v1_path, v2_path, still_locked = _run_sync(lambda db: scenario(db, tmpdir))

            assert status_code == 500
            assert still_locked is False
            # Both dates were corrected regardless of the renumber failure.
            assert v1.upload_date == date(2019, 1, 1)
            assert v2.upload_date == date(2020, 1, 1)

            # v1's move succeeded before v2's raised - that file_path is committed.
            expected_v1_path = build_output_path(
                channel_name="Test Channel", video_title="Video 1", video_id="v1",
                upload_date=date(2019, 1, 1), season=2019, episode=1,
                naming_template=None, base_dir=tmpdir,
            ) + ".mp4"
            assert v1.file_path == expected_v1_path

            # v2's move never completed, so its DB file_path is unchanged.
            assert v2.file_path == v2_path


class TestPreviewEndpoint:
    def test_404_for_missing_channel(self):
        async def scenario(db):
            with pytest.raises(HTTPException) as exc:
                await repair_dates_preview(999, db=db)
            return exc.value.status_code

        assert _run_sync(scenario) == 404

    def test_response_shape_with_no_suspects(self):
        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)
            return await repair_dates_preview(channel.id, db=db)

        result = _run_sync(scenario)
        assert set(result.keys()) == {
            "channel_name", "checked", "changes", "total_changes", "stopped_early", "message",
        }
        assert result["checked"] == 0
        assert result["changes"] == []
        assert result["total_changes"] == 0
        assert result["stopped_early"] is False
        assert result["message"] is None

    def test_stopped_early_failure_message(self, monkeypatch):
        today = date.today()
        monkeypatch.setattr(settings, "YOUTUBE_API_KEY", None)
        monkeypatch.setattr(YtdlpService, "get_rss_upload_dates", staticmethod(lambda cid, platform, is_playlist: {}))
        monkeypatch.setattr(
            YtdlpService, "get_video_info_or_error",
            lambda self, vid_id, platform="youtube": (None, "Sign in to confirm you're not a bot"),
        )

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            videos = [
                _video(channel.id, f"v{i}", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, i, 0),
                       season=today.year, episode=i)
                for i in range(1, 5)
            ]
            db.add_all(videos)
            await db.commit()

            return await repair_dates_preview(channel.id, db=db)

        result = _run_sync(scenario)
        assert result["stopped_early"] is True
        assert result["message"] == (
            "Stopped after repeated metadata fetch failures. Upload fresh cookies.txt "
            "and run Repair Dates again for the rest."
        )

    def test_cap_message_with_251_suspects(self, monkeypatch):
        today = date.today()
        monkeypatch.setattr(settings, "YOUTUBE_API_KEY", None)
        monkeypatch.setattr(YtdlpService, "get_rss_upload_dates", staticmethod(lambda cid, platform, is_playlist: {}))
        monkeypatch.setattr(
            YtdlpService, "get_video_info_or_error",
            lambda self, vid_id, platform="youtube": ({"upload_date": today.strftime("%Y%m%d")}, None),
        )

        async def scenario(db):
            channel = Channel(**_channel_kwargs())
            db.add(channel)
            await db.commit()
            await db.refresh(channel)

            videos = [
                _video(channel.id, f"v{i}", upload_date=today,
                       discovered_at=datetime(today.year, today.month, today.day, 0, 0),
                       season=today.year, episode=1)
                for i in range(251)
            ]
            db.add_all(videos)
            await db.commit()

            return await repair_dates_preview(channel.id, db=db)

        result = _run_sync(scenario)
        assert result["stopped_early"] is True
        assert result["message"] == (
            "Checked the first 250 videos with a suspect date. Run Repair Dates again for the rest."
        )
