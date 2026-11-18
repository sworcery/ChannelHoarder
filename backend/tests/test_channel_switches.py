import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.routers.channels as channels_router
from app.database import Base
from app.models import Channel
from app.schemas import ChannelUpdate


async def _make_channel():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    async with maker() as db:
        ch = Channel(channel_id="c1", channel_name="Chan", channel_url="u1",
                     platform="youtube", health_status="unknown",
                     enabled=True, auto_download=True)
        db.add(ch)
        await db.commit()
        cid = ch.id
    return maker, cid


@pytest.mark.asyncio
async def test_auto_download_update_leaves_enabled_alone():
    maker, cid = await _make_channel()
    async with maker() as db:
        ch = await channels_router.update_channel(cid, ChannelUpdate(auto_download=False), db)
        assert ch.auto_download is False
        assert ch.enabled is True


@pytest.mark.asyncio
async def test_enabled_update_leaves_auto_download_alone():
    maker, cid = await _make_channel()
    async with maker() as db:
        ch = await channels_router.update_channel(cid, ChannelUpdate(enabled=False), db)
        assert ch.enabled is False
        assert ch.auto_download is True
