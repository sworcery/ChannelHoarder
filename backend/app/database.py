from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.db_url,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=5,
    max_overflow=5,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable WAL mode and busy timeout for SQLite concurrency."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_database():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        # Pragmatic migration: add 'platform' column to channels if missing
        # (create_all doesn't add columns to existing tables)
        result = await conn.execute(text("PRAGMA table_info(channels)"))
        columns = {row[1] for row in result.fetchall()}
        if "platform" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN platform VARCHAR(32) DEFAULT 'youtube' NOT NULL")
            )


async def get_db():
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
