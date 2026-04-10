from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(
    settings.db_url,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False, "timeout": 30},
    pool_size=10,
    max_overflow=10,
    pool_pre_ping=True,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragma(dbapi_conn, connection_record):
    """Optimize SQLite for concurrent web-app usage."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Performance: 64MB page cache (default is ~2MB), 256MB mmap, temp tables in memory
    cursor.execute("PRAGMA cache_size=-65536")
    cursor.execute("PRAGMA mmap_size=268435456")
    cursor.execute("PRAGMA temp_store=MEMORY")
    cursor.close()

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def init_database():
    # Import models so Base.metadata knows about all tables before create_all
    import app.models  # noqa: F401

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
        if "banner_url" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN banner_url VARCHAR(512)")
            )
        if "include_shorts" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN include_shorts BOOLEAN DEFAULT 0 NOT NULL")
            )
        if "auto_download" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN auto_download BOOLEAN DEFAULT 1 NOT NULL")
            )

        # Add is_short column to videos table
        result2 = await conn.execute(text("PRAGMA table_info(videos)"))
        video_columns = {row[1] for row in result2.fetchall()}
        if "is_short" not in video_columns:
            await conn.execute(
                text("ALTER TABLE videos ADD COLUMN is_short BOOLEAN DEFAULT 0 NOT NULL")
            )


# Note: get_db() dependency is defined in deps.py  - do not duplicate here
