import asyncio
import io
import json

import pytest
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.config import settings
from app.database import Base
from app.models import AppSetting
from app.routers import auth, downloads
from app.routers import settings as settings_router
from app.services import queue_state as qs
from app.tasks import cookie_recovery, cookie_watcher


async def _maker():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)


async def _raw(db, key):
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    return row.value if row else None


async def _set_raw(db, key, value):
    db.add(AppSetting(key=key, value=value))
    await db.commit()


def _upload(content=b"# Netscape\n" * 10):
    return UploadFile(file=io.BytesIO(content), filename="cookies.txt")


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path / "config"))
    watch = tmp_path / "watch"
    watch.mkdir()
    monkeypatch.setattr(settings, "COOKIE_WATCH_DIR", str(watch))
    return watch


def test_cookie_pause_survives_restart_then_upload_resumes(cfg, monkeypatch):
    async def run():
        maker = await _maker()
        monkeypatch.setattr(auth, "async_session", maker)
        async with maker() as db:
            await qs.set_cookies_expired(db, True)
            await qs.pause_queue(db, qs.REASON_COOKIES)
            await db.commit()
            # simulated restart: boot normalisation
            await qs.normalize_flags(db)
            await db.commit()
            assert await qs.get_pause_state(db) == (True, "cookies_expired")
            assert await qs.get_cookies_expired(db) is True

        await auth.upload_cookies(_upload())

        async with maker() as db:
            assert await qs.get_pause_state(db) == (False, None)
            assert await qs.get_cookies_expired(db) is False

    asyncio.run(run())


def test_old_raw_cookie_pause_resumes_after_normalise(cfg, monkeypatch):
    async def run():
        maker = await _maker()
        monkeypatch.setattr(auth, "async_session", maker)
        async with maker() as db:
            await _set_raw(db, "queue_paused", "true")
            await _set_raw(db, "queue_pause_reason", "cookies_expired")  # raw, unquoted
            await _set_raw(db, "cookies_expired", "true")
            assert await qs.get_pause_state(db) == (True, "cookies_expired")
            await qs.normalize_flags(db)
            await db.commit()
            assert await _raw(db, "queue_pause_reason") == '"cookies_expired"'
        await auth.upload_cookies(_upload())
        async with maker() as db:
            assert await qs.get_pause_state(db) == (False, None)

    asyncio.run(run())


def test_stale_cookie_reason_without_flag_becomes_manual(cfg, monkeypatch):
    # Old versions left the reason after Resume; a later manual pause then read
    # as a cookie pause. Without the cookies flag it must stay paused on upload.
    async def run():
        maker = await _maker()
        monkeypatch.setattr(auth, "async_session", maker)
        async with maker() as db:
            await _set_raw(db, "queue_paused", "true")
            await _set_raw(db, "queue_pause_reason", "cookies_expired")
            await _set_raw(db, "cookies_expired", "false")
            await qs.normalize_flags(db)
            await db.commit()
            assert await qs.get_pause_state(db) == (True, "manual")
        result = await auth.upload_cookies(_upload())
        assert "Queue resumed" not in result["message"]
        async with maker() as db:
            assert await qs.get_pause_state(db) == (True, "manual")

    asyncio.run(run())


def test_manual_pause_survives_upload_watcher_and_recovery(cfg, monkeypatch):
    async def run():
        maker = await _maker()
        monkeypatch.setattr(auth, "async_session", maker)
        monkeypatch.setattr(cookie_watcher, "async_session", maker)
        monkeypatch.setattr(cookie_recovery, "async_session", maker)

        async def fake_broadcast(*a, **k):
            return None

        monkeypatch.setattr(cookie_watcher.NotificationService, "broadcast", fake_broadcast)

        class Boom:
            def test_download_capability(self):
                raise AssertionError("auth test must not run for a manual pause")

        monkeypatch.setattr(cookie_recovery, "YtdlpService", Boom)

        async with maker() as db:
            await downloads.pause_queue(db)

        await auth.upload_cookies(_upload())

        (cfg / "cookies.txt").write_bytes(b"x" * 100)
        await cookie_watcher.watch_cookie_file()

        await cookie_recovery.check_cookie_recovery()

        async with maker() as db:
            assert await qs.get_pause_state(db) == (True, "manual")

    asyncio.run(run())


def test_cookie_recovery_resumes_cookie_pause(cfg, monkeypatch):
    async def run():
        maker = await _maker()
        monkeypatch.setattr(cookie_recovery, "async_session", maker)

        async def fake_broadcast(*a, **k):
            return None

        monkeypatch.setattr(cookie_recovery.NotificationService, "broadcast", fake_broadcast)

        class Ok:
            def test_download_capability(self):
                return True, "ok"

        monkeypatch.setattr(cookie_recovery, "YtdlpService", Ok)
        async with maker() as db:
            await qs.set_cookies_expired(db, True)
            await qs.pause_queue(db, qs.REASON_COOKIES)
            await db.commit()
        await cookie_recovery.check_cookie_recovery()
        async with maker() as db:
            assert await qs.get_pause_state(db) == (False, None)
            assert await qs.get_cookies_expired(db) is False

    asyncio.run(run())


def test_flagging_cookies_keeps_existing_manual_pause():
    async def run():
        maker = await _maker()
        from app.utils.cookie_utils import flag_cookies_expired
        async with maker() as db:
            await qs.pause_queue(db, qs.REASON_MANUAL)
            await flag_cookies_expired(db)
            assert await qs.get_pause_state(db) == (True, "manual")
            assert await qs.get_cookies_expired(db) is True

    asyncio.run(run())


def test_resume_clears_reason():
    async def run():
        maker = await _maker()
        async with maker() as db:
            await qs.pause_queue(db, "disk_full")
            assert await qs.get_pause_state(db) == (True, "disk_full")
            await downloads.resume_queue(db)
            assert await qs.get_pause_state(db) == (False, None)
            assert json.loads(await _raw(db, "queue_pause_reason")) == ""

    asyncio.run(run())


def test_reads_old_values():
    async def run():
        maker = await _maker()
        async with maker() as db:
            assert await qs.get_pause_state(db) == (False, None)
            await _set_raw(db, "queue_paused", "true")
            # paused before reasons existed: treated as manual
            assert await qs.get_pause_state(db) == (True, "manual")
        async with maker() as db:
            row = (await db.execute(select(AppSetting).where(AppSetting.key == "queue_pause_reason"))).scalar_one_or_none()
            assert row is None
            await _set_raw(db, "queue_pause_reason", '"cookies_expired"')
            assert await qs.get_pause_state(db) == (True, "cookies_expired")

    asyncio.run(run())


def test_export_omits_flags_and_import_ignores_them():
    async def run():
        maker = await _maker()
        async with maker() as db:
            await qs.pause_queue(db, qs.REASON_COOKIES)
            await qs.set_cookies_expired(db, True)
            db.add(AppSetting(key="max_concurrent_downloads", value="3"))
            await db.commit()

            data = await settings_router.export_config(db)
            for key in ("queue_paused", "queue_pause_reason", "cookies_expired"):
                assert key not in data["settings"]
            assert data["settings"]["max_concurrent_downloads"] == 3

        # a fresh install importing a backup that (wrongly) carries the flags
        maker2 = await _maker()
        backup = {"settings": {"queue_paused": True, "queue_pause_reason": "cookies_expired",
                               "cookies_expired": True, "max_concurrent_downloads": 2},
                  "channels": []}
        upload = UploadFile(file=io.BytesIO(json.dumps(backup).encode()), filename="b.json")
        async with maker2() as db:
            res = await settings_router.import_config(db=db, file=upload)
            assert res["imported_settings"] == 1
            assert await qs.get_pause_state(db) == (False, None)
            assert await qs.get_cookies_expired(db) is False

    asyncio.run(run())
