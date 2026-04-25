import logging

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

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
    """Optimize SQLite for concurrent web-app usage."""
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
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
        if "quality_cutoff" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN quality_cutoff VARCHAR(10)")
            )
        if "min_video_duration" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN min_video_duration INTEGER")
            )
        if "auto_download" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN auto_download BOOLEAN DEFAULT 1 NOT NULL")
            )
        if "include_livestreams" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN include_livestreams BOOLEAN DEFAULT 0 NOT NULL")
            )
        if "next_scan_at" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN next_scan_at DATETIME")
            )
        if "download_from_year" not in columns:
            await conn.execute(
                text("ALTER TABLE channels ADD COLUMN download_from_year INTEGER")
            )

        # Add is_short column to videos table
        result2 = await conn.execute(text("PRAGMA table_info(videos)"))
        video_columns = {row[1] for row in result2.fetchall()}
        if "is_short" not in video_columns:
            await conn.execute(
                text("ALTER TABLE videos ADD COLUMN is_short BOOLEAN DEFAULT 0 NOT NULL")
            )
        if "monitored" not in video_columns:
            await conn.execute(
                text("ALTER TABLE videos ADD COLUMN monitored BOOLEAN DEFAULT 1 NOT NULL")
            )
        if "is_livestream" not in video_columns:
            await conn.execute(
                text("ALTER TABLE videos ADD COLUMN is_livestream BOOLEAN DEFAULT 0 NOT NULL")
            )

        # Add target_quality and estimated_size to download_queue
        result3 = await conn.execute(text("PRAGMA table_info(download_queue)"))
        queue_columns = {row[1] for row in result3.fetchall()}
        if "target_quality" not in queue_columns:
            await conn.execute(
                text("ALTER TABLE download_queue ADD COLUMN target_quality VARCHAR(10)")
            )
        if "estimated_size" not in queue_columns:
            await conn.execute(
                text("ALTER TABLE download_queue ADD COLUMN estimated_size BIGINT")
            )

        # Clean up orphan records: videos whose channel no longer exists
        orphan_count = await conn.execute(text(
            "SELECT COUNT(*) FROM videos WHERE channel_id NOT IN (SELECT id FROM channels)"
        ))
        count = orphan_count.scalar() or 0
        if count > 0:
            await conn.execute(text(
                "DELETE FROM download_queue WHERE video_id IN "
                "(SELECT id FROM videos WHERE channel_id NOT IN (SELECT id FROM channels))"
            ))
            await conn.execute(text(
                "DELETE FROM download_log WHERE video_id IN "
                "(SELECT id FROM videos WHERE channel_id NOT IN (SELECT id FROM channels))"
            ))
            await conn.execute(text(
                "DELETE FROM videos WHERE channel_id NOT IN (SELECT id FROM channels)"
            ))
            logger.info("Cleaned up %d orphan video records on startup", count)

        # Stagger next_scan_at for channels without one set
        # Use deterministic per-channel offsets based on channel.id so restarts are idempotent
        null_scan_result = await conn.execute(text(
            "SELECT id FROM channels WHERE next_scan_at IS NULL AND enabled = 1"
        ))
        null_ids = [row[0] for row in null_scan_result.fetchall()]
        if null_ids:
            import random as _rand
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            now_utc = _dt.now(_tz.utc).replace(tzinfo=None)
            for cid in null_ids:
                rng = _rand.Random(cid)
                offset_seconds = rng.randint(60, 24 * 3600)  # 1 min to 24h out
                scan_time = now_utc + _td(seconds=offset_seconds)
                await conn.execute(
                    text("UPDATE channels SET next_scan_at = :t WHERE id = :id"),
                    {"t": scan_time.strftime("%Y-%m-%d %H:%M:%S"), "id": cid},
                )
            logger.info("Staggered initial next_scan_at for %d existing channels", len(null_ids))


# Note: get_db() dependency is defined in deps.py  - do not duplicate here
