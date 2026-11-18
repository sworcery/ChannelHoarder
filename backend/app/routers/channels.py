import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.utils.file_utils import (
    channel_folder_name, delete_video_files, escape_like, has_kept_file, move_video_files,
)
from app.models import Channel, DownloadLog, Video, DownloadQueue
from pydantic import BaseModel, Field

from app.schemas import (
    ChannelCreate, ChannelUpdate, ChannelResponse, VideoResponse,
    ImportScanRequest, ImportConfirmRequest, RepairDatesConfirm,
)
from app.services.channel_service import (
    ChannelService, SHORTS_MAX_DURATION, _moving_channels, _scanning_channels, other_recorded,
)
from app.services.import_service import scan_folder_for_imports, import_matched_files
from app.utils.renumber import renumber_channel_episodes as _renumber_channel_episodes

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[ChannelResponse])
async def list_channels(
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Channel).where(Channel.channel_id != "__standalone__").order_by(Channel.channel_name)
    if search:
        query = query.where(Channel.channel_name.ilike(f"%{escape_like(search)}%"))
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    channels = result.scalars().all()

    if not channels:
        return []

    # Compute live counts from Video table (avoids stale cached values)
    channel_ids = [c.id for c in channels]
    counts_result = await db.execute(
        select(
            Video.channel_id,
            func.count(Video.id).filter(Video.status != "skipped", Video.monitored == True).label("total"),
            func.count(Video.id).filter(Video.status == "completed").label("downloaded"),
        )
        .where(Video.channel_id.in_(channel_ids))
        .group_by(Video.channel_id)
    )
    counts_map = {row[0]: (row[1], row[2]) for row in counts_result.all()}

    # Override stale cached values with live counts
    for ch in channels:
        total, downloaded = counts_map.get(ch.id, (0, 0))
        ch.total_videos = total
        ch.downloaded_count = downloaded

    return channels


@router.post("/download-all-missing", status_code=202)
async def download_all_missing(db: AsyncSession = Depends(get_db)):
    """Queue all pending/failed videos across all channels for download."""
    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.status.in_(["pending", "failed"]))
        .where(Video.monitored == True)
        .where(Video.retention_removed_at.is_(None))
        .where(Video.id.notin_(subquery))
    )
    videos = result.scalars().all()

    queued = 0
    for video in videos:
        video.status = "queued"
        db.add(DownloadQueue(video_id=video.id))
        queued += 1

    await db.commit()
    return {"message": f"Queued {queued} videos for download across all channels.", "queued": queued}


@router.post("/", response_model=ChannelResponse, status_code=201)
async def add_channel(
    body: ChannelCreate,
    db: AsyncSession = Depends(get_db),
):
    from app.utils.file_utils import validate_url_scheme, validate_download_path
    try:
        validate_url_scheme(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if body.download_dir:
        try:
            validate_download_path(body.download_dir, settings.allowed_download_roots)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    service = ChannelService(db)
    try:
        channel = await service.add_channel(body)
        return channel
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to add channel: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to add channel. Check logs for details.")


@router.get("/{channel_id}/retention-preview")
async def retention_preview(
    channel_id: int,
    days: int = Query(..., ge=1, le=36500),
    db: AsyncSession = Depends(get_db),
):
    """Count and size of downloaded videos a retention of `days` would delete now."""
    from app.tasks.retention import expired_videos_filter

    exists = await db.scalar(select(Channel.id).where(Channel.id == channel_id))
    if exists is None:
        raise HTTPException(status_code=404, detail="Channel not found")
    row = (await db.execute(
        select(func.count(Video.id), func.coalesce(func.sum(Video.file_size), 0))
        .where(*expired_videos_filter(channel_id, days))
    )).one()
    return {"count": int(row[0]), "total_bytes": int(row[1])}


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Compute live counts from Video table
    counts = await db.execute(
        select(
            func.count(Video.id).filter(Video.status != "skipped", Video.monitored == True),
            func.count(Video.id).filter(Video.status == "completed"),
        ).where(Video.channel_id == channel.id)
    )
    row = counts.one()
    channel.total_videos = row[0] or 0
    channel.downloaded_count = row[1] or 0

    return channel


@router.put("/{channel_id}", response_model=ChannelResponse)
async def update_channel(
    channel_id: int,
    body: ChannelUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    update_data = body.model_dump(exclude_unset=True)

    if "download_dir" in update_data and update_data["download_dir"]:
        from app.utils.file_utils import validate_download_path
        try:
            validate_download_path(update_data["download_dir"], settings.allowed_download_roots)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    old_dir = channel.download_dir
    for field, value in update_data.items():
        setattr(channel, field, value)

    # A new download root (without moving files) can hold a same-named channel's
    # folder; give this channel its own there so future downloads don't mix.
    if "download_dir" in update_data and channel.download_dir != old_dir:
        from app.services.channel_service import _folder_key, pick_free_folder, taken_folder_keys
        from app.utils.file_utils import channel_folder_name
        taken = await taken_folder_keys(db, exclude_id=channel.id)
        if _folder_key(channel.download_dir, channel_folder_name(channel)) in taken:
            channel.folder_name = pick_free_folder(
                taken, channel.channel_name, channel.platform, channel.download_dir)

    await db.commit()
    await db.refresh(channel)
    return channel


@router.delete("/{channel_id}", status_code=204)
async def delete_channel(
    channel_id: int,
    delete_files: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    if delete_files:
        service = ChannelService(db)
        await service.delete_channel_files(channel)

    # Explicitly delete child records (SQLite may not cascade without PRAGMA foreign_keys)
    from sqlalchemy import delete as sa_delete
    video_ids = select(Video.id).where(Video.channel_id == channel.id)
    await db.execute(sa_delete(DownloadLog).where(DownloadLog.video_id.in_(video_ids)))
    await db.execute(sa_delete(DownloadQueue).where(DownloadQueue.video_id.in_(video_ids)))
    await db.execute(sa_delete(Video).where(Video.channel_id == channel.id))
    await db.delete(channel)
    await db.commit()


@router.get("/{channel_id}/videos")
async def list_channel_videos(
    channel_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=50000),
    status: Optional[str] = None,
    monitored: Optional[bool] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Video).where(Video.channel_id == channel_id).order_by(Video.season.desc(), Video.episode.desc())
    if status:
        query = query.where(Video.status == status)
    if monitored is not None:
        query = query.where(Video.monitored == monitored)
    if search:
        query = query.where(Video.title.ilike(f"%{escape_like(search)}%"))

    # Total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    result = await db.execute(query.offset(skip).limit(limit))
    videos = result.scalars().all()

    return {
        "items": [VideoResponse.model_validate(v) for v in videos],
        "total": total,
        "skip": skip,
        "limit": limit,
    }


@router.post("/{channel_id}/refresh-metadata", status_code=200)
async def refresh_metadata(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Re-fetch channel metadata (thumbnail, banner, description) from the platform."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    service = ChannelService(db)
    try:
        updated = await service.refresh_channel_metadata(channel)
        return {"message": f"Metadata refreshed for {updated.channel_name}"}
    except Exception as e:
        logger.error("Metadata refresh failed for %s: %s", channel.channel_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Metadata refresh failed: {e}")


@router.post("/{channel_id}/renumber/preview", status_code=200)
async def renumber_preview(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Preview what episode renumbering would change without applying anything."""
    from app.services.naming_service import build_output_path

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(Video.upload_date.asc(), Video.id.asc())
    )
    videos = result.scalars().all()

    from app.services.naming_service import resolve_naming_template
    effective_template = await resolve_naming_template(db, channel.naming_template)

    season_counts: dict[int, int] = {}
    changes = []

    for video in videos:
        # Shorts and livestreams are excluded from episode numbering
        if video.is_short or video.is_livestream:
            if video.episode != 0:
                changes.append({
                    "video_id": video.id,
                    "title": video.title,
                    "upload_date": str(video.upload_date),
                    "old_episode": f"S{video.season}E{video.episode:03d}",
                    "new_episode": "(short - excluded)",
                    "has_file": False,
                    "old_path": None,
                    "new_path": None,
                })
            continue

        season = video.upload_date.year
        season_counts.setdefault(season, 0)
        season_counts[season] += 1
        new_episode = season_counts[season]

        if video.season != season or video.episode != new_episode:
            old_label = f"S{video.season}E{video.episode:03d}"
            new_label = f"S{season}E{new_episode:03d}"

            # Figure out new file path
            new_file_path = None
            has_file = False
            if video.file_path and os.path.exists(video.file_path):
                has_file = True
                new_file_path = build_output_path(
                    channel_name=channel.channel_name,
                    video_title=video.title,
                    video_id=video.video_id,
                    upload_date=video.upload_date,
                    season=season,
                    episode=new_episode,
                    naming_template=effective_template,
                    base_dir=channel.download_dir,
                    folder_name=channel_folder_name(channel),
                # Match what the renumber itself will do, so the preview is accurate
                ) + (os.path.splitext(video.file_path)[1] or ".mp4")

            changes.append({
                "video_id": video.id,
                "title": video.title,
                "upload_date": str(video.upload_date),
                "old_episode": old_label,
                "new_episode": new_label,
                "has_file": has_file,
                "old_path": os.path.basename(video.file_path) if video.file_path else None,
                "new_path": os.path.basename(new_file_path) if new_file_path else None,
            })

    return {
        "channel_name": channel.channel_name,
        "total_videos": len(videos),
        "changes": changes,
        "total_changes": len(changes),
    }


@router.post("/{channel_id}/renumber/confirm", status_code=200)
async def renumber_confirm(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Apply episode renumbering and rename files on disk."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(Video.upload_date.asc(), Video.id.asc())
    )
    videos = result.scalars().all()

    # Count how many need updating before renumber (excluding shorts)
    season_counts_pre: dict[int, int] = {}
    updated = 0
    for video in videos:
        if video.is_short or video.is_livestream:
            if video.episode != 0:
                updated += 1
            continue
        season = video.upload_date.year
        season_counts_pre.setdefault(season, 0)
        season_counts_pre[season] += 1
        if video.season != season or video.episode != season_counts_pre[season]:
            updated += 1

    from app.services.naming_service import resolve_naming_template
    template = await resolve_naming_template(db, channel.naming_template)
    protected, _ = await other_recorded(db, channel.id)
    renamed = await asyncio.to_thread(
        _renumber_channel_episodes, videos, channel, template, protected_keys=protected
    )

    await db.commit()
    return {
        "message": f"Re-numbered {updated} episodes, renamed {renamed} files on disk",
        "updated": updated,
        "renamed": renamed,
    }


@router.post("/{channel_id}/repair-dates/preview", status_code=200)
async def repair_dates_preview(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Preview upload-date repairs for videos stamped with a fallback date, without applying anything."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    service = ChannelService(db)
    changes, checked, stopped_early, capped = await service.resolve_fallback_dates(channel)

    if stopped_early:
        message = "Stopped after repeated metadata fetch failures. Upload fresh cookies.txt and run Repair Dates again for the rest."
    elif capped:
        message = "Checked the first 250 videos with a suspect date. Run Repair Dates again for the rest."
    else:
        message = None

    return {
        "channel_name": channel.channel_name,
        "checked": checked,
        "changes": changes,
        "total_changes": len(changes),
        "stopped_early": stopped_early or capped,
        "message": message,
    }


@router.post("/{channel_id}/repair-dates/confirm", status_code=200)
async def repair_dates_confirm(
    channel_id: int,
    body: RepairDatesConfirm,
    db: AsyncSession = Depends(get_db),
):
    """Apply upload-date repairs, renumber episodes, and rename files on disk."""
    from datetime import date

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    if channel_id in _scanning_channels:
        raise HTTPException(status_code=409, detail="A scan is running for this channel. Try again when it finishes.")

    _scanning_channels.add(channel_id)
    try:
        # A duplicate video_id in the payload is applied once.
        seen_ids: set[int] = set()
        deduped_changes = []
        for item in body.changes:
            if item.video_id in seen_ids:
                continue
            seen_ids.add(item.video_id)
            deduped_changes.append(item)

        min_year = 1990
        max_year = date.today().year + 1

        dates_corrected = 0
        skipped = 0
        corrected_ids: set[int] = set()
        for item in deduped_changes:
            if not (min_year <= item.new_date.year <= max_year):
                skipped += 1
                continue

            video = await db.get(Video, item.video_id)
            if (
                video is not None
                and video.channel_id == channel_id
                and video.video_id == item.source_id
                and video.discovered_at is not None
                and video.discovered_at.date() == video.upload_date
                and item.new_date != video.upload_date
            ):
                video.upload_date = item.new_date
                if video.is_short or video.is_livestream:
                    # Shorts/livestreams never get an episode number, so the
                    # renumber pass only rebuilds their file/NFO; set season here.
                    video.season = item.new_date.year
                corrected_ids.add(video.id)
                dates_corrected += 1
            else:
                skipped += 1

        await db.flush()

        renamed = 0
        if dates_corrected > 0:
            result = await db.execute(
                select(Video)
                .where(Video.channel_id == channel_id)
                .order_by(Video.upload_date.asc(), Video.id.asc())
            )
            videos = result.scalars().all()

            from app.services.naming_service import resolve_naming_template
            template = await resolve_naming_template(db, channel.naming_template)
            try:
                protected, _ = await other_recorded(db, channel.id)
                renamed = await asyncio.to_thread(
                    _renumber_channel_episodes, videos, channel, template,
                    force_ids=corrected_ids, protected_keys=protected,
                )
            except Exception as e:
                logger.error("Repair Dates renumber failed for %s: %s", channel.channel_name, e, exc_info=True)
                await db.commit()
                raise HTTPException(
                    status_code=500,
                    detail="Dates were corrected but renaming stopped partway. Check the log, then run Fix Episode Numbers to finish.",
                )

        if dates_corrected == 0:
            message = f"Corrected {dates_corrected} upload dates"
        else:
            message = f"Corrected {dates_corrected} upload dates, renumbered episodes, renamed {renamed} files on disk"
        if skipped > 0:
            message += f", {skipped} item(s) skipped because they no longer applied"

        await db.commit()
        return {
            "message": message,
            "dates_corrected": dates_corrected,
            "skipped": skipped,
            "renamed": renamed,
        }
    finally:
        _scanning_channels.discard(channel_id)


@router.post("/{channel_id}/scan", status_code=202)
async def trigger_scan(channel_id: int, db: AsyncSession = Depends(get_db)):
    import json
    from datetime import datetime, timedelta, timezone
    from app.models import AppSetting

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Rate limit: prevent spam clicking "Scan Now"
    cooldown_minutes = 5
    try:
        cd_result = await db.execute(
            select(AppSetting).where(AppSetting.key == "manual_scan_cooldown_minutes")
        )
        cd_setting = cd_result.scalar_one_or_none()
        if cd_setting:
            cooldown_minutes = int(json.loads(cd_setting.value))
    except Exception:
        pass

    if channel.last_scanned_at and cooldown_minutes > 0:
        # Exempt channels that haven't found any videos yet - a freshly added
        # channel that came up empty should be re-scannable immediately rather
        # than locked out by the anti-spam cooldown.
        video_count = await db.scalar(
            select(func.count(Video.id)).where(Video.channel_id == channel.id)
        )
        if video_count:
            last_ts = channel.last_scanned_at
            if last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            elapsed = datetime.now(timezone.utc) - last_ts
            cooldown = timedelta(minutes=cooldown_minutes)
            if elapsed < cooldown:
                remaining_sec = int((cooldown - elapsed).total_seconds())
                raise HTTPException(
                    status_code=429,
                    detail=f"This channel was scanned recently. Try again in {remaining_sec} seconds.",
                )

    service = ChannelService(db)
    try:
        new_count = await service.scan_channel(channel)
        return {"message": f"Scan complete. Found {new_count} new videos.", "new_videos": new_count}
    except Exception as e:
        logger.error("Scan failed for channel %s: %s", channel.channel_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scan failed: {e}")


@router.post("/{channel_id}/download-all", status_code=202)
async def queue_all_videos(channel_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Find all monitored pending videos not already in queue
    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.status.in_(["pending", "failed"]))
        .where(Video.monitored == True)
        .where(Video.retention_removed_at.is_(None))
        .where(Video.id.notin_(subquery))
    )
    videos = result.scalars().all()

    queued = 0
    for video in videos:
        video.status = "queued"
        db.add(DownloadQueue(video_id=video.id))
        queued += 1

    await db.commit()
    return {"message": f"Queued {queued} videos for download.", "queued": queued}


class BulkVideoAction(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=50000)


@router.post("/{channel_id}/videos/bulk-queue")
async def bulk_queue_videos(
    channel_id: int,
    body: BulkVideoAction,
    db: AsyncSession = Depends(get_db),
):
    """Queue selected videos for download."""
    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
        .where(Video.status.in_(["pending", "pending_review", "failed", "skipped"]))
        .where(Video.id.notin_(subquery))
    )
    videos = result.scalars().all()

    queued = 0
    for video in videos:
        video.status = "queued"
        video.retention_removed_at = None  # picked by hand, so it's wanted again
        db.add(DownloadQueue(video_id=video.id))
        queued += 1

    await db.commit()
    return {"message": f"Queued {queued} videos for download", "queued": queued}


@router.post("/{channel_id}/videos/bulk-skip")
async def bulk_skip_videos(
    channel_id: int,
    body: BulkVideoAction,
    db: AsyncSession = Depends(get_db),
):
    """Mark selected videos as skipped."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
        .where(Video.status.in_(["pending", "failed", "queued"]))
    )
    videos = result.scalars().all()

    # Batch-load queue entries to avoid N+1
    video_ids = [v.id for v in videos]
    queue_result = await db.execute(
        select(DownloadQueue).where(DownloadQueue.video_id.in_(video_ids))
    )
    queue_entries = {q.video_id: q for q in queue_result.scalars().all()}

    skipped = 0
    for video in videos:
        queue_entry = queue_entries.get(video.id)
        if queue_entry:
            await db.delete(queue_entry)
        # Skipping a queued re-download or upgrade cancels it; the video keeps its file
        # and stays downloaded instead of being hidden as skipped.
        video.status = "completed" if has_kept_file(video) else "skipped"
        skipped += 1

    await db.commit()
    return {"message": f"Skipped {skipped} videos", "skipped": skipped}


@router.post("/{channel_id}/videos/bulk-unskip")
async def bulk_unskip_videos(
    channel_id: int,
    body: BulkVideoAction,
    db: AsyncSession = Depends(get_db),
):
    """Unskip selected videos back to pending."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
        .where(Video.status == "skipped")
    )
    videos = result.scalars().all()

    unskipped = 0
    for video in videos:
        video.status = "pending"
        video.retention_removed_at = None
        unskipped += 1

    await db.commit()
    return {"message": f"Unskipped {unskipped} videos", "unskipped": unskipped}


class BulkDeleteRequest(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=50000)
    delete_files: bool = False


@router.post("/{channel_id}/videos/bulk-delete")
async def bulk_delete_videos(
    channel_id: int,
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete selected videos. Optionally removes files from disk."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
    )
    videos = result.scalars().all()

    # Remove queue entries
    video_ids = [v.id for v in videos]
    queue_result = await db.execute(
        select(DownloadQueue).where(DownloadQueue.video_id.in_(video_ids))
    )
    for q in queue_result.scalars().all():
        await db.delete(q)

    deleted = 0
    with_files = 0
    files_removed = 0
    for video in videos:
        if body.delete_files and video.file_path:
            files_removed += await asyncio.to_thread(delete_video_files, video.file_path)
            with_files += 1

        video.status = "skipped"
        video.file_path = None
        video.file_size = None
        deleted += 1

    await db.commit()
    if body.delete_files and with_files:
        msg = (f"Deleted the files of {with_files} videos ({files_removed} files removed); "
               f"{deleted} videos marked skipped")
    else:
        msg = f"Marked {deleted} videos skipped"
    return {"message": msg, "deleted": deleted, "files_removed": files_removed}


@router.delete("/{channel_id}/videos/{video_id}")
async def delete_video(
    channel_id: int,
    video_id: int,
    delete_files: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single video. Optionally removes files from disk."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    files_removed = False
    if delete_files and video.file_path:
        files_removed = await asyncio.to_thread(delete_video_files, video.file_path) > 0

    # Remove from queue if present
    queue_result = await db.execute(
        select(DownloadQueue).where(DownloadQueue.video_id == video.id)
    )
    queue_entry = queue_result.scalar_one_or_none()
    if queue_entry:
        await db.delete(queue_entry)

    video.status = "skipped"
    video.file_path = None
    video.file_size = None
    await db.commit()

    return {"message": f"Video '{video.title}' deleted", "files_removed": files_removed}


@router.post("/{channel_id}/videos/{video_id}/redownload")
async def redownload_video(
    channel_id: int,
    video_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Re-queue video for download. The existing file is kept until the new one lands."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.error_code = None
    video.error_message = None
    video.error_details = None
    video.retry_count = 0
    video.status = "queued"
    video.retention_removed_at = None

    # Add to queue if not already there
    queue_check = await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == video.id))
    if not queue_check.scalar_one_or_none():
        db.add(DownloadQueue(video_id=video.id))

    await db.commit()
    return {"message": f"Re-queued '{video.title}' for download"}


@router.delete("/{channel_id}/videos/{video_id}/file")
async def delete_video_file(
    channel_id: int,
    video_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete file from disk but keep the video record as pending."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    files_removed = False
    if video.file_path:
        files_removed = await asyncio.to_thread(delete_video_files, video.file_path) > 0

    video.file_path = None
    video.file_size = None
    video.quality_downloaded = None
    video.status = "pending"
    await db.commit()

    return {"message": f"Deleted files for '{video.title}'", "files_removed": files_removed}


@router.post("/{channel_id}/videos/{video_id}/rename")
async def rename_video_file(
    channel_id: int,
    video_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Rename video file on disk based on current naming template."""
    from app.services.naming_service import build_output_path

    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.file_path or not await asyncio.to_thread(os.path.exists, video.file_path):
        raise HTTPException(status_code=400, detail="No file on disk to rename")

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    from app.services.naming_service import resolve_naming_template
    new_path = build_output_path(
        channel_name=channel.channel_name,
        video_title=video.title,
        video_id=video.video_id,
        upload_date=video.upload_date,
        season=video.season,
        episode=video.episode,
        naming_template=await resolve_naming_template(db, channel.naming_template),
        base_dir=channel.download_dir,
        folder_name=channel_folder_name(channel),
    # Preserve the real container - imported files aren't always .mp4
    ) + (os.path.splitext(video.file_path)[1] or ".mp4")

    old_path = video.file_path
    if old_path == new_path:
        return {"message": "File already has the correct name", "renamed": False}

    await asyncio.to_thread(move_video_files, old_path, new_path)
    video.file_path = new_path

    await db.commit()
    return {"message": f"Renamed '{video.title}'", "renamed": True, "new_path": new_path}


class ShortRequest(BaseModel):
    is_short: bool


@router.patch("/{channel_id}/videos/{video_id}/short")
async def toggle_video_short(
    channel_id: int,
    video_id: int,
    body: ShortRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manually mark or unmark a video as a short."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.is_short = body.is_short
    if body.is_short:
        video.episode = 0  # Shorts are excluded from episode numbering
    await db.commit()
    return {"message": f"{'Marked' if body.is_short else 'Unmarked'} '{video.title}' as short", "is_short": body.is_short}


class LivestreamRequest(BaseModel):
    is_livestream: bool


@router.patch("/{channel_id}/videos/{video_id}/livestream")
async def toggle_video_livestream(
    channel_id: int,
    video_id: int,
    body: LivestreamRequest,
    db: AsyncSession = Depends(get_db),
):
    """Manually mark or unmark a video as a livestream."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.is_livestream = body.is_livestream
    if body.is_livestream:
        video.episode = 0  # Livestreams are excluded from episode numbering
    await db.commit()
    return {"message": f"{'Marked' if body.is_livestream else 'Unmarked'} '{video.title}' as livestream", "is_livestream": body.is_livestream}


class MonitorRequest(BaseModel):
    monitored: bool


class BulkMonitorRequest(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=50000)
    monitored: bool


@router.patch("/{channel_id}/videos/{video_id}/monitored")
async def toggle_video_monitored(
    channel_id: int,
    video_id: int,
    body: MonitorRequest,
    db: AsyncSession = Depends(get_db),
):
    """Toggle monitored state for a single video."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    video.monitored = body.monitored
    if body.monitored:
        video.retention_removed_at = None
    await db.commit()
    return {"message": f"{'Monitoring' if body.monitored else 'Unmonitoring'} {video.title}", "monitored": body.monitored}


@router.post("/{channel_id}/videos/bulk-monitor")
async def bulk_monitor_videos(
    channel_id: int,
    body: BulkMonitorRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set monitored state for multiple videos."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
    )
    videos = result.scalars().all()
    count = 0
    for video in videos:
        video.monitored = body.monitored
        if body.monitored:
            video.retention_removed_at = None
        count += 1
    await db.commit()
    return {"message": f"{'Monitored' if body.monitored else 'Unmonitored'} {count} videos", "count": count}


class BulkReclassifyRequest(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=50000)
    set_short: Optional[bool] = None
    set_livestream: Optional[bool] = None


@router.post("/{channel_id}/videos/bulk-reclassify")
async def bulk_reclassify_videos(
    channel_id: int,
    body: BulkReclassifyRequest,
    db: AsyncSession = Depends(get_db),
):
    """Bulk update short/livestream classification for selected videos."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
    )
    videos = result.scalars().all()
    count = 0
    for video in videos:
        if body.set_short is not None:
            video.is_short = body.set_short
            if body.set_short:
                video.episode = 0
        if body.set_livestream is not None:
            video.is_livestream = body.set_livestream
            if body.set_livestream:
                video.episode = 0
        count += 1
    await db.commit()

    label_parts = []
    if body.set_short is True:
        label_parts.append("shorts")
    if body.set_livestream is True:
        label_parts.append("livestreams")
    if body.set_short is False and body.set_livestream is False:
        label_parts.append("regular videos")
    label = " & ".join(label_parts) if label_parts else "reclassified"

    return {"message": f"Marked {count} videos as {label}", "count": count}


class BulkRenameRequest(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=50000)


@router.post("/{channel_id}/videos/bulk-rename")
async def bulk_rename_videos(
    channel_id: int,
    body: BulkRenameRequest,
    db: AsyncSession = Depends(get_db),
):
    """Rename selected videos' files on disk to match the current naming template."""
    from app.services.naming_service import build_output_path

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.id.in_(body.video_ids))
    )
    videos = result.scalars().all()

    from app.services.naming_service import resolve_naming_template
    effective_template = await resolve_naming_template(db, channel.naming_template)

    renamed = 0
    skipped = 0
    errors = 0
    for video in videos:
        if not video.file_path or not await asyncio.to_thread(os.path.exists, video.file_path):
            skipped += 1
            continue
        new_path = build_output_path(
            channel_name=channel.channel_name,
            video_title=video.title,
            video_id=video.video_id,
            upload_date=video.upload_date,
            season=video.season,
            episode=video.episode,
            naming_template=effective_template,
            base_dir=channel.download_dir,
            folder_name=channel_folder_name(channel),
        # Preserve the real container - imported files aren't always .mp4
        ) + (os.path.splitext(video.file_path)[1] or ".mp4")
        if new_path == video.file_path:
            skipped += 1
            continue
        try:
            await asyncio.to_thread(move_video_files, video.file_path, new_path)
            video.file_path = new_path
            renamed += 1
        except Exception as e:
            logger.error("Bulk rename failed for video %s: %s", video.id, e)
            errors += 1
    await db.commit()

    msg = f"Renamed {renamed} file(s)"
    if skipped:
        msg += f", {skipped} already correct or no file"
    if errors:
        msg += f", {errors} failed"
    return {"message": msg, "renamed": renamed, "skipped": skipped, "errors": errors}


@router.post("/{channel_id}/monitor-all")
async def monitor_all_videos(
    channel_id: int,
    body: MonitorRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set monitored state for all videos in a channel."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        update(Video)
        .where(Video.channel_id == channel_id, Video.retention_removed_at.is_(None))
        .values(monitored=body.monitored)
    )
    count = result.rowcount
    await db.commit()
    return {"message": f"{'Monitored' if body.monitored else 'Unmonitored'} {count} videos", "count": count}


@router.post("/{channel_id}/seasons/{season}/monitor")
async def monitor_season(
    channel_id: int,
    season: int,
    body: MonitorRequest,
    db: AsyncSession = Depends(get_db),
):
    """Set monitored state for all videos in a season."""
    result = await db.execute(
        update(Video)
        .where(
            Video.channel_id == channel_id,
            Video.season == season,
            Video.retention_removed_at.is_(None),
        )
        .values(monitored=body.monitored)
    )
    count = result.rowcount
    await db.commit()
    return {"message": f"{'Monitored' if body.monitored else 'Unmonitored'} {count} videos in Season {season}", "count": count}


@router.post("/{channel_id}/seasons/{season}/download-missing")
async def download_missing_season(
    channel_id: int,
    season: int,
    db: AsyncSession = Depends(get_db),
):
    """Queue all monitored pending/failed videos in a season."""
    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.season == season)
        .where(Video.status.in_(["pending", "failed"]))
        .where(Video.monitored == True)
        .where(Video.retention_removed_at.is_(None))
        .where(Video.id.notin_(subquery))
    )
    videos = result.scalars().all()
    queued = 0
    for video in videos:
        video.status = "queued"
        db.add(DownloadQueue(video_id=video.id))
        queued += 1
    await db.commit()
    return {"message": f"Queued {queued} videos from Season {season}", "queued": queued}


@router.post("/{channel_id}/upgrade-quality")
async def upgrade_quality(channel_id: int, db: AsyncSession = Depends(get_db)):
    """Re-queue completed videos where quality is below the channel's target quality."""
    from app.utils.quality_utils import quality_met

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Use channel.quality_cutoff if explicitly set, else fall back to channel.quality.
    # "best" means no cutoff (any quality acceptable).
    cutoff = channel.quality_cutoff or channel.quality
    if not cutoff or cutoff == "best":
        return {"message": "No quality cutoff (channel set to 'best')", "queued": 0}

    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.status == "completed")
        .where(Video.monitored == True)
        .where(Video.id.notin_(subquery))
    )
    videos = result.scalars().all()

    queued = 0
    for video in videos:
        if not quality_met(video.quality_downloaded, cutoff):
            video.status = "queued"
            db.add(DownloadQueue(video_id=video.id))
            queued += 1

    await db.commit()
    return {"message": f"Queued {queued} videos for quality upgrade", "queued": queued}


@router.post("/{channel_id}/download-subtitles", status_code=202)
async def download_channel_subtitles(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download subtitles for all completed videos in a channel (runs in background)."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Count eligible videos
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.status == "completed")
        .where(Video.file_path.isnot(None))
    )
    videos = result.scalars().all()
    eligible = sum(1 for v in videos if v.file_path and not _has_subtitles(v.file_path))

    if eligible == 0:
        return {"message": "All videos already have subtitles or no completed videos found", "downloaded": 0}

    asyncio.create_task(_download_channel_subtitles_task(channel_id, channel.channel_name))

    return {"message": f"Downloading subtitles for {eligible} videos in '{channel.channel_name}'"}


@router.post("/{channel_id}/videos/{video_id}/download-subtitles")
async def download_video_subtitles(
    channel_id: int,
    video_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Download subtitles for a single video."""
    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.file_path:
        raise HTTPException(status_code=400, detail="No file on disk for this video")

    if _has_subtitles(video.file_path):
        return {"message": "Subtitles already exist for this video", "downloaded": False}

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    platform = channel.platform if channel else "youtube"

    from app.utils.platform_utils import build_video_url
    video_url = video.source_url or build_video_url(platform, video.video_id)
    output_base = os.path.splitext(video.file_path)[0]

    from app.services.ytdlp_service import YtdlpService
    ytdlp = YtdlpService()
    success = await asyncio.to_thread(
        ytdlp.download_subtitles_only, video_url, output_base, platform
    )

    if success:
        return {"message": f"Subtitles downloaded for '{video.title}'", "downloaded": True}
    return {"message": f"No subtitles available for '{video.title}'", "downloaded": False}


def _has_subtitles(file_path: str) -> bool:
    """Check if subtitle files already exist next to a video file."""
    if not file_path:
        return False
    base = os.path.splitext(file_path)[0]
    for ext in [".en.vtt", ".en.srt", ".en.ass"]:
        if os.path.exists(base + ext):
            return True
    return False


async def _download_channel_subtitles_task(channel_id: int, channel_name: str):
    """Background task to download subtitles for all completed videos in a channel."""
    from app.database import async_session
    from app.utils.platform_utils import build_video_url
    from app.services.ytdlp_service import YtdlpService
    from app.services.notification_service import NotificationService

    # Short-lived session: just snapshot the data we need
    async with async_session() as db:
        result = await db.execute(select(Channel).where(Channel.id == channel_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return
        platform = channel.platform or "youtube"

        result = await db.execute(
            select(Video.video_id, Video.file_path, Video.source_url)
            .where(Video.channel_id == channel_id)
            .where(Video.status == "completed")
            .where(Video.file_path.isnot(None))
        )
        video_rows = result.all()

    ytdlp = YtdlpService()
    downloaded = 0
    skipped = 0
    failed = 0

    for vid_id, file_path, source_url in video_rows:
        if not file_path or not os.path.exists(file_path) or _has_subtitles(file_path):
            skipped += 1
            continue

        video_url = source_url or build_video_url(platform, vid_id)
        output_base = os.path.splitext(file_path)[0]

        success = await asyncio.to_thread(
            ytdlp.download_subtitles_only, video_url, output_base, platform
        )
        if success:
            downloaded += 1
        else:
            failed += 1

    await NotificationService.broadcast("subtitles_complete", {
        "channel_name": channel_name,
        "message": f"Subtitles for '{channel_name}': {downloaded} downloaded, {skipped} skipped, {failed} failed",
    })
    logger.info("Subtitle download complete for '%s': %d downloaded, %d skipped, %d failed",
                 channel_name, downloaded, skipped, failed)


class MoveFilesRequest(BaseModel):
    new_download_dir: str = Field(..., min_length=1)


async def _get_move_preview(db: AsyncSession, channel_id: int, new_dir: str) -> dict:
    """Compute move preview for a single channel."""

    channel = await db.get(Channel, channel_id)
    if not channel:
        return {"error": "Channel not found"}

    old_dir = channel.download_dir or settings.DOWNLOAD_DIR
    safe_name = channel_folder_name(channel)
    old_channel_dir = os.path.join(old_dir, safe_name)
    new_channel_dir = os.path.join(new_dir, safe_name)
    same_path = os.path.realpath(old_channel_dir) == os.path.realpath(new_channel_dir)

    result = await db.execute(
        select(Video).where(Video.channel_id == channel_id, Video.file_path.isnot(None))
    )
    videos = result.scalars().all()

    file_count = 0
    total_size = 0
    missing_count = 0
    for video in videos:
        if video.file_path:
            if os.path.exists(video.file_path):
                file_count += 1
                total_size += video.file_size or 0
            else:
                missing_count += 1

    return {
        "channel_id": channel_id,
        "channel_name": channel.channel_name,
        "source_dir": old_channel_dir,
        "dest_dir": new_channel_dir,
        "same_path": same_path,
        "file_count": file_count,
        "missing_count": missing_count,
        "total_size": total_size,
        "db_records": len(videos),
    }


@router.post("/{channel_id}/move-files/preview")
async def move_files_preview(
    channel_id: int,
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview what a file move would do before executing."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Channel not found")

    preview = await _get_move_preview(db, channel_id, body.new_download_dir)
    return preview


@router.post("/move-all/preview")
async def move_all_preview(
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Preview what moving all channels would do."""
    result = await db.execute(select(Channel).where(Channel.channel_id != "__standalone__"))
    channels = result.scalars().all()

    previews = []
    total_files = 0
    total_size = 0
    channels_to_move = 0

    for channel in channels:
        preview = await _get_move_preview(db, channel.id, body.new_download_dir)
        if not preview.get("same_path"):
            previews.append(preview)
            total_files += preview["file_count"]
            total_size += preview["total_size"]
            channels_to_move += 1

    return {
        "channels": previews,
        "channels_to_move": channels_to_move,
        "total_files": total_files,
        "total_size": total_size,
    }


def _same_channel_folder(folder: str, old_dir: str, new_dir: str) -> bool:
    return _same_location(os.path.join(old_dir, folder), os.path.join(new_dir, folder))


def _same_location(a: str, b: str) -> bool:
    """True when a and b are the same place. Uses device/inode when both exist
    (case-insensitive filesystems, overlapping bind mounts), realpath otherwise."""
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return os.path.realpath(a) == os.path.realpath(b)


def _is_default_dir(new_dir: str) -> bool:
    return os.path.realpath(new_dir) == os.path.realpath(settings.DOWNLOAD_DIR)


def _remap_path(file_path: str, old_dir: str, new_dir: str) -> str | None:
    """Re-root file_path from old_dir to new_dir; None if it isn't under old_dir."""
    old_n = os.path.normpath(old_dir)
    new_n = os.path.normpath(new_dir)
    fp = os.path.normpath(file_path)
    if not fp.startswith(old_n.rstrip(os.sep) + os.sep):
        return None
    return os.path.join(new_n, fp[len(old_n.rstrip(os.sep)) + 1:])


async def _move_channel_contents(
    db: AsyncSession, channel: Channel, old_dir: str, new_dir: str,
    skip_keys: set[str] | None = None,
    shared_stems: set[str] | None = None,
    overwrite: bool = True,
    strict_sidecars: bool = False,
) -> tuple[int, list[str], int]:
    """Move one channel's videos and metadata from old_dir to new_dir.

    Each video's new file_path is committed right after its files move; a video
    whose move fails keeps its files and DB path. Nothing is deleted except
    folders left empty inside the old channel folder.
    skip_keys: file identities (file_keys) of files that must not move.
    shared_stems: stems another channel's video uses; sidecars of those stay.
    overwrite: whether an equal-size file at the target may be replaced.
    strict_sidecars: a sidecar that could not move counts as an error.
    Returns (videos moved, error messages, untracked files left behind).
    """
    from app.utils.file_utils import (
        ASSOCIATED_EXTENSIONS, count_files, file_keys, move_channel_metadata,
        move_sidecar_files, remove_empty_dirs, stem_key,
    )

    from app.services.channel_service import pick_free_folder, taken_folder_keys, _folder_key

    safe_name = channel_folder_name(channel)
    # Another channel already using this folder in the target root must not be
    # merged with: this channel moves into a free "Name (Platform)" folder instead.
    new_folder = safe_name
    taken = await taken_folder_keys(db, exclude_id=channel.id)
    if _folder_key(new_dir, safe_name) in taken:
        new_folder = pick_free_folder(taken, channel.channel_name, channel.platform, new_dir)
    old_channel_dir = os.path.join(old_dir, safe_name)
    new_channel_dir = os.path.join(new_dir, new_folder)
    moved = 0
    errors: list[str] = []
    channel_pk = channel.id

    # Plain values: a rollback expires ORM objects, and async sessions can't lazy-load them.
    result = await db.execute(
        select(Video.id, Video.file_path).where(
            Video.channel_id == channel.id, Video.file_path.isnot(None)
        )
    )
    def _plan(old_path: str):
        """Disk work for one video, run off the event loop. None = leave it alone."""
        if skip_keys and file_keys(old_path) & skip_keys:
            return None  # a file another channel's video records stays where it is
        if new_folder != safe_name:
            new_path = _remap_path(old_path, old_channel_dir, new_channel_dir)
        else:
            new_path = None
        if new_path is None:
            new_path = _remap_path(old_path, old_dir, new_dir)
        if new_path is None or _same_location(new_path, old_path):
            return None
        shared_stem = bool(shared_stems) and stem_key(old_path) in shared_stems
        return new_path, os.path.exists(old_path), os.path.exists(new_path), shared_stem

    for video_pk, old_path in result.all():
        if not old_path:
            continue
        plan = await asyncio.to_thread(_plan, old_path)
        if plan is None:
            continue
        new_path, old_exists, new_exists, shared_stem = plan
        try:
            if old_exists:
                # Sidecars named after a stem another channel's video also uses stay put.
                extra = (False,) if shared_stem else ()
                await asyncio.to_thread(move_video_files, old_path, new_path, overwrite, *extra)
                if strict_sidecars and not shared_stem:
                    base = os.path.splitext(old_path)[0]
                    stuck = await asyncio.to_thread(
                        lambda: [base + e for e in ASSOCIATED_EXTENSIONS if os.path.exists(base + e)]
                    )
                    if stuck:
                        errors.append(f"{old_path}: sidecar not moved: " + ", ".join(
                            os.path.basename(s) for s in stuck))
            elif new_exists:
                # An earlier run moved the file but didn't record it: heal the path
                # and bring over any sidecars it left behind.
                if not shared_stem:
                    await asyncio.to_thread(move_sidecar_files, old_path, new_path, overwrite)
            else:
                continue
            video = await db.get(Video, video_pk)
            video.file_path = new_path
            await db.commit()
        except Exception as e:
            logger.warning("Failed to move %s: %s", old_path, e)
            errors.append(f"{old_path}: {e}")
            try:
                await db.rollback()
            except Exception:
                pass
            continue
        moved += 1

    if not errors:
        # Leave shared artwork and tvshow.nfo with an old channel that still uses
        # this folder; the moved channel gets its own written on its next download.
        others = await db.execute(select(Channel).where(Channel.id != channel_pk))
        other_dirs = [
            os.path.join(c.download_dir or settings.DOWNLOAD_DIR, channel_folder_name(c))
            for c in others.scalars().all()
        ]
        old_key = await asyncio.to_thread(lambda: os.path.realpath(old_channel_dir).casefold())
        shared = await asyncio.to_thread(
            lambda: any(os.path.realpath(d).casefold() == old_key for d in other_dirs)
        )
        meta_errors = []
        if not shared:
            _, meta_errors = await asyncio.to_thread(
                move_channel_metadata, old_channel_dir, new_channel_dir
            )
        for err in meta_errors:
            logger.warning("Channel metadata not moved: %s", err)
        if new_folder != safe_name:
            row = await db.get(Channel, channel_pk)
            row.folder_name = new_folder
            await db.commit()
            channel.folder_name = new_folder

    await asyncio.to_thread(remove_empty_dirs, old_channel_dir)
    left = await asyncio.to_thread(count_files, old_channel_dir) if os.path.isdir(old_channel_dir) else 0
    return moved, errors, left


def _failure_message(errors: list[str]) -> str:
    return f"{len(errors)} errors, first: " + "; ".join(errors[:3])


# Moves in flight. Two moves of the same files at once could race between one
# task's finished copy and the other's equal-size replace, so they're refused.
_move_all_running = False


async def _move_channel_task(channel_id: int, new_dir: str, old_dir: str):
    """Background task to move a channel's files using DB file_path as source of truth."""
    try:
        await _move_channel_task_inner(channel_id, new_dir, old_dir)
    finally:
        _moving_channels.discard(channel_id)


async def _move_channel_task_inner(channel_id: int, new_dir: str, old_dir: str):
    from app.database import async_session
    from app.services.notification_service import NotificationService

    async with async_session() as db:
        channel = await db.get(Channel, channel_id)
        if not channel:
            return

        if _same_channel_folder(channel_folder_name(channel), old_dir, new_dir):
            # Another name for the same folder (second mount, different case).
            await NotificationService.broadcast("move_complete", {
                "channel_name": channel.channel_name,
                "message": f"'{channel.channel_name}' is already in that folder; nothing to move",
            })
            return

        logger.info("Moving channel '%s' files to %s", channel.channel_name, new_dir)
        name = channel.channel_name
        moved_files = 0
        folder_before = channel_folder_name(channel)
        try:
            moved_files, errors, left = await _move_channel_contents(db, channel, old_dir, new_dir)
            if not errors:
                channel.download_dir = None if _is_default_dir(new_dir) else new_dir
                await db.commit()
        except Exception as e:
            logger.error("Move of '%s' crashed: %s", name, e, exc_info=True)
            errors, left = [str(e)], 0

        if errors:
            # Keep the channel's folder setting so the unmoved videos stay where they are.
            await NotificationService.broadcast("move_complete", {
                "channel_name": name,
                "failed": True,
                "message": (f"Move of '{name}' FAILED: {moved_files} files moved, "
                            + _failure_message(errors)),
            })
            logger.error("Move failed for '%s': %d moved, %s", name, moved_files,
                         _failure_message(errors))
            return

        extra = f", {left} other files left in the old folder" if left else ""
        folder_after = channel_folder_name(channel)
        if folder_after != folder_before:
            extra += (f"; the folder '{folder_before}' was already in use there, "
                      f"so it went to '{folder_after}'")
        await NotificationService.broadcast("move_complete", {
            "channel_name": channel.channel_name,
            "message": f"Moved '{channel.channel_name}' to {new_dir} ({moved_files} files{extra})",
        })
        logger.info("Move complete for '%s': %d files moved, %d left", channel.channel_name, moved_files, left)


@router.post("/{channel_id}/move-files", status_code=202)
async def move_channel_files(
    channel_id: int,
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start moving channel files to a new directory."""
    from app.utils.file_utils import validate_download_path
    try:
        validate_download_path(body.new_download_dir, settings.allowed_download_roots)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Capture old directory BEFORE any changes
    old_dir = channel.download_dir or settings.DOWNLOAD_DIR

    # Check for same-path move
    safe_name = channel_folder_name(channel)
    old_channel_dir = os.path.join(old_dir, safe_name)
    new_channel_dir = os.path.join(body.new_download_dir, safe_name)
    if os.path.realpath(old_channel_dir) == os.path.realpath(new_channel_dir):
        return {"message": f"'{channel.channel_name}' is already in {body.new_download_dir}"}

    if channel_id in _moving_channels or _move_all_running:
        raise HTTPException(status_code=409, detail="A move of this channel's files is already running")

    # Count files to decide sync vs async
    file_count = await db.scalar(
        select(func.count(Video.id)).where(Video.channel_id == channel_id, Video.file_path.isnot(None))
    ) or 0

    # Do NOT update channel.download_dir here - the background task handles it after move completes
    _moving_channels.add(channel_id)
    asyncio.create_task(_move_channel_task(channel_id, body.new_download_dir, old_dir))

    return {"message": f"Moving '{channel.channel_name}' to {body.new_download_dir} ({file_count} files)"}


async def _move_all_task(new_dir: str, old_dirs: dict[int, str]):
    """Background task to move all channels."""
    global _move_all_running
    try:
        await _move_all_task_inner(new_dir, old_dirs)
    finally:
        _move_all_running = False


async def _move_all_task_inner(new_dir: str, old_dirs: dict[int, str]):
    from app.database import async_session
    from app.services.notification_service import NotificationService

    async with async_session() as db:
        result = await db.execute(select(Channel).where(Channel.channel_id != "__standalone__"))
        channels = result.scalars().all()

        total_moved = 0
        all_errors: list[str] = []
        total_left = 0
        channels_moved = 0
        channels_failed = 0
        renamed: list[str] = []

        # Plain values: a rollback expires ORM objects, and async sessions can't lazy-load them.
        infos = [(c.id, c.channel_name, c.download_dir, channel_folder_name(c)) for c in channels]

        for cid, cname, cdir, cfolder in infos:
            old_dir = old_dirs.get(cid, cdir or settings.DOWNLOAD_DIR)
            if _same_channel_folder(cfolder, old_dir, new_dir):
                continue

            try:
                channel = await db.get(Channel, cid)
                channel_moved, errors, left = await _move_channel_contents(db, channel, old_dir, new_dir)
                if not errors:
                    if channel_folder_name(channel) != cfolder:
                        renamed.append(f"'{cfolder}' -> '{channel_folder_name(channel)}'")
                    channel = await db.get(Channel, cid)
                    channel.download_dir = None if _is_default_dir(new_dir) else new_dir
                    await db.commit()
            except Exception as e:
                logger.error("Move of '%s' crashed: %s", cname, e, exc_info=True)
                channel_moved, errors, left = 0, [f"{cname}: {e}"], 0
                try:
                    await db.rollback()  # else every later channel fails with PendingRollbackError
                except Exception:
                    pass
            total_moved += channel_moved
            total_left += left
            if errors:
                # Leave this channel's folder setting alone; its unmoved videos stay put.
                channels_failed += 1
                all_errors.extend(errors)
                continue

            if channel_moved > 0:
                channels_moved += 1

        extra = f", {total_left} other files left in old folders" if total_left else ""
        if all_errors:
            message = (f"Move all FAILED for {channels_failed} channels ({channels_moved} moved, "
                       f"{total_moved} files): " + _failure_message(all_errors))
            logger.error(message)
        else:
            if renamed:
                extra += ("; folders already in use there were renamed: " + ", ".join(renamed))
            message = f"Moved {channels_moved} channels to {new_dir} ({total_moved} files{extra})"
            logger.info("Move all complete: %d channels, %d files moved", channels_moved, total_moved)
        await NotificationService.broadcast(
            "move_complete", {"message": message, "failed": bool(all_errors)}
        )


@router.post("/move-all", status_code=202)
async def move_all_channels(
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start moving all channel files to a new directory."""
    from app.utils.file_utils import validate_download_path
    try:
        validate_download_path(body.new_download_dir, settings.allowed_download_roots)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    global _move_all_running
    if _move_all_running or _moving_channels:
        raise HTTPException(status_code=409, detail="A move is already running; wait for it to finish")

    # Capture all old directories BEFORE any changes
    result = await db.execute(select(Channel).where(Channel.channel_id != "__standalone__"))
    channels = result.scalars().all()
    old_dirs = {ch.id: (ch.download_dir or settings.DOWNLOAD_DIR) for ch in channels}

    _move_all_running = True
    asyncio.create_task(_move_all_task(body.new_download_dir, old_dirs))

    return {"message": f"Moving all channels to {body.new_download_dir}"}


@router.post("/{channel_id}/import/scan")
async def scan_for_import(
    channel_id: int,
    body: ImportScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Scan a folder for video files that match un-downloaded videos for this channel."""
    from app.utils.file_utils import validate_download_path
    try:
        validate_download_path(body.folder_path, settings.allowed_download_roots)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Channel not found")

    try:
        matches = await scan_folder_for_imports(
            db, channel_id, body.folder_path, threshold=body.threshold / 100.0,
        )
        return {"matches": matches, "total": len(matches)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Import scan failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Scan failed: {e}")


@router.get("/{channel_id}/shorts")
async def list_channel_shorts(
    channel_id: int,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all videos identified as shorts for a channel."""
    query = (
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == True)
        .order_by(Video.season.desc(), Video.episode.desc())
    )
    if status:
        query = query.where(Video.status == status)
    result = await db.execute(query)
    videos = result.scalars().all()
    return {
        "items": [VideoResponse.model_validate(v) for v in videos],
        "total": len(videos),
    }


@router.post("/{channel_id}/shorts/delete")
async def delete_channel_shorts(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete all downloaded shorts for a channel (removes files and marks as skipped)."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == True)
        .where(Video.status == "completed")
    )
    videos = result.scalars().all()

    deleted = 0
    for video in videos:
        if video.file_path:
            if await asyncio.to_thread(delete_video_files, video.file_path) > 0:
                deleted += 1
        video.status = "skipped"
        video.file_path = None
        video.file_size = None

    await db.commit()
    return {"message": f"Deleted {deleted} shorts", "deleted": deleted}


@router.post("/{channel_id}/shorts/detect")
async def detect_channel_shorts(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Scan existing videos and mark shorts using duration, title, and URL patterns."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # YouTube Shorts are <= 60s (separate from the min_video_duration download filter)
    threshold = SHORTS_MAX_DURATION

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == False)
    )
    videos = result.scalars().all()

    detected = 0
    for video in videos:
        if _is_likely_short(video, threshold):
            video.is_short = True
            detected += 1

    await db.commit()
    return {"message": f"Detected {detected} shorts (threshold: {threshold}s)", "detected": detected, "threshold": threshold}


def _is_likely_short(video, threshold: int) -> bool:
    """Check if a video is likely a YouTube Short using multiple signals."""
    # Duration check (if we have it)
    if video.duration and video.duration <= threshold:
        return True

    # Title contains #shorts or (Short) pattern
    if video.title:
        title_lower = video.title.lower()
        if "#shorts" in title_lower or "#short" in title_lower:
            return True

    return False


@router.post("/{channel_id}/shorts/detect-clean/preview")
async def detect_clean_shorts_preview(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Preview what detect & clean would do: reclassify shorts, list files to delete, count renumbers."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    threshold = SHORTS_MAX_DURATION

    # Find videos that would be newly classified as shorts
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == False)
    )
    all_non_shorts = result.scalars().all()

    new_shorts = []
    files_to_delete = 0
    disk_space_freed = 0
    for video in all_non_shorts:
        if _is_likely_short(video, threshold):
            new_shorts.append({
                "video_id": video.id,
                "title": video.title,
                "duration": video.duration,
                "status": video.status,
                "has_file": bool(video.file_path and os.path.exists(video.file_path)),
                "file_size": video.file_size or 0,
            })
            if video.file_path and video.status == "completed" and os.path.exists(video.file_path):
                files_to_delete += 1
                disk_space_freed += video.file_size or 0

    # Count existing shorts with completed files
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == True)
        .where(Video.status == "completed")
    )
    existing_shorts_with_files = len(result.scalars().all())

    return {
        "threshold": threshold,
        "new_shorts_count": len(new_shorts),
        "new_shorts": new_shorts[:50],  # Limit preview to 50
        "existing_shorts_with_files": existing_shorts_with_files,
        "files_to_delete": files_to_delete + existing_shorts_with_files,
        "disk_space_freed": disk_space_freed,
        "will_renumber": len(new_shorts) > 0,
    }


@router.post("/{channel_id}/shorts/detect-clean/confirm")
async def detect_clean_shorts_confirm(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detect shorts, delete their files, mark as skipped, and renumber remaining episodes."""
    from app.services.naming_service import build_output_path

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    threshold = SHORTS_MAX_DURATION

    # Step 1: Detect new shorts
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == False)
    )
    detected = 0
    for video in result.scalars().all():
        if _is_likely_short(video, threshold):
            video.is_short = True
            detected += 1

    # Step 2: Delete files for all shorts (including previously detected)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == True)
        .where(Video.status == "completed")
    )
    deleted = 0
    for video in result.scalars().all():
        if video.file_path:
            if await asyncio.to_thread(delete_video_files, video.file_path) > 0:
                deleted += 1
        video.status = "skipped"
        video.monitored = False
        video.file_path = None
        video.file_size = None

    # Step 3: Renumber remaining episodes
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .order_by(Video.upload_date.asc(), Video.id.asc())
    )
    all_videos = result.scalars().all()
    from app.services.naming_service import resolve_naming_template
    template = await resolve_naming_template(db, channel.naming_template)
    protected, _ = await other_recorded(db, channel_id)
    renamed = await asyncio.to_thread(
        _renumber_channel_episodes, all_videos, channel, template, protected_keys=protected
    )

    await db.commit()
    return {
        "message": f"Detected {detected} shorts, deleted {deleted} files, renumbered {renamed} episodes",
        "detected": detected,
        "deleted": deleted,
        "renamed": renamed,
        "threshold": threshold,
    }


# --- Livestream endpoints ---

def _is_likely_livestream(video) -> bool:
    """Check if a video is likely a livestream using multiple signals."""
    # Title contains common livestream indicators
    if video.title:
        title_lower = video.title.lower()
        if any(kw in title_lower for kw in ["[live]", "🔴 live", "livestream", "live stream"]):
            return True
    # Very long duration (over 4 hours) is often a livestream
    if video.duration and video.duration > 4 * 3600:
        return True
    return False


@router.get("/{channel_id}/livestreams")
async def list_channel_livestreams(
    channel_id: int,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all videos identified as livestreams for a channel."""
    query = (
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == True)
        .order_by(Video.upload_date.desc())
    )
    if status:
        query = query.where(Video.status == status)
    result = await db.execute(query)
    videos = result.scalars().all()
    return {
        "items": [VideoResponse.model_validate(v) for v in videos],
        "total": len(videos),
    }


@router.post("/{channel_id}/livestreams/delete")
async def delete_channel_livestreams(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete all downloaded livestreams for a channel."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == True)
        .where(Video.status == "completed")
    )
    videos = result.scalars().all()

    deleted = 0
    for video in videos:
        if video.file_path:
            if await asyncio.to_thread(delete_video_files, video.file_path) > 0:
                deleted += 1
        video.status = "skipped"
        video.file_path = None
        video.file_size = None

    await db.commit()
    return {"message": f"Deleted {deleted} livestreams", "deleted": deleted}


@router.post("/{channel_id}/livestreams/detect")
async def detect_channel_livestreams(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Scan existing videos and mark livestreams using title and duration heuristics."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == False)
    )
    videos = result.scalars().all()

    detected = 0
    for video in videos:
        if _is_likely_livestream(video):
            video.is_livestream = True
            detected += 1

    await db.commit()
    return {"message": f"Detected {detected} livestreams", "detected": detected}


# _renumber_channel_episodes now lives in app/utils/renumber.py (imported at top)


@router.post("/{channel_id}/force-rescan", status_code=200)
async def force_rescan(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Rebuild a channel's stuck video records and re-scan it.

    Use this to recover from stuck channels. Only monitored videos that are
    pending, failed or queued and have no file are deleted (with their queue
    entries and logs) and rebuilt from a fresh scan. Downloaded, skipped,
    unmonitored, retention-removed and downloading videos are kept as they are.
    The listing is fetched first, and nothing changes if it is empty or can't be
    read. Afterwards files on disk are re-linked by video id and episodes are
    renumbered in date order.
    """
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    if channel_id in _scanning_channels:
        raise HTTPException(status_code=409, detail="A scan is running for this channel. Try again when it finishes.")

    # Held for the whole run so scheduled and RSS scans skip this channel
    _scanning_channels.add(channel_id)
    try:
        return await _force_rescan_locked(channel, db)
    finally:
        _scanning_channels.discard(channel_id)


async def _number_rebuilt_rows(db: AsyncSession, channel_id: int, kept_ids: set[int]) -> None:
    """Give rows the rescan re-created episode numbers no kept row uses.

    The scan numbers new rows after the rows already in a season, which can repeat
    a kept episode number (Plex then shows only one of the two). Kept rows keep
    their numbers and files are never renamed: a rebuilt row takes its date-order
    position when that number is free, otherwise the season's next free number.
    """
    rows = (await db.execute(
        select(Video).where(Video.channel_id == channel_id)
        .order_by(Video.upload_date.asc(), Video.id.asc())
    )).scalars().all()
    by_season: dict[int, list[Video]] = {}
    for v in rows:
        if not v.is_short and not v.is_livestream:
            by_season.setdefault(v.season, []).append(v)
    changed = False
    for season_rows in by_season.values():
        used = {v.episode for v in season_rows if v.id in kept_ids}
        for position, v in enumerate(season_rows, 1):
            if v.id in kept_ids:
                continue
            episode = position if position not in used else max(used, default=0) + 1
            used.add(episode)
            if v.episode != episode:
                v.episode = episode
                changed = True
    if changed:
        await db.commit()


async def _force_rescan_locked(channel: Channel, db: AsyncSession):
    from sqlalchemy import delete as sa_delete

    service = ChannelService(db)
    try:
        video_list = await service.fetch_video_list(channel)
    except Exception as e:
        logger.error("Force rescan listing failed for %s: %s", channel.channel_name, e, exc_info=True)
        raise HTTPException(
            status_code=502,
            detail=f"Could not read the channel's video list ({e}). Nothing was changed.",
        )
    if not video_list:
        raise HTTPException(
            status_code=502,
            detail="The channel's video list came back empty, so nothing was changed. Try again later.",
        )

    # Only stuck rows are rebuilt: monitored, pending/failed/queued, no file, no
    # marker. Downloaded, skipped, unmonitored, retention-removed and downloading
    # rows are kept as they are, so a partial or failed scan can never turn one of
    # them into a "new" video that gets queued, and choices the user made stay.
    disposable = (
        Video.channel_id == channel.id,
        Video.status.in_(("pending", "failed", "queued")),
        Video.monitored.is_(True),
        or_(Video.file_path.is_(None), Video.file_path == ""),
        Video.retention_removed_at.is_(None),
    )
    kept_ids = set((await db.execute(
        select(Video.id).where(Video.channel_id == channel.id).except_(select(Video.id).where(*disposable))
    )).scalars().all())
    video_ids = select(Video.id).where(*disposable)
    await db.execute(sa_delete(DownloadLog).where(DownloadLog.video_id.in_(video_ids)))
    await db.execute(sa_delete(DownloadQueue).where(DownloadQueue.video_id.in_(video_ids)))
    deleted_count = await db.scalar(select(func.count(Video.id)).where(*disposable)) or 0
    await db.execute(sa_delete(Video).where(*disposable))

    # Reset channel health
    channel.health_status = "healthy"
    channel.last_error_code = None

    await db.commit()

    # Fresh scan from the listing fetched above (the scan lock is already held)
    try:
        new_count = await service._scan_channel_inner(channel, video_list=video_list)
    except Exception as e:
        logger.error("Force rescan failed for %s: %s", channel.channel_name, e, exc_info=True)
        return {
            "message": f"Cleared {deleted_count} old records but scan failed: {e}",
            "deleted": deleted_count,
            "new_videos": 0,
        }

    await _number_rebuilt_rows(db, channel.id, kept_ids)

    # Match existing files on disk back to the new video records by video ID
    import re
    import json as _json
    from pathlib import Path

    matched_count = 0
    download_dir = Path(channel.download_dir or settings.DOWNLOAD_DIR)
    channel_dir = download_dir / channel_folder_name(channel)
    search_dir = channel_dir if channel_dir.is_dir() else download_dir
    if search_dir.is_dir():
        file_map: dict[str, Path] = {}

        # Primary: extract video ID from [video_id] in filename
        for mp4 in search_dir.rglob("*.mp4"):
            m = re.search(r"\[([A-Za-z0-9_-]{8,})\]", mp4.stem)
            if m:
                file_map[m.group(1)] = mp4

        # Fallback: read video ID from .info.json sidecar files
        for info_json in search_dir.rglob("*.info.json"):
            try:
                data = _json.loads(info_json.read_text(encoding="utf-8"))
                vid_id = data.get("id")
                if vid_id and vid_id not in file_map:
                    mp4_sibling = info_json.with_suffix("").with_suffix(".mp4")
                    if not mp4_sibling.exists():
                        stem = info_json.stem.removesuffix(".info")
                        mp4_sibling = info_json.parent / f"{stem}.mp4"
                    if mp4_sibling.exists():
                        file_map[vid_id] = mp4_sibling
            except Exception:
                continue

        if file_map:
            result = await db.execute(
                select(Video).where(
                    Video.channel_id == channel.id,
                    Video.video_id.in_(list(file_map.keys())),
                    Video.status != "downloading",
                )
            )
            matched_video_ids = []
            for video in result.scalars().all():
                # A file already on record (and still on disk) is never replaced;
                # a recorded file that has gone missing may be re-linked by its id.
                if video.file_path and os.path.exists(video.file_path):
                    continue
                mp4_path = file_map.get(video.video_id)
                if mp4_path and mp4_path.exists():
                    video.status = "completed"
                    video.file_path = str(mp4_path)
                    video.file_size = mp4_path.stat().st_size
                    video.retention_removed_at = None  # the file is back on disk
                    if video.downloaded_at is None:
                        video.downloaded_at = datetime.fromtimestamp(
                            mp4_path.stat().st_mtime, tz=timezone.utc
                        ).replace(tzinfo=None)
                    matched_video_ids.append(video.id)
                    matched_count += 1
            if matched_video_ids:
                from sqlalchemy import delete as sa_delete
                await db.execute(
                    sa_delete(DownloadQueue).where(
                        DownloadQueue.video_id.in_(matched_video_ids)
                    )
                )
            await db.commit()
            logger.info("Force rescan matched %d existing files for %s", matched_count, channel.channel_name)

    return {
        "message": f"Cleared {deleted_count} old records, found {new_count} videos on rescan, matched {matched_count} existing files",
        "deleted": deleted_count,
        "new_videos": new_count,
        "matched_files": matched_count,
    }


@router.post("/{channel_id}/import/confirm")
async def confirm_import(
    channel_id: int,
    body: ImportConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """Import confirmed file matches: move, rename, write metadata, update DB."""
    from app.utils.file_utils import validate_download_path

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Channel not found")

    for match in body.matches:
        try:
            validate_download_path(match.file_path, settings.allowed_download_roots)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"Invalid file path: {e}")

    try:
        result = await import_matched_files(
            db, channel_id,
            [m.model_dump() for m in body.matches],
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Import failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Import failed: {e}")
