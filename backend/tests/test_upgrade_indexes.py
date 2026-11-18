import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401
from app import database
from app.database import Base, init_database


def _expected_indexes():
    return {i.name for t in Base.metadata.tables.values() for i in t.indexes}


async def _index_names(engine):
    async with engine.connect() as conn:
        rows = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")
        )
        return {r[0] for r in rows.fetchall()}


@pytest.fixture
def temp_engine(tmp_path, monkeypatch):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    monkeypatch.setattr(database, "engine", eng)
    yield eng
    asyncio.run(eng.dispose())


def test_expected_indexes_nonempty():
    assert len(_expected_indexes()) >= 3


@pytest.mark.asyncio
async def test_fresh_create_all_has_indexes(temp_engine):
    async with temp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    assert _expected_indexes() <= await _index_names(temp_engine)


@pytest.mark.asyncio
async def test_upgrade_adds_dropped_indexes_and_is_idempotent(temp_engine):
    async with temp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for name in _expected_indexes():
            await conn.execute(text(f"DROP INDEX {name}"))
    assert not (_expected_indexes() & await _index_names(temp_engine))

    await init_database()
    assert _expected_indexes() <= await _index_names(temp_engine)
    await init_database()
    assert _expected_indexes() <= await _index_names(temp_engine)


@pytest.mark.asyncio
async def test_duplicate_rows_skip_that_unique_index_but_the_app_starts(temp_engine):
    # An old database can hold rows a unique index refuses; startup must not fail.
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from app.models import Channel

    unique_name = next(i.name for i in Channel.__table__.indexes if i.unique)
    async with temp_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for name in _expected_indexes():
            await conn.execute(text(f"DROP INDEX {name}"))
    maker = async_sessionmaker(temp_engine, class_=AsyncSession)
    async with maker() as db:
        for _ in range(2):
            db.add(Channel(channel_id="dup", channel_name="Dup", channel_url="u",
                           platform="youtube", health_status="unknown"))
        await db.commit()

    await init_database()  # must not raise
    names = await _index_names(temp_engine)
    assert unique_name not in names
    assert (_expected_indexes() - {unique_name}) <= names
