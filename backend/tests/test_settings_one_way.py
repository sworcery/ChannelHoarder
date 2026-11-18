import io
import json
import logging

import pytest
from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.database as database
from app.database import Base
from app.models import AppSetting, Channel, Video
from app.routers import downloads
from app.routers import settings as settings_router
from app.services import settings_service
from app.services.settings_service import coerce, get_setting
from tests import test_delete_and_redownload as tdr
from tests import test_download_failures as tdf


def _put(db, key, value):
    db.add(AppSetting(key=key, value=json.dumps(value)))


async def _memory_maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _setting_count(maker):
    async with maker() as db:
        return await db.scalar(select(func.count(AppSetting.key)))


# --- Max Retries -----------------------------------------------------------

async def test_max_retries_from_db_is_honoured(monkeypatch, tmp_path):
    ytdlp = tdf._FakeYtdlp(info=None, lookup_error="Something odd happened")
    maker, service, ids = await tdf._setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    assert tdf.ds.settings.MAX_RETRIES == 3  # the env default would allow retries
    async with maker() as db:
        _put(db, "max_retries", 0)
        await db.commit()

    await service.download_video(*ids)
    video, queue = await tdf._state(maker, ids)
    assert video.status == "failed" and queue is None
    assert video.retry_count == 0


async def test_max_retries_from_db_can_be_raised(monkeypatch, tmp_path):
    ytdlp = tdf._FakeYtdlp(info=None, lookup_error="Something odd happened")
    maker, service, ids = await tdf._setup(monkeypatch, tmp_path, min_quality="1080p", ytdlp=ytdlp)
    async with maker() as db:
        _put(db, "max_retries", 5)
        video = await db.get(Video, ids[0])
        video.retry_count = 4  # past the env default of 3
        await db.commit()

    await service.download_video(*ids)
    video, queue = await tdf._state(maker, ids)
    assert video.status == "queued" and video.retry_count == 5


# --- retry_count reset -----------------------------------------------------

async def _failed_video(maker, video_id):
    async with maker() as db:
        video = await db.get(Video, video_id)
        video.status = "failed"
        video.retry_count = 7
        await db.delete(await db.get(tdf.DownloadQueue, 1))
        await db.commit()


async def test_retry_and_retry_all_reset_retry_count(monkeypatch, tmp_path):
    maker, _service, ids = await tdf._setup(monkeypatch, tmp_path)
    await _failed_video(maker, ids[0])
    async with maker() as db:
        await downloads.retry_download(ids[0], db)
    video, queue = await tdf._state(maker, ids)
    assert video.status == "queued" and video.retry_count == 0 and queue is not None

    await _failed_video(maker, ids[0])
    async with maker() as db:
        await downloads.retry_all_failed(db)
    video, queue = await tdf._state(maker, ids)
    assert video.status == "queued" and video.retry_count == 0 and queue is not None


async def test_download_anyway_resets_retry_count(monkeypatch, tmp_path):
    maker, _service, ids = await tdf._setup(monkeypatch, tmp_path)
    await _failed_video(maker, ids[0])
    async with maker() as db:
        video = await db.get(Video, ids[0])
        video.error_code = "QUALITY_TOO_LOW"
        await db.commit()
    async with maker() as db:
        await downloads.download_anyway(ids[0], db)
    video, _queue = await tdf._state(maker, ids)
    assert video.retry_count == 0


async def test_successful_download_resets_retry_count(monkeypatch, tmp_path):
    maker, (vid_id, ch_id, q_id), _old = await tdr._setup(monkeypatch, tmp_path)
    async with maker() as db:
        video = await db.get(Video, vid_id)
        video.retry_count = 2
        await db.commit()
    svc = tdr._service(monkeypatch, succeed=True)
    assert await svc.download_video(vid_id, ch_id, q_id) is True
    assert (await tdr._video(maker, vid_id)).retry_count == 0


# --- typed reads -----------------------------------------------------------

async def test_string_false_reads_false(monkeypatch):
    maker = await _memory_maker()
    async with maker() as db:
        db.add(AppSetting(key="jitter_enabled", value='"false"'))  # legacy raw-string store
        _put(db, "user_agent_rotation", False)
        _put(db, "max_retries", "4")
        await db.commit()
        assert await get_setting(db, "jitter_enabled") is False
        assert await get_setting(db, "user_agent_rotation") is False
        assert await get_setting(db, "max_retries") == 4
        # nothing saved: env value
        assert await get_setting(db, "max_concurrent_downloads") == tdf.ds.settings.MAX_CONCURRENT_DOWNLOADS
        assert await get_setting(db, "no_such_key", "x") == "x"
    assert coerce("false", True) is False
    assert coerce("True", False) is True
    assert coerce("junk", 3) == 3


# --- Log Level at startup --------------------------------------------------

async def test_saved_log_level_applies_at_startup(monkeypatch):
    maker = await _memory_maker()
    monkeypatch.setattr(database, "async_session", maker)
    root = logging.getLogger()
    before = root.level
    try:
        async with maker() as db:
            _put(db, "log_level", "debug")
            await db.commit()
        assert await settings_service.apply_runtime_settings() == "DEBUG"
        assert root.level == logging.DEBUG

        async with maker() as db:
            row = (await db.execute(select(AppSetting))).scalar_one()
            row.value = json.dumps("error")
            await db.commit()
        await settings_service.apply_runtime_settings()
        assert root.level == logging.ERROR
    finally:
        root.setLevel(before)


async def test_saved_ua_rotation_reaches_ytdlp_options(monkeypatch):
    from app.config import settings
    maker = await _memory_maker()
    monkeypatch.setattr(database, "async_session", maker)
    monkeypatch.setattr(settings, "USER_AGENT_ROTATION", True)
    async with maker() as db:
        _put(db, "user_agent_rotation", False)
        await db.commit()
    await settings_service.apply_runtime_settings()
    # ytdlp_service builds its options from settings.USER_AGENT_ROTATION in worker threads.
    assert settings.USER_AGENT_ROTATION is False


# --- config import / PUT /settings/{key} -----------------------------------

def _upload(payload) -> UploadFile:
    return UploadFile(file=io.BytesIO(json.dumps(payload).encode()), filename="backup.json")


@pytest.fixture(autouse=True)
def _restore_root_logger():
    root = logging.getLogger()
    level, ua = root.level, settings_service.settings.USER_AGENT_ROTATION
    yield
    root.setLevel(level)
    settings_service.settings.USER_AGENT_ROTATION = ua


async def test_a_file_that_is_not_a_backup_is_refused_whole():
    maker = await _memory_maker()
    async with maker() as db:
        for payload in ([1, 2], {"settings": [1]}, {"settings": {}, "channels": {"a": 1}}):
            with pytest.raises(HTTPException) as exc:
                await settings_router.import_config(db, _upload(payload))
            assert exc.value.status_code == 400
    assert await _setting_count(maker) == 0


async def test_invalid_settings_are_skipped_and_the_rest_restored():
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "naming_template", "{channel_name}/{title}")
        await db.commit()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "settings": {
                "default_quality": "720p",
                "max_retries": 200,
                "naming_template": "{channel_name}/{title} [{id}]",
                "jitter_enabled": False,
            },
        }))
    assert out["imported_settings"] == 2
    assert {s["key"] for s in out["skipped_settings"]} == {"max_retries", "naming_template"}
    assert all(s["reason"] for s in out["skipped_settings"])
    async with maker() as db:
        rows = {s.key: json.loads(s.value) for s in (await db.execute(select(AppSetting))).scalars()}
    assert rows == {"naming_template": "{channel_name}/{title}",  # current value kept
                    "default_quality": "720p", "jitter_enabled": False}


async def test_export_then_import_round_trip_skips_only_the_bad_ones():
    src = await _memory_maker()
    async with src() as db:
        _put(db, "default_quality", "480p")
        _put(db, "jitter_enabled", False)
        _put(db, "naming_template", "{channel_name}/{title} [{id}]")  # saved before validation existed
        _put(db, "max_retries", 200)
        db.add(Channel(channel_id="c1", channel_name="A", channel_url="u", platform="youtube",
                       health_status="unknown", quality="720p"))
        await db.commit()
        backup = await settings_router.export_config(db)

    dst = await _memory_maker()
    async with dst() as db:
        out = await settings_router.import_config(db, _upload(backup))
    assert out["imported_settings"] == 2 and out["imported_channels"] == 1
    assert {s["key"] for s in out["skipped_settings"]} == {"max_retries", "naming_template"}
    async with dst() as db:
        rows = {s.key: json.loads(s.value) for s in (await db.execute(select(AppSetting))).scalars()}
        assert rows == {"default_quality": "480p", "jitter_enabled": False}
        assert await db.scalar(select(func.count(Channel.id))) == 1


async def test_channel_with_unmounted_root_is_skipped_others_imported():
    from app.config import settings as cfg
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "settings": {},
            "channels": [
                {"channel_id": "c1", "channel_name": "A", "download_dir": "/mnt/not-mounted-xyz/a"},
                {"channel_id": "c2", "channel_name": "B", "download_dir": cfg.DOWNLOAD_DIR + "/../x"},
                {"channel_id": "c3", "channel_name": "C", "download_dir": cfg.DOWNLOAD_DIR + "/c"},
                {"channel_id": "c4", "channel_name": "D"},
            ],
        }))
        assert out["imported_channels"] == 2
        assert {c["channel_id"] for c in out["skipped_channels"]} == {"c1", "c2"}
        assert all(c["reason"] for c in out["skipped_channels"])
        ids = set((await db.execute(select(Channel.channel_id))).scalars())
        assert ids == {"c3", "c4"}  # never imported with the path cleared


async def test_existing_channel_with_bad_path_is_just_listed_as_existing():
    maker = await _memory_maker()
    async with maker() as db:
        db.add(Channel(channel_id="c1", channel_name="A", channel_url="u", platform="youtube",
                       health_status="unknown", quality="best"))
        await db.commit()
        out = await settings_router.import_config(db, _upload({
            "channels": [{"channel_id": "c1", "download_dir": "/mnt/not-mounted-xyz/a"}],
        }))
    assert out["skipped_channels"] == [{"channel_id": "c1", "channel_name": "c1", "reason": "already exists"}]


async def test_channel_whose_folder_is_not_a_path_is_skipped_not_a_500():
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "channels": [{"channel_id": "c1", "channel_name": "A", "download_dir": 5},
                         {"channel_id": "c2", "channel_name": "B"}],
        }))
    assert out["imported_channels"] == 1
    assert [c["channel_name"] for c in out["skipped_channels"]] == ["A"]


async def _window_after_import(file_hours):
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "scan_window_start_hour", 1)
        _put(db, "scan_window_end_hour", 9)
        await db.commit()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({"settings": file_hours}))
    async with maker() as db:
        rows = {s.key: json.loads(s.value) for s in (await db.execute(select(AppSetting))).scalars()}
    return out, (rows["scan_window_start_hour"], rows["scan_window_end_hour"])


async def test_hours_valid_alone_but_not_together_are_both_skipped():
    out, window = await _window_after_import({"scan_window_start_hour": 22, "scan_window_end_hour": 23})
    assert {s["key"] for s in out["skipped_settings"]} == {"scan_window_start_hour", "scan_window_end_hour"}
    assert window == (1, 9)


async def test_one_bad_hour_keeps_the_current_window_whole():
    # Importing only the good hour would mix the backup's end with the current start.
    out, window = await _window_after_import({"scan_window_start_hour": 24, "scan_window_end_hour": 6})
    assert {s["key"] for s in out["skipped_settings"]} == {"scan_window_start_hour", "scan_window_end_hour"}
    assert window == (1, 9)


async def test_log_level_applied_after_put_and_after_import():
    from app.schemas import SettingsUpdate
    maker = await _memory_maker()
    root = logging.getLogger()
    async with maker() as db:
        await settings_router.update_settings(SettingsUpdate(log_level="debug"), db)
    assert root.level == logging.DEBUG
    async with maker() as db:
        await settings_router.update_setting("log_level", "error", db)
    assert root.level == logging.ERROR
    async with maker() as db:
        await settings_router.import_config(db, _upload({"settings": {"log_level": "warning"}}))
    assert root.level == logging.WARNING


async def test_good_import_stores_typed_values():
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "settings": {"jitter_enabled": "false", "max_retries": "2", "mystery_key": 1},
        }))
        assert out["imported_settings"] == 2
        assert out["skipped_settings"] == []
    async with maker() as db:
        rows = {s.key: s.value for s in (await db.execute(select(AppSetting))).scalars()}
    assert rows == {"jitter_enabled": "false", "max_retries": "2"}  # JSON false / 2


async def test_put_key_stores_typed_value_and_rejects_unknown_keys():
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.update_setting("jitter_enabled", "false", db)
        assert out["value"] is False
        await settings_router.update_setting("max_retries", "4", db)
        with pytest.raises(HTTPException) as exc:
            await settings_router.update_setting("not_a_setting", "1", db)
        assert exc.value.status_code == 400
        with pytest.raises(HTTPException) as exc:
            await settings_router.update_setting("max_retries", "abc", db)
        assert exc.value.status_code == 400
    async with maker() as db:
        rows = {s.key: s.value for s in (await db.execute(select(AppSetting))).scalars()}
        assert rows == {"jitter_enabled": "false", "max_retries": "4"}
        assert await get_setting(db, "jitter_enabled") is False
