import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, func, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models import AppSetting, Channel, Video, DownloadQueue, DownloadLog
from app.services import channel_activity, queue_state
from app.services.diagnostics_service import DiagnosticsService
from app.services.settings_service import get_setting, get_settings
from app.services.metadata_service import write_episode_nfo, write_tvshow_nfo
from app.services.naming_service import build_output_path
from app.utils.file_utils import channel_folder_name
from app.services.notification_service import NotificationService
from app.services.ytdlp_service import YtdlpService
from app.utils.error_codes import ErrorCode, ERROR_CATALOG, classify_error
from app.utils.file_utils import delete_video_files
from app.utils.platform_utils import build_video_url
from app.utils.rate_limiter import wait_for_rate_limit

logger = logging.getLogger(__name__)

# Video IDs of downloads currently running; the set lives in channel_activity.
# A set (not a bool) so that with max_concurrent_downloads > 1 the first download to
# finish doesn't make is_download_active() report False while others are still running
# (which let the PO-token watchdog probe/restart the PO server under a live download).
_active_downloads = channel_activity.active_downloads


def is_download_active() -> bool:
    return bool(_active_downloads)


def attempt_temp_dir(download_root: str) -> str:
    """Per-attempt working dir: under TEMP_DIR if set, else under the download root."""
    base = settings.TEMP_DIR or download_root
    return os.path.join(base, ".channelhoarder-tmp", uuid4().hex)


def create_attempt_dir(temp_dir: str) -> None:
    try:
        os.makedirs(temp_dir, exist_ok=True)
    except OSError as e:
        if settings.TEMP_DIR:
            raise Exception(
                f"Cannot create download temp folder under TEMP_DIR ({temp_dir}): {e}. "
                "Check that TEMP_DIR exists and is writable."
            ) from e
        raise


def move_finished_files(temp_dir: str, final_parent: str) -> None:
    """Move finished files into place. Blocking; call via asyncio.to_thread.

    Falls back to copy-to-sibling then rename when a plain rename fails (cross-drive),
    so a half-copied file is never visible at the final path.
    """
    for f in os.listdir(temp_dir):
        src, dst = os.path.join(temp_dir, f), os.path.join(final_parent, f)
        try:
            os.replace(src, dst)
            continue
        except OSError:
            pass
        partial = dst + ".chmove"
        try:
            shutil.copyfile(src, partial)
            try:
                # Keep the file's times (yt-dlp sets mtime to the upload date). Drives
                # such as NTFS/exFAT can refuse permission/time changes; that must not
                # fail an otherwise finished download.
                shutil.copystat(src, partial)
            except OSError:
                pass
            os.replace(partial, dst)
        except BaseException:
            try:
                os.remove(partial)
            except OSError:
                pass
            raise
        os.remove(src)


def _remove_replaced_file(old_path: str, new_path: str) -> None:
    """Remove the file a finished re-download replaced. Blocking.

    Never touches the old path when it is the very file just written (same text,
    case-only difference on a case-insensitive mount, or a symlinked folder). When
    the old file shares the new file's name stem (e.g. .mkv -> .mp4) only the old
    video file goes: the sidecars at that stem now belong to the new download.
    """
    if not os.path.exists(old_path):
        return
    if os.path.abspath(old_path) == os.path.abspath(new_path):
        return
    try:
        if os.path.exists(new_path) and os.path.samefile(old_path, new_path):
            return
    except OSError:
        return
    old_stem = os.path.splitext(old_path)[0]
    new_stem = os.path.splitext(new_path)[0]
    same_stem = old_stem == new_stem
    if not same_stem:
        try:
            same_stem = (
                os.path.samefile(os.path.dirname(old_path), os.path.dirname(new_path))
                and os.path.basename(old_stem).lower() == os.path.basename(new_stem).lower()
            )
        except OSError:
            same_stem = False
    if same_stem:
        os.remove(old_path)
    else:
        delete_video_files(old_path)


# Minimum-quality waits: a fresh upload may only offer low formats for a while.
QUALITY_RETRY_DELAYS = (timedelta(hours=1), timedelta(hours=6), timedelta(hours=24))
# A failed format lookup that has no clearer cause is retried after a short pause.
LOOKUP_FAILED_MARKER = "could not read the available formats"
LOOKUP_RETRY_DELAY = timedelta(minutes=10)

# Errors that mean this video can't be fetched again (as opposed to cookies, yt-dlp
# or disk trouble that a later run can get past).
_VIDEO_GONE_CODES = frozenset({
    ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.VIDEO_PRIVATE, ErrorCode.VIDEO_REMOVED,
    ErrorCode.GEO_BLOCKED, ErrorCode.AGE_RESTRICTED,
})


_ENQUEUE_CHUNK = 500


async def enqueue_video(
    db: AsyncSession,
    video: Video,
    *,
    priority: int = 0,
    target_quality: str | None = None,
    skip_min_quality: bool = False,
    reset_retries: bool = False,
) -> DownloadQueue:
    """The one way to queue a video: set it "queued" and make sure it has a queue row.

    Rules:
    - A video never has two rows (download_queue.video_id is unique). An existing
      row is reused; two racing callers end with one row.
    - Reused row: priority only goes up; retry_after and quality_attempts reset and
      skip_min_quality is set to what the caller asks (a fresh queue); target_quality
      is replaced only when given.
    - A row that is already running (started_at set) is left running: its video stays
      "downloading" and only the priority bump applies (nothing else is reset).
    - Error fields and retention_removed_at are cleared: queueing means "wanted again".
    - reset_retries (manual Retry / Download anyway) gives the video a fresh retry
      count; automatic callers leave it so a failing video still ends up failed.
    Flushes; the caller commits.
    """
    if reset_retries:
        video.retry_count = 0

    async def _load() -> DownloadQueue | None:
        return (await db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == video.id)
        )).scalar_one_or_none()

    entry = await _load()
    if entry is None:
        # Video fields first, so the row never exists (and gets dispatched) while the
        # video still says something else. No savepoint: on SQLite one would commit.
        video.status = "queued"
        video.retention_removed_at = None
        video.error_code = None
        video.error_message = None
        video.error_details = None
        await db.flush()
        inserted = await db.execute(
            sqlite_insert(DownloadQueue)
            .values(
                video_id=video.id,
                priority=priority,
                target_quality=target_quality,
                skip_min_quality=skip_min_quality,
                quality_attempts=0,
                progress_percent=0.0,
            )
            .on_conflict_do_nothing(index_elements=["video_id"])
        )
        entry = await _load()
        if inserted.rowcount == 1:
            return entry
        # Lost a race with another request: fall through and reuse the winner's row.

    entry.priority = max(entry.priority, priority)
    if entry.started_at is None:
        entry.retry_after = None
        entry.quality_attempts = 0
        entry.skip_min_quality = skip_min_quality
        if target_quality is not None:
            entry.target_quality = target_quality
        video.status = "queued"
        video.retention_removed_at = None
        video.error_code = None
        video.error_message = None
        video.error_details = None
    await db.flush()
    return entry


async def enqueue_videos(
    db: AsyncSession,
    videos: list[Video],
    *,
    priority: int = 0,
    target_quality: str | None = None,
    reset_retries: bool = False,
) -> int:
    """Bulk enqueue_video: same rules, a few statements per 500 videos instead of
    several per video, so a 50,000-video request does not hold the write lock for
    minutes. Returns how many new queue rows were created. Flushes; the caller
    commits. The videos' in-memory state is not refreshed (the DB rows are updated).
    """
    created = 0
    for i in range(0, len(videos), _ENQUEUE_CHUNK):
        ids = [v.id for v in videos[i:i + _ENQUEUE_CHUNK]]
        await db.flush()
        running = select(DownloadQueue.video_id).where(
            DownloadQueue.video_id.in_(ids), DownloadQueue.started_at.isnot(None)
        )
        # Video fields first (skipping videos whose download is running)
        await db.execute(
            update(Video)
            .where(Video.id.in_(ids), Video.id.notin_(running))
            .values(
                status="queued", retention_removed_at=None,
                error_code=None, error_message=None, error_details=None,
                **({"retry_count": 0} if reset_retries else {}),
            )
            .execution_options(synchronize_session=False)
        )
        result = await db.execute(
            sqlite_insert(DownloadQueue)
            .values([
                {
                    "video_id": vid, "priority": priority, "target_quality": target_quality,
                    "skip_min_quality": False, "quality_attempts": 0, "progress_percent": 0.0,
                }
                for vid in ids
            ])
            .on_conflict_do_nothing(index_elements=["video_id"])
        )
        created += result.rowcount or 0
        # Rows that already existed: reuse (priority only goes up; running rows untouched)
        reuse = {
            "priority": func.max(DownloadQueue.priority, priority),
            "retry_after": None, "quality_attempts": 0, "skip_min_quality": False,
        }
        if target_quality is not None:
            reuse["target_quality"] = target_quality
        await db.execute(
            update(DownloadQueue)
            .where(DownloadQueue.video_id.in_(ids), DownloadQueue.started_at.is_(None))
            .values(**reuse)
            .execution_options(synchronize_session=False)
        )
        await db.execute(
            update(DownloadQueue)
            .where(DownloadQueue.video_id.in_(ids), DownloadQueue.started_at.isnot(None))
            .values(priority=func.max(DownloadQueue.priority, priority))
            .execution_options(synchronize_session=False)
        )
    return created


class DownloadService:
    """Manages video downloads with short-lived DB sessions.

    The key architectural constraint: the DB connection must NOT be held open
    during the actual yt-dlp download (which can take 15+ minutes). SQLite
    only allows one writer at a time; a long-held connection starves dashboard
    queries and API requests. Instead we use three brief sessions:

    1. Pre-download: mark video as downloading, gather metadata
    2. Download: yt-dlp runs with NO open DB session
    3. Post-download: record success or failure
    """

    def __init__(self):
        self.ytdlp = YtdlpService()
        self.notification = NotificationService()

    async def download_video(self, video_id: int, channel_id: int, queue_id: int) -> bool:
        """Download a single video. Returns True on success."""

        # ── Phase 1: mark as downloading and gather plain data ───────────
        pre_error: Exception | None = None
        async with async_session() as db:
            video = await db.get(Video, video_id)
            channel = await db.get(Channel, channel_id)
            queue_entry = await db.get(DownloadQueue, queue_id)

            if not video or not channel or not queue_entry:
                logger.error("Download task: missing video/channel/queue for video_id=%d", video_id)
                return False

            video_url = video.source_url or build_video_url(channel.platform, video.video_id)
            skip_min_quality = bool(queue_entry.skip_min_quality)
            video.status = "downloading"
            if not queue_entry.started_at:
                queue_entry.started_at = datetime.now(timezone.utc)
            await db.commit()

            # Snapshot all ORM data we need into plain Python values so we
            # can close the session before the long-running download.
            vdata = _VideoData(
                pk=video.id,
                video_id=video.video_id,
                title=video.title,
                description=video.description,
                upload_date=video.upload_date,
                season=video.season,
                episode=video.episode,
                duration=video.duration,
                thumbnail_url=video.thumbnail_url,
            )
            # From here until the main try, any error goes through _record_failure so
            # the row never stays "started" with nobody running it.
            try:
                db.add(DownloadLog(video_id=video.id, event="started", message=f"Starting download: {video.title}"))
                await db.commit()

                await self.notification.broadcast("download_progress", {
                    "video_id": video.video_id,
                    "title": video.title,
                    "status": "downloading",
                    "percent": 0,
                })
                cdata = _ChannelData(
                pk=channel.id,
                    channel_id=channel.channel_id,
                    channel_name=channel.channel_name,
                    channel_url=channel.channel_url,
                    platform=channel.platform,
                    description=channel.description,
                    thumbnail_url=channel.thumbnail_url,
                    quality=channel.quality,
                    min_quality=channel.min_quality,
                    preferred_codec=channel.preferred_codec,
                    naming_template=channel.naming_template,
                    download_dir=channel.download_dir,
                    folder_name=channel_folder_name(channel),
                )

                # Read download feature settings and global fallbacks while we have a DB session
                features = await get_settings(db, ["subtitles_enabled", "chapters_enabled", "sponsorblock_mode"])
                subtitles_enabled = bool(features["subtitles_enabled"])
                chapters_enabled = bool(features["chapters_enabled"])
                sponsorblock_mode = features["sponsorblock_mode"] or "off"

                # Per-channel naming_template takes priority; fall back to global setting.
                # Shared with every rename/renumber path so a later scan can't compute a
                # different path than the one this download writes to.
                from app.services.naming_service import resolve_naming_template
                effective_template = await resolve_naming_template(db, cdata.naming_template)

                output_path = build_output_path(
                    channel_name=cdata.channel_name,
                    video_title=vdata.title,
                    video_id=vdata.video_id,
                    upload_date=vdata.upload_date,
                    season=vdata.season,
                    episode=vdata.episode,
                    naming_template=effective_template,
                    base_dir=cdata.download_dir,
                    folder_name=cdata.folder_name,
                )
            except Exception as e:
                pre_error = e
        # ← session released

        if pre_error is not None:
            await self._record_failure(video_id, channel_id, queue_id, pre_error, vdata)
            return False

        # ── Phase 2: actual download  - no DB session held ────────────────
        # Per-attempt isolated working directory. A stalled download's yt-dlp thread
        # cannot be killed (asyncio.to_thread cancellation doesn't stop the OS thread),
        # so the orphan may keep writing while the retry runs. Giving every attempt its
        # own directory means they can never write over each other; finished files are
        # moved to output_path on success. Rooted under the channel's own download root
        # so the final move is an atomic same-filesystem rename. Orphaned dirs are
        # reclaimed by the cleanup_download_temp task.
        try:
            os.makedirs(os.path.dirname(output_path), exist_ok=True)
            download_root = cdata.download_dir or settings.DOWNLOAD_DIR
            temp_dir = attempt_temp_dir(download_root)
        except Exception as e:
            await self._record_failure(video_id, channel_id, queue_id, e, vdata)
            return False

        _loop = asyncio.get_running_loop()
        import time
        _download_start = time.monotonic()

        async def _broadcast_progress(d: dict):
            if d.get("status") == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                downloaded = d.get("downloaded_bytes", 0)
                percent = (downloaded / total * 100) if total > 0 else 0
                speed = d.get("speed", 0) or 0
                eta = d.get("eta", 0) or 0
                elapsed = time.monotonic() - _download_start

                await self.notification.broadcast("download_progress", {
                    "video_id": vdata.video_id,
                    "title": vdata.title,
                    "status": "downloading",
                    "percent": round(percent, 1),
                    "speed": self._format_speed(speed),
                    "eta": self._format_eta(eta),
                    "downloaded_bytes": int(downloaded),
                    "total_bytes": int(total),
                    "speed_bytes": round(speed, 1),
                    "eta_seconds": int(eta),
                    "elapsed_seconds": int(elapsed),
                })

        _last_progress_time = time.monotonic()
        _cancel_requested = False

        def progress_hook(d: dict):
            nonlocal _last_progress_time
            if _cancel_requested:
                raise Exception("Download cancelled - no progress detected")
            _last_progress_time = time.monotonic()
            try:
                asyncio.run_coroutine_threadsafe(_broadcast_progress(d), _loop)
            except Exception:
                pass

        def pp_hook(d: dict):
            nonlocal _last_progress_time
            _last_progress_time = time.monotonic()

        try:
            create_attempt_dir(temp_dir)
            await wait_for_rate_limit()

            # Check minimum quality requirement before downloading
            # ("Download anyway" skips it.) A failed lookup is a failure to retry, not a pass.
            if cdata.min_quality and not skip_min_quality:
                from app.utils.quality_utils import best_available_quality, quality_rank
                info, lookup_error = await asyncio.to_thread(
                    self.ytdlp.get_video_info_or_error, vdata.video_id, cdata.platform, video_url
                )
                if not info:
                    # Carry yt-dlp's own error text so the classifier sees the real cause
                    # (cookies, private, geo-blocked, premiere ...).
                    raise Exception(
                        f"Could not read the available formats to check the minimum quality: "
                        f"{lookup_error or 'no metadata returned'}"
                    )
                best_q = best_available_quality(info.get("formats") or [])
                if best_q and quality_rank(best_q) < quality_rank(cdata.min_quality):
                    raise Exception(
                        f"Best available quality ({best_q}) is below minimum ({cdata.min_quality})"
                    )

            logger.info(
                "Starting download: %s (video_id=%s, quality=%s, output=%s, cookies=%s, pot=%s)",
                vdata.title, vdata.video_id, cdata.quality, output_path,
                settings.has_cookies, settings.POT_SERVER_ENABLED,
            )

            stall_timeout = 600  # 10 min with no progress from yt-dlp
            _last_progress_time = time.monotonic()
            try:
                _active_downloads.add(vdata.video_id)
                download_task = asyncio.ensure_future(
                    asyncio.to_thread(
                        self.ytdlp.download_video,
                        video_url,
                        output_path,
                        quality=cdata.quality,
                        codec=cdata.preferred_codec,
                        progress_hook=progress_hook,
                        pp_hook=pp_hook,
                        platform=cdata.platform,
                        subtitles_enabled=subtitles_enabled,
                        chapters_enabled=chapters_enabled,
                        sponsorblock_mode=sponsorblock_mode,
                        temp_dir=temp_dir,
                    )
                )
                while not download_task.done():
                    await asyncio.sleep(10)
                    if time.monotonic() - _last_progress_time > stall_timeout:
                        _cancel_requested = True
                        download_task.cancel()
                        try:
                            await asyncio.wait_for(download_task, timeout=30)
                        except (asyncio.TimeoutError, asyncio.CancelledError, Exception):
                            pass
                        raise asyncio.TimeoutError()
                info = download_task.result()
            except asyncio.TimeoutError:
                logger.warning("Download stalled - no progress for %d minutes", stall_timeout // 60)
                if cdata.platform == "youtube":
                    try:
                        from app.tasks.pot_watchdog import _restart_pot_server
                        await _restart_pot_server()
                    except Exception:
                        pass
                raise Exception(
                    f"Download stalled - no progress for {stall_timeout // 60} minutes. "
                    + ("The PO token server has been restarted. " if cdata.platform == "youtube" else "")
                    + "Retry this download."
                )
            finally:
                _active_downloads.discard(vdata.video_id)

            # Move the finished files from the per-attempt temp dir to the final
            # location (atomic same-filesystem renames), then remove the temp dir.
            # When the download itself fails or stalls we never reach this point and
            # deliberately leave temp_dir alone - the orphaned yt-dlp thread may still
            # be writing there; the sweeper reclaims it.
            final_parent = os.path.dirname(output_path)
            # Never move anything over an existing file until the new video itself is
            # known to be a real, non-empty file: an empty result must not destroy the
            # old copy. yt-dlp has finished, so dropping the temp dir here is safe.
            expected_base = os.path.basename(output_path)
            finished_ok = False
            try:
                exact = os.path.join(temp_dir, expected_base + ".mp4")
                if os.path.exists(exact):
                    # The expected file decides; a leftover format stream must not
                    # stand in for an empty result.
                    finished_ok = os.path.getsize(exact) > 0
                else:
                    for f in os.listdir(temp_dir):
                        if f.startswith(expected_base) and f.endswith(".mp4"):
                            if os.path.getsize(os.path.join(temp_dir, f)) > 0:
                                finished_ok = True
                                break
            except OSError:
                pass
            if not finished_ok:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise Exception(
                    f"Downloaded file is missing or empty (0 bytes): {expected_base}.mp4"
                )
            # A cross-drive move copies the whole file, so keep it off the event loop.
            try:
                await asyncio.to_thread(move_finished_files, temp_dir, final_parent)
            except Exception:
                # yt-dlp has finished, so nothing still writes here. Drop the finished
                # copy now: with TEMP_DIR on another drive, failed moves (destination
                # full or read-only) would otherwise fill that drive until the 6h sweep.
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise
            shutil.rmtree(temp_dir, ignore_errors=True)

            # Verify output file
            mp4_path = output_path + ".mp4"
            if not os.path.exists(mp4_path):
                parent = final_parent
                base = os.path.basename(output_path)
                for f in os.listdir(parent):
                    if f.startswith(base) and f.endswith(".mp4"):
                        mp4_path = os.path.join(parent, f)
                        break

            file_size = os.path.getsize(mp4_path) if os.path.exists(mp4_path) else 0

            # Download integrity check
            if file_size == 0:
                raise Exception(f"Downloaded file is empty (0 bytes): {mp4_path}")

            # Write Plex metadata NFO files
            write_tvshow_nfo(
                channel_name=cdata.channel_name,
                channel_id=cdata.channel_id,
                channel_url=cdata.channel_url,
                description=cdata.description,
                thumbnail_url=cdata.thumbnail_url,
                base_dir=cdata.download_dir,
                platform=cdata.platform,
                folder_name=cdata.folder_name,
            )
            write_episode_nfo(
                channel_name=cdata.channel_name,
                video_title=vdata.title,
                video_id=vdata.video_id,
                description=vdata.description,
                upload_date=vdata.upload_date,
                season=vdata.season,
                episode=vdata.episode,
                duration=vdata.duration,
                thumbnail_url=vdata.thumbnail_url,
                video_file_path=mp4_path,
                platform=cdata.platform,
            )

            # Apply file permissions if configured
            from app.utils.permissions import apply_permissions
            async with async_session() as perm_db:
                perm_settings = await get_settings(
                    perm_db, ["set_permissions", "chmod_folder", "chmod_file", "chown_group"]
                )
            apply_permissions(mp4_path, perm_settings)

            # Create season poster only when the template organizes videos into
            # season folders - a flat template must not spawn empty Season folders.
            from app.services.naming_service import template_uses_season_folder
            if template_uses_season_folder(effective_template):
                from app.services.metadata_service import write_season_poster
                write_season_poster(
                    channel_name=cdata.channel_name,
                    season=vdata.season,
                    thumbnail_url=cdata.thumbnail_url,
                    base_dir=cdata.download_dir,
                    folder_name=cdata.folder_name,
                )

        except Exception as e:
            # ── Phase 3 (error): record failure ──────────────────────────
            # Reclaim the temp dir only if it's empty: a non-empty dir may belong to a
            # stalled orphan thread still writing, so leave that for the 6h sweeper.
            try:
                if os.path.isdir(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
            except OSError:
                pass
            await self._record_failure(video_id, channel_id, queue_id, e, vdata)
            return False

        # ── Phase 3 (success): record completion ─────────────────────────
        try:
            async with async_session() as db:
                video = await db.get(Video, video_id)
                channel = await db.get(Channel, channel_id)
                queue_entry = await db.get(DownloadQueue, queue_id)

                if not video:
                    return False

                # Current path from the DB, not the start-of-download snapshot: a rename
                # or move during the download must not leave an untracked duplicate.
                old_path = video.file_path
                old_in_use_elsewhere = bool(old_path) and bool(await db.scalar(
                    select(func.count(Video.id))
                    .where(Video.file_path == old_path)
                    .where(Video.id != video.id)
                ))
                video.status = "completed"
                video.retention_removed_at = None
                video.file_path = mp4_path
                video.file_size = file_size
                video.downloaded_at = datetime.now(timezone.utc)
                video.quality_downloaded = cdata.quality
                video.retry_count = 0
                video.error_code = None
                video.error_message = None
                video.error_details = None

                if channel:
                    channel.downloaded_count = (
                        await db.scalar(
                            select(func.count(Video.id))
                            .where(Video.channel_id == channel.id)
                            .where(Video.status == "completed")
                        )
                    ) or 0
                    channel.health_status = "healthy"

                if queue_entry:
                    await db.delete(queue_entry)

                db.add(DownloadLog(
                    video_id=video.id,
                    event="completed",
                    message=f"Downloaded successfully: {self._format_bytes(file_size)}",
                ))

                # Track last successful auth time and auto-recover from expired state
                result = await db.execute(
                    select(AppSetting).where(
                        AppSetting.key == "last_successful_auth"
                    )
                )
                db_flags = {s.key: s for s in result.scalars().all()}
                now_str = json.dumps(datetime.now(timezone.utc).isoformat())
                if "last_successful_auth" in db_flags:
                    db_flags["last_successful_auth"].value = now_str
                else:
                    db.add(AppSetting(key="last_successful_auth", value=now_str))
                if await queue_state.get_cookies_expired(db):
                    resumed = await queue_state.resume_if_paused_for_cookies(db)
                    logger.info("Auto-recovery: download succeeded, cookies_expired cleared%s",
                                ", queue resumed" if resumed else "")

                await db.commit()
                # A re-download that landed on a different name replaces the old file
                # only now that the new one is safely recorded.
                if old_path and not old_in_use_elsewhere:
                    try:
                        await asyncio.to_thread(_remove_replaced_file, old_path, mp4_path)
                    except Exception:
                        logger.warning("Could not remove replaced file %s", old_path, exc_info=True)
                logger.info("Downloaded: %s (%s)", vdata.title, self._format_bytes(file_size))

        except Exception as e:
            logger.error("Phase 3 success recording failed for %s: %s", vdata.title, e, exc_info=True)
            # Even if recording failed, ensure the queue entry is cleaned up
            try:
                async with async_session() as db:
                    queue_entry = await db.get(DownloadQueue, queue_id)
                    if queue_entry:
                        await db.delete(queue_entry)
                    video = await db.get(Video, video_id)
                    if video and video.status == "downloading":
                        video.status = "completed"
                    await db.commit()
            except Exception:
                logger.error("Failed to clean up queue entry %d", queue_id, exc_info=True)

        await self.notification.broadcast("download_complete", {
            "video_id": vdata.video_id,
            "title": vdata.title,
            "file_size": self._format_bytes(file_size),
        })

        return True

    async def _record_failure(
        self,
        video_id: int,
        channel_id: int,
        queue_id: int,
        error: Exception,
        vdata: "_VideoData",
    ):
        """Record a download error with diagnostics (opens its own session)."""
        error_str = str(error)

        disk_full_alert = False
        quality_waiting = False
        async with async_session() as db:
            diagnostics = DiagnosticsService(db)
            diagnosis = diagnostics.classify_download_error(error_str)
            code = ErrorCode(diagnosis.code)

            logger.error("Download failed for %s: [%s] %s", vdata.title, code.value, error_str)

            video = await db.get(Video, video_id)
            channel = await db.get(Channel, channel_id)
            queue_entry = await db.get(DownloadQueue, queue_id)

            if not video or not channel:
                return

            video.error_code = code.value
            video.error_message = diagnosis.summary
            video.error_details = json.dumps({
                "explanation": diagnosis.explanation,
                "suggested_fix": diagnosis.suggested_fix,
                "raw_log": diagnosis.raw_log,
                "system_context": diagnosis.system_context,
            })

            db.add(DownloadLog(
                video_id=video.id,
                event="failed",
                error_code=code.value,
                message=diagnosis.summary,
                details=json.dumps({
                    "explanation": diagnosis.explanation,
                    "suggested_fix": diagnosis.suggested_fix,
                    "error": error_str[:2000],
                }),
            ))

            info = ERROR_CATALOG.get(code, ERROR_CATALOG[ErrorCode.UNKNOWN])
            try:
                kept_file = bool(video.file_path) and os.path.getsize(video.file_path) > 0
            except OSError:
                kept_file = False

            if code in (ErrorCode.VIDEO_UNAVAILABLE, ErrorCode.VIDEO_PRIVATE, ErrorCode.VIDEO_REMOVED, ErrorCode.GEO_BLOCKED):
                video.status = "skipped"
                if queue_entry:
                    await db.delete(queue_entry)

            elif code == ErrorCode.LIVESTREAM_SCHEDULED:
                # Auto-mark as livestream so it stops retrying
                video.status = "skipped"
                video.is_livestream = True
                video.monitored = False
                video.episode = 0  # Exclude from episode numbering
                if queue_entry:
                    await db.delete(queue_entry)

            elif code == ErrorCode.DISK_FULL:
                # Pause the queue and keep this download queued; it is not a retry.
                if queue_entry:
                    video.status = "queued"
                    queue_entry.started_at = None
                    queue_entry.progress_percent = 0
                    queue_entry.speed_bps = None
                    queue_entry.eta_seconds = None
                else:
                    video.status = "failed"  # row was removed mid-download
                await queue_state.pause_queue(db, queue_state.REASON_DISK_FULL)
                disk_full_alert = True

            elif code == ErrorCode.QUALITY_TOO_LOW and not kept_file and (
                queue_entry is not None and queue_entry.quality_attempts < len(QUALITY_RETRY_DELAYS)
            ):
                # Fresh uploads often show only low formats at first: wait and look again.
                delay = QUALITY_RETRY_DELAYS[queue_entry.quality_attempts]
                queue_entry.quality_attempts += 1
                queue_entry.retry_after = (
                    datetime.now(timezone.utc) + delay
                ).replace(tzinfo=None)
                queue_entry.started_at = None
                queue_entry.progress_percent = 0
                queue_entry.speed_bps = None
                queue_entry.eta_seconds = None
                video.status = "queued"
                quality_waiting = True

            elif (
                info.retry_strategy != "none"
                and code != ErrorCode.QUALITY_TOO_LOW  # its waits ran out: fail for good
                and video.retry_count < await get_setting(db, "max_retries")
            ):
                video.retry_count += 1
                if not queue_entry:
                    # Row was removed mid-download: nothing to retry, never leave "queued"
                    # without a row. Only an in-flight video changes; a skipped one stays.
                    if video.status == "downloading":
                        video.status = "failed"
                else:
                    video.status = "queued"
                    queue_entry.started_at = None
                    queue_entry.progress_percent = 0
                    queue_entry.speed_bps = None
                    queue_entry.eta_seconds = None
                    queue_entry.priority = max(queue_entry.priority - 1, -10)
                    if LOOKUP_FAILED_MARKER in error_str.lower():
                        queue_entry.retry_after = (
                            datetime.now(timezone.utc) + LOOKUP_RETRY_DELAY
                        ).replace(tzinfo=None)

            else:
                video.status = "failed"
                if queue_entry:
                    await db.delete(queue_entry)

            # A failed re-download or quality upgrade must not strand a video whose
            # old file is still on disk: give it back as completed.
            if kept_file and video.status in ("failed", "skipped"):
                video.status = "completed"
                if code in _VIDEO_GONE_CODES:
                    # The video itself is gone upstream: keep error_code as a marker so
                    # the quality-upgrade task doesn't re-queue it daily. Errors about
                    # the system (cookies, yt-dlp, disk) clear, so later upgrades run.
                    pass
                else:
                    video.error_code = None
                    video.error_message = None
                    video.error_details = None

            # SCAN_FAILED belongs to the scan: a failed download must not replace it, or
            # the outage would look new again (another webhook) and recovery would miss it.
            code_written = channel.last_error_code != ErrorCode.SCAN_FAILED.value
            if code_written:
                channel.last_error_code = code.value
            failed_count = await db.scalar(
                select(func.count(Video.id))
                .where(Video.channel_id == channel.id)
                .where(Video.status == "failed")
            )
            if failed_count and failed_count > 3:
                channel.health_status = "unhealthy"
            elif code_written and code in (ErrorCode.AUTH_EXPIRED, ErrorCode.PO_TOKEN_FAILURE):
                channel.health_status = "warning"

            # Flag cookies as expired and auto-pause queue
            # But only if cookies weren't recently uploaded (avoids loop where
            # an in-flight download with old cookies fails and invalidates fresh ones)
            if code == ErrorCode.AUTH_EXPIRED:
                cookie_age_seconds = 0
                try:
                    if settings.cookies_path.exists():
                        cookie_age_seconds = (datetime.now(timezone.utc).timestamp()
                                              - os.path.getmtime(settings.cookies_path))
                except Exception as e:
                    logger.warning(
                        "Could not read cookies mtime (%s); treating age as unknown, "
                        "cookie-expiry guard may not fire", e,
                    )

                if cookie_age_seconds > 120:  # Only invalidate if cookies are older than 2 minutes
                    from app.utils.cookie_utils import flag_cookies_expired
                    await flag_cookies_expired(db)
                else:
                    logger.info("Skipping cookie invalidation - cookies were uploaded %ds ago", int(cookie_age_seconds))

            await db.commit()

        # Broadcast failure + cookie expiration events
        if code == ErrorCode.AUTH_EXPIRED:
            await self.notification.broadcast("cookies_expired", {
                "message": "YouTube cookies have expired. Queue paused. Re-export and upload fresh cookies.",
            })

        if disk_full_alert:
            await self.notification.broadcast("critical_alert", {
                "type": "disk_full",
                "message": "Disk is full. Downloads are paused. Free up space, then press Resume.",
            })

        if quality_waiting:
            logger.info("Quality below minimum for %s; waiting before the next look", vdata.title)
            return

        if kept_file:
            logger.info("Re-download/upgrade failed for %s but the existing file was kept: %s",
                        vdata.title, diagnosis.summary)
            return

        await self.notification.broadcast("download_failed", {
            "video_id": vdata.video_id,
            "title": vdata.title,
            "error_code": code.value,
            "summary": diagnosis.summary,
            "suggested_fix": diagnosis.suggested_fix,
        })

    @staticmethod
    def _format_speed(speed: float) -> str:
        if not speed:
            return "0 B/s"
        for unit in ["B/s", "KB/s", "MB/s", "GB/s"]:
            if speed < 1024:
                return f"{speed:.1f} {unit}"
            speed /= 1024
        return f"{speed:.1f} TB/s"

    @staticmethod
    def _format_bytes(size: int) -> str:
        from app.services.storage_service import format_bytes
        return format_bytes(size)

    @staticmethod
    def _format_eta(seconds: float) -> str:
        if not seconds:
            return "??:??"
        m, s = divmod(int(seconds), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"


# ── Plain data containers (no ORM / session dependency) ──────────────────

class _VideoData:
    __slots__ = (
        "pk", "video_id", "title", "description", "upload_date",
        "season", "episode", "duration", "thumbnail_url",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class _ChannelData:
    __slots__ = (
        "pk", "channel_id", "channel_name", "channel_url", "platform",
        "description", "thumbnail_url", "quality", "min_quality",
        "preferred_codec", "naming_template", "download_dir", "folder_name",
    )

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
