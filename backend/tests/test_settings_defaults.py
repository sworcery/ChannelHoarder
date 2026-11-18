"""Effective settings (S1), one defaults table and one window reader (S2),
per-channel date-failure clear (C1), runtime settings (C3), import checks (C7)."""
import io
import json

from fastapi import UploadFile
from sqlalchemy import select

import app.tasks.rss_check as rss_check
from app.models import AppSetting, Channel
from app.routers import settings as settings_router
from app.schemas import SettingsUpdate
from app.services import channel_service, settings_service
from app.services.settings_service import get_setting, read_scan_window
from tests.test_force_rescan_safety import isolate  # noqa: F401
from tests.test_settings_one_way import _memory_maker, _put, _restore_root_logger  # noqa: F401


def _upload(payload) -> UploadFile:
    return UploadFile(file=io.BytesIO(json.dumps(payload).encode()), filename="backup.json")


# --- S1 ---------------------------------------------------------------------

async def test_get_returns_the_effective_value_for_every_setting(monkeypatch):
    monkeypatch.setattr(settings_service.settings, "MAX_RETRIES", 10)
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "max_concurrent_downloads", 4)
        _put(db, "some_other_saved_key", "x")
        _put(db, "queue_paused", True)
        _put(db, "date_lookup_failures:1", {"v": 1})
        _put(db, settings_service.MIGRATION_KEY, True)
        await db.commit()
        out = await settings_router.get_all_settings(db)
        assert set(SettingsUpdate.model_fields) <= set(out)
        assert out["max_retries"] == 10  # env tier, nothing saved
        assert out["max_concurrent_downloads"] == 4  # saved row
        assert out["some_other_saved_key"] == "x"
        assert out["shorts_enabled"] is False
        assert "queue_paused" not in out
        assert not any(k.startswith(("date_lookup_failures:", "_")) for k in out)
        backup = await settings_router.export_config(db)
        assert not any(k.startswith("_") for k in backup["settings"])


def test_defaults_table_covers_every_settings_field():
    assert set(settings_service._DEFAULTS) == set(SettingsUpdate.model_fields)


async def test_env_override_beats_a_saved_row_once(monkeypatch):
    monkeypatch.setenv("MAX_RETRIES", "10")
    monkeypatch.setattr(settings_service.settings, "MAX_RETRIES", 10)
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "max_retries", 3)
        await db.commit()
    async with maker() as db:
        await settings_service.migrate_env_overrides(db)
    async with maker() as db:
        assert await get_setting(db, "max_retries") == 10
        await settings_router.update_settings(SettingsUpdate(max_retries=5), db)
    async with maker() as db:
        await settings_service.migrate_env_overrides(db)  # does not run again
    async with maker() as db:
        assert await get_setting(db, "max_retries") == 5


async def test_env_not_set_leaves_saved_rows_alone(monkeypatch):
    monkeypatch.delenv("MAX_RETRIES", raising=False)
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "max_retries", 3)
        await db.commit()
    async with maker() as db:
        await settings_service.migrate_env_overrides(db)
    async with maker() as db:
        assert await get_setting(db, "max_retries") == 3


# --- S2 ---------------------------------------------------------------------

async def test_stored_false_strings_read_false():
    maker = await _memory_maker()
    keys = ("shorts_enabled", "livestreams_enabled", "set_permissions", "subtitles_enabled")
    async with maker() as db:
        for k in keys:
            db.add(AppSetting(key=k, value=json.dumps("false")))
        await db.commit()
        for k in keys:
            assert await get_setting(db, k) is False


async def test_invalid_window_is_no_window_everywhere(monkeypatch):
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "scan_window_start_hour", 8)
        _put(db, "scan_window_end_hour", 10)  # 2 hours wide: not valid
        await db.commit()
        assert await read_scan_window(db) == (None, None)
    monkeypatch.setattr(rss_check, "async_session", maker)
    minutes, start, end, interval = await rss_check._read_settings()
    assert (start, end, interval) == (None, None, 12)
    # a stored string hour that validates is read as an int
    async with maker() as db:
        for row in (await db.execute(select(AppSetting))).scalars():
            row.value = json.dumps("22" if row.key.endswith("start_hour") else "6")
        await db.commit()
        assert await read_scan_window(db) == (22, 6)


# --- C1 ---------------------------------------------------------------------

async def test_date_failures_cleared_for_one_channel_or_all():
    maker = await _memory_maker()
    async with maker() as db:
        for pk in (1, 2):
            _put(db, f"date_lookup_failures:{pk}", {"v": 1})
        _put(db, "max_retries", 3)
        await db.commit()
        await channel_service.clear_date_lookup_failures(db, 1)
        await db.commit()
        assert await db.get(AppSetting, "date_lookup_failures:1") is None
        assert await db.get(AppSetting, "date_lookup_failures:2") is not None
        await channel_service.clear_date_lookup_failures(db)
        await db.commit()
        assert await db.get(AppSetting, "date_lookup_failures:2") is None
        assert await db.get(AppSetting, "max_retries") is not None


# --- C3 ---------------------------------------------------------------------

async def test_runtime_settings_do_not_change_the_env_tier(monkeypatch):
    monkeypatch.setattr(settings_service.settings, "USER_AGENT_ROTATION", True)
    monkeypatch.setattr(settings_service, "_ENV_USER_AGENT_ROTATION", True)
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "user_agent_rotation", False)
        await db.commit()
        await settings_service.apply_runtime_settings(db)
        assert settings_service.settings.USER_AGENT_ROTATION is False
        await db.delete(await db.get(AppSetting, "user_agent_rotation"))
        await db.commit()
        assert await get_setting(db, "user_agent_rotation") is True


# --- C7 ---------------------------------------------------------------------

async def test_import_skips_a_channel_that_fails_the_channel_schema():
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "settings": {},
            "channels": [
                {"channel_id": "bad1", "channel_name": "A", "channel_url": "u", "quality": "8k"},
                {"channel_id": "bad2", "channel_name": "B", "channel_url": "u", "title_filter_mode": "maybe"},
                {"channel_id": "bad3", "channel_name": "C", "channel_url": "u", "naming_template": "{nope"},
                {"channel_id": "ok", "channel_name": "D", "channel_url": "u", "quality": "720p"},
            ],
        }))
        assert out["imported_channels"] == 1
        assert {c["channel_id"] for c in out["skipped_channels"]} == {"bad1", "bad2", "bad3"}
        assert all(c["reason"] for c in out["skipped_channels"])
        assert (await db.execute(select(Channel.channel_id))).scalars().all() == ["ok"]


async def test_import_coerces_string_bools_and_never_raises():
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.import_config(db, _upload({
            "settings": {},
            "channels": [
                {"channel_id": "s", "channel_name": "S", "channel_url": "u", "enabled": "true",
                 "auto_download": "false", "include_shorts": "true", "include_livestreams": "false",
                 "prioritize_downloads": "true"},
                {"channel_id": "b1", "channel_name": "B", "channel_url": "u", "enabled": "perhaps"},
                {"channel_id": "b2", "channel_name": "B", "channel_url": "u", "include_shorts": "perhaps"},
                {"channel_id": "b3", "channel_name": "B", "channel_url": "u", "include_livestreams": [1]},
                {"channel_id": "b4", "channel_name": "B", "channel_url": "u", "prioritize_downloads": "x"},
                {"channel_id": "b5", "channel_name": 5, "channel_url": "u"},
            ],
        }))
        assert out["imported_channels"] == 1
        assert {c["channel_id"] for c in out["skipped_channels"]} == {"b1", "b2", "b3", "b4", "b5"}
        ch = (await db.execute(select(Channel))).scalar_one()
        assert (ch.enabled, ch.auto_download, ch.include_shorts,
                ch.include_livestreams, ch.prioritize_downloads) == (True, False, True, False, True)


# --- env tier is validated, migration cases, internal keys -------------------

async def test_env_tier_is_valid_for_the_page(monkeypatch):
    cfg = settings_service.settings
    monkeypatch.setattr(cfg, "LOG_LEVEL", "WARNING")
    monkeypatch.setattr(cfg, "DEFAULT_QUALITY", "ultra")
    monkeypatch.setattr(cfg, "MAX_RETRIES", 500)
    maker = await _memory_maker()
    async with maker() as db:
        out = await settings_router.get_all_settings(db)
    assert out["log_level"] == "warning"
    assert out["default_quality"] == "best"
    assert out["max_retries"] == 100
    SettingsUpdate.model_validate({k: out[k] for k in ("log_level", "default_quality", "max_retries")})
    monkeypatch.setattr(cfg, "LOG_LEVEL", "nonsense")
    async with maker() as db:
        assert await get_setting(db, "log_level") == "info"


async def test_migration_ua_rotation_and_upper_case_log_level(monkeypatch):
    monkeypatch.setenv("USER_AGENT_ROTATION", "false")
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    maker = await _memory_maker()
    async with maker() as db:
        _put(db, "user_agent_rotation", True)
        _put(db, "log_level", "info")
        await db.commit()
        await settings_service.migrate_env_overrides(db)
    async with maker() as db:
        rows = {s.key: json.loads(s.value) for s in (await db.execute(select(AppSetting))).scalars()}
    assert rows["user_agent_rotation"] is False
    assert rows["log_level"] == "debug"


async def test_migration_on_a_fresh_install_writes_only_the_marker(monkeypatch):
    monkeypatch.setenv("MAX_RETRIES", "10")
    monkeypatch.setenv("LOG_LEVEL", "debug")
    maker = await _memory_maker()
    async with maker() as db:
        await settings_service.migrate_env_overrides(db)
    async with maker() as db:
        keys = (await db.execute(select(AppSetting.key))).scalars().all()
    assert keys == [settings_service.MIGRATION_KEY]


async def test_internal_keys_are_not_served_one_by_one():
    from fastapi import HTTPException
    import pytest
    maker = await _memory_maker()
    async with maker() as db:
        for k in (settings_service.MIGRATION_KEY, "queue_paused", "date_lookup_failures:3"):
            _put(db, k, True)
        _put(db, "max_retries", 4)
        await db.commit()
        for k in (settings_service.MIGRATION_KEY, "queue_paused", "date_lookup_failures:3", "nope"):
            with pytest.raises(HTTPException) as exc:
                await settings_router.get_setting(k, db)
            assert exc.value.status_code == 404
        assert (await settings_router.get_setting("max_retries", db))["value"] == 4


# --- C1 through Force Rescan ---------------------------------------------------

async def test_force_rescan_of_a_leaves_bs_date_failures(monkeypatch, tmp_path):
    from tests import test_force_rescan_safety as frs
    from app.routers.channels import force_rescan
    eng, maker = await frs._maker()
    try:
        async with maker() as db:
            a = await frs._seed(db, tmp_path)
            b = Channel(channel_id="UC_b", channel_name="B", channel_url="https://www.youtube.com/@b",
                        platform="youtube", auto_download=False, health_status="unknown")
            db.add(b)
            await db.flush()
            _put(db, f"date_lookup_failures:{a.id}", {"v": 1})
            _put(db, f"date_lookup_failures:{b.id}", {"v": 1})
            await db.commit()
            frs._listing(monkeypatch, [frs._entry("new_video001")])
            await force_rescan(a.id, db)
            assert await db.get(AppSetting, f"date_lookup_failures:{b.id}") is not None
            assert await db.get(AppSetting, f"date_lookup_failures:{a.id}") is None
    finally:
        await eng.dispose()
