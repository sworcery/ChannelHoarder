import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.utils.file_utils import escape_like
from app.models import Channel, DownloadLog, Video, DownloadQueue
from pydantic import BaseModel, Field

from app.schemas import (
    ChannelCreate, ChannelUpdate, ChannelResponse, VideoResponse,
    ImportScanRequest, ImportConfirmRequest,
)
from app.services.channel_service import ChannelService
from app.services.import_service import scan_folder_for_imports, import_matched_files
from app.utils.renumber import renumber_channel_episodes as _renumber_channel_episodes

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/", response_model=list[ChannelResponse])
async def list_channels(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
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

    for field, value in update_data.items():
        setattr(channel, field, value)

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
    limit: int = Query(50, ge=1, le=500),
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
    import os
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
                    naming_template=channel.naming_template,
                    base_dir=channel.download_dir,
                ) + ".mp4"

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

    renamed = _renumber_channel_episodes(videos, channel)

    await db.commit()
    return {
        "message": f"Re-numbered {updated} episodes, renamed {renamed} files on disk",
        "updated": updated,
        "renamed": renamed,
    }


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
    video_ids: list[int] = Field(..., min_length=1, max_length=1000)


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
        video.status = "skipped"
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
        unskipped += 1

    await db.commit()
    return {"message": f"Unskipped {unskipped} videos", "unskipped": unskipped}


class BulkDeleteRequest(BaseModel):
    video_ids: list[int] = Field(..., min_length=1, max_length=1000)
    delete_files: bool = False


@router.post("/{channel_id}/videos/bulk-delete")
async def bulk_delete_videos(
    channel_id: int,
    body: BulkDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """Delete selected videos. Optionally removes files from disk."""
    import os

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
    files_removed = 0
    for video in videos:
        if body.delete_files and video.file_path:
            base = video.file_path.rsplit(".", 1)[0] if "." in video.file_path else video.file_path
            for ext in [".mp4", ".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                path = base + ext
                if os.path.exists(path):
                    os.remove(path)
                    files_removed += 1

        video.status = "skipped"
        video.file_path = None
        video.file_size = None
        deleted += 1

    await db.commit()
    return {"message": f"Deleted {deleted} videos ({files_removed} files removed)", "deleted": deleted, "files_removed": files_removed}


@router.delete("/{channel_id}/videos/{video_id}")
async def delete_video(
    channel_id: int,
    video_id: int,
    delete_files: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """Delete a single video. Optionally removes files from disk."""
    import os

    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    files_removed = False
    if delete_files and video.file_path:
        base = video.file_path.rsplit(".mp4", 1)[0] if video.file_path.endswith(".mp4") else video.file_path
        for ext in [".mp4", ".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
            path = base + ext
            if os.path.exists(path):
                os.remove(path)
                files_removed = True

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
    """Delete existing file and re-queue video for download."""
    import os

    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Delete existing file if present
    if video.file_path:
        base = video.file_path.rsplit(".mp4", 1)[0] if video.file_path.endswith(".mp4") else video.file_path
        for ext in [".mp4", ".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
            path = base + ext
            if os.path.exists(path):
                os.remove(path)

    video.file_path = None
    video.file_size = None
    video.quality_downloaded = None
    video.error_code = None
    video.error_message = None
    video.error_details = None
    video.status = "queued"

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
    import os

    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    files_removed = False
    if video.file_path:
        base = video.file_path.rsplit(".mp4", 1)[0] if video.file_path.endswith(".mp4") else video.file_path
        for ext in [".mp4", ".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
            path = base + ext
            if os.path.exists(path):
                os.remove(path)
                files_removed = True

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
    import os
    import shutil
    from app.services.naming_service import build_output_path

    result = await db.execute(
        select(Video).where(Video.id == video_id, Video.channel_id == channel_id)
    )
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    if not video.file_path or not os.path.exists(video.file_path):
        raise HTTPException(status_code=400, detail="No file on disk to rename")

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    new_path = build_output_path(
        channel_name=channel.channel_name,
        video_title=video.title,
        video_id=video.video_id,
        upload_date=video.upload_date,
        season=video.season,
        episode=video.episode,
        naming_template=channel.naming_template,
        base_dir=channel.download_dir,
    ) + ".mp4"

    old_path = video.file_path
    if old_path == new_path:
        return {"message": "File already has the correct name", "renamed": False}

    os.makedirs(os.path.dirname(new_path), exist_ok=True)
    shutil.move(old_path, new_path)
    video.file_path = new_path

    # Move associated files
    for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
        old_extra = old_path.rsplit(".mp4", 1)[0] + ext
        new_extra = new_path.rsplit(".mp4", 1)[0] + ext
        if os.path.exists(old_extra):
            shutil.move(old_extra, new_extra)

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
    video_ids: list[int] = Field(..., min_length=1, max_length=1000)
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
        count += 1
    await db.commit()
    return {"message": f"{'Monitored' if body.monitored else 'Unmonitored'} {count} videos", "count": count}


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
        select(Video).where(Video.channel_id == channel_id)
    )
    videos = result.scalars().all()
    count = 0
    for video in videos:
        video.monitored = body.monitored
        count += 1
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
        select(Video).where(Video.channel_id == channel_id, Video.season == season)
    )
    videos = result.scalars().all()
    count = 0
    for video in videos:
        video.monitored = body.monitored
        count += 1
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
    import asyncio

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
    import asyncio

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
    video_url = build_video_url(platform, video.video_id)
    output_base = video.file_path.rsplit(".", 1)[0] if "." in video.file_path else video.file_path

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
    import os
    if not file_path:
        return False
    base = file_path.rsplit(".", 1)[0] if "." in file_path else file_path
    for ext in [".en.vtt", ".en.srt", ".en.ass"]:
        if os.path.exists(base + ext):
            return True
    return False


async def _download_channel_subtitles_task(channel_id: int, channel_name: str):
    """Background task to download subtitles for all completed videos in a channel."""
    import asyncio
    from app.database import async_session
    from app.utils.platform_utils import build_video_url
    from app.services.ytdlp_service import YtdlpService
    from app.services.notification_service import NotificationService

    async with async_session() as db:
        result = await db.execute(select(Channel).where(Channel.id == channel_id))
        channel = result.scalar_one_or_none()
        if not channel:
            return

        platform = channel.platform or "youtube"

        result = await db.execute(
            select(Video)
            .where(Video.channel_id == channel_id)
            .where(Video.status == "completed")
            .where(Video.file_path.isnot(None))
        )
        videos = result.scalars().all()

        ytdlp = YtdlpService()
        downloaded = 0
        skipped = 0
        failed = 0

        for video in videos:
            if not video.file_path or _has_subtitles(video.file_path):
                skipped += 1
                continue

            video_url = build_video_url(platform, video.video_id)
            output_base = video.file_path.rsplit(".", 1)[0] if "." in video.file_path else video.file_path

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
    import os
    from app.utils.file_utils import sanitize_filename

    channel = await db.get(Channel, channel_id)
    if not channel:
        return {"error": "Channel not found"}

    old_dir = channel.download_dir or settings.DOWNLOAD_DIR
    safe_name = sanitize_filename(channel.channel_name)
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


async def _move_channel_task(channel_id: int, new_dir: str, old_dir: str):
    """Background task to move a channel's files using DB file_path as source of truth."""
    import asyncio
    import os
    import shutil
    from app.database import async_session
    from app.utils.file_utils import sanitize_filename
    from app.services.notification_service import NotificationService

    async with async_session() as db:
        channel = await db.get(Channel, channel_id)
        if not channel:
            return

        safe_name = sanitize_filename(channel.channel_name)
        new_channel_dir = os.path.join(new_dir, safe_name)
        moved_files = 0
        errors = 0

        logger.info("Moving channel '%s' files to %s", channel.channel_name, new_channel_dir)

        # Move files based on DB records (source of truth)
        result = await db.execute(
            select(Video).where(Video.channel_id == channel_id, Video.file_path.isnot(None))
        )
        for video in result.scalars().all():
            if not video.file_path:
                continue

            # Compute new path by replacing the old root with new root
            new_path = video.file_path.replace(old_dir, new_dir, 1)

            if new_path == video.file_path:
                continue

            # Move the video file and associated files
            if os.path.exists(video.file_path):
                os.makedirs(os.path.dirname(new_path), exist_ok=True)
                try:
                    # Overwrite if destination exists
                    if os.path.exists(new_path):
                        os.remove(new_path)
                    await asyncio.to_thread(shutil.move, video.file_path, new_path)
                    moved_files += 1

                    # Move associated files (.nfo, -thumb.jpg, .info.json)
                    if video.file_path.endswith(".mp4"):
                        base_old = video.file_path.rsplit(".mp4", 1)[0]
                        base_new = new_path.rsplit(".mp4", 1)[0]
                        for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                            old_extra = base_old + ext
                            new_extra = base_new + ext
                            if os.path.exists(old_extra):
                                if os.path.exists(new_extra):
                                    os.remove(new_extra)
                                await asyncio.to_thread(shutil.move, old_extra, new_extra)
                except Exception as e:
                    logger.warning("Failed to move %s: %s", video.file_path, e)
                    errors += 1
                    continue

            # Update DB path regardless (file may have already been moved manually)
            video.file_path = new_path

        # Clean up empty source directories
        old_channel_dir = os.path.join(old_dir, safe_name)
        if os.path.isdir(old_channel_dir):
            try:
                shutil.rmtree(old_channel_dir)
            except OSError:
                pass

        # Update channel download_dir after all files are moved
        channel.download_dir = new_dir if new_dir != settings.DOWNLOAD_DIR else None
        await db.commit()

        await NotificationService.broadcast("move_complete", {
            "channel_name": channel.channel_name,
            "message": f"Moved '{channel.channel_name}' to {new_dir} ({moved_files} files, {errors} errors)",
        })
        logger.info("Move complete for '%s': %d files moved, %d errors", channel.channel_name, moved_files, errors)


@router.post("/{channel_id}/move-files", status_code=202)
async def move_channel_files(
    channel_id: int,
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start moving channel files to a new directory."""
    import asyncio

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Capture old directory BEFORE any changes
    old_dir = channel.download_dir or settings.DOWNLOAD_DIR

    # Check for same-path move
    from app.utils.file_utils import sanitize_filename
    import os
    safe_name = sanitize_filename(channel.channel_name)
    old_channel_dir = os.path.join(old_dir, safe_name)
    new_channel_dir = os.path.join(body.new_download_dir, safe_name)
    if os.path.realpath(old_channel_dir) == os.path.realpath(new_channel_dir):
        return {"message": f"'{channel.channel_name}' is already in {body.new_download_dir}"}

    # Count files to decide sync vs async
    file_count = await db.scalar(
        select(func.count(Video.id)).where(Video.channel_id == channel_id, Video.file_path.isnot(None))
    ) or 0

    # Do NOT update channel.download_dir here - the background task handles it after move completes
    asyncio.create_task(_move_channel_task(channel_id, body.new_download_dir, old_dir))

    return {"message": f"Moving '{channel.channel_name}' to {body.new_download_dir} ({file_count} files)"}


async def _move_all_task(new_dir: str, old_dirs: dict[int, str]):
    """Background task to move all channels."""
    import asyncio
    import os
    import shutil
    from app.database import async_session
    from app.utils.file_utils import sanitize_filename
    from app.services.notification_service import NotificationService

    async with async_session() as db:
        result = await db.execute(select(Channel).where(Channel.channel_id != "__standalone__"))
        channels = result.scalars().all()

        total_moved = 0
        total_errors = 0
        channels_moved = 0

        for channel in channels:
            old_dir = old_dirs.get(channel.id, channel.download_dir or settings.DOWNLOAD_DIR)
            if old_dir == new_dir:
                continue

            safe_name = sanitize_filename(channel.channel_name)
            new_channel_dir = os.path.join(new_dir, safe_name)

            vid_result = await db.execute(
                select(Video).where(Video.channel_id == channel.id, Video.file_path.isnot(None))
            )
            channel_moved = 0
            for video in vid_result.scalars().all():
                if not video.file_path:
                    continue

                new_path = video.file_path.replace(old_dir, new_dir, 1)
                if new_path == video.file_path:
                    continue

                if os.path.exists(video.file_path):
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    try:
                        if os.path.exists(new_path):
                            os.remove(new_path)
                        await asyncio.to_thread(shutil.move, video.file_path, new_path)
                        channel_moved += 1

                        if video.file_path.endswith(".mp4"):
                            base_old = video.file_path.rsplit(".mp4", 1)[0]
                            base_new = new_path.rsplit(".mp4", 1)[0]
                            for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                                old_extra = base_old + ext
                                new_extra = base_new + ext
                                if os.path.exists(old_extra):
                                    if os.path.exists(new_extra):
                                        os.remove(new_extra)
                                    await asyncio.to_thread(shutil.move, old_extra, new_extra)
                    except Exception as e:
                        logger.warning("Failed to move %s: %s", video.file_path, e)
                        total_errors += 1
                        continue

                video.file_path = new_path

            # Clean up empty source directories
            old_channel_dir = os.path.join(old_dir, safe_name)
            if os.path.isdir(old_channel_dir):
                try:
                    shutil.rmtree(old_channel_dir)
                except OSError:
                    pass

            if channel_moved > 0:
                channels_moved += 1
                total_moved += channel_moved

            channel.download_dir = new_dir if new_dir != settings.DOWNLOAD_DIR else None

        await db.commit()

        await NotificationService.broadcast("move_complete", {
            "message": f"Moved {channels_moved} channels to {new_dir} ({total_moved} files, {total_errors} errors)",
        })
        logger.info("Move all complete: %d channels, %d files moved, %d errors", channels_moved, total_moved, total_errors)


@router.post("/move-all", status_code=202)
async def move_all_channels(
    body: MoveFilesRequest,
    db: AsyncSession = Depends(get_db),
):
    """Start moving all channel files to a new directory."""
    import asyncio

    # Capture all old directories BEFORE any changes
    result = await db.execute(select(Channel).where(Channel.channel_id != "__standalone__"))
    channels = result.scalars().all()
    old_dirs = {ch.id: (ch.download_dir or settings.DOWNLOAD_DIR) for ch in channels}

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
    import os
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
            try:
                base = video.file_path.rsplit(".", 1)[0] if "." in video.file_path else video.file_path
                if os.path.exists(video.file_path):
                    os.remove(video.file_path)
                    deleted += 1
                # Remove associated files
                for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                    extra = base + ext
                    if os.path.exists(extra):
                        os.remove(extra)
            except Exception as e:
                logger.warning("Failed to delete short file %s: %s", video.file_path, e)
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

    # Use channel-specific threshold if set, otherwise default 30s
    threshold = channel.min_video_duration if channel.min_video_duration else 30

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

    # Very small file size (< 20MB) combined with very short duration or no duration
    # Shorts are typically under 20MB
    if video.file_size and video.file_size < 20 * 1024 * 1024:
        if not video.duration or video.duration <= threshold:
            return True

    return False


@router.post("/{channel_id}/shorts/detect-clean/preview")
async def detect_clean_shorts_preview(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Preview what detect & clean would do: reclassify shorts, list files to delete, count renumbers."""
    import os
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    threshold = channel.min_video_duration if channel.min_video_duration else 30

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
    import os
    from app.services.naming_service import build_output_path

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    threshold = channel.min_video_duration if channel.min_video_duration else 30

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
            try:
                if os.path.exists(video.file_path):
                    os.remove(video.file_path)
                    # Remove associated files
                    if video.file_path.endswith(".mp4"):
                        base = video.file_path.rsplit(".mp4", 1)[0]
                        for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                            extra = base + ext
                            if os.path.exists(extra):
                                os.remove(extra)
                    deleted += 1
            except Exception as e:
                logger.warning("Failed to delete short file %s: %s", video.file_path, e)
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
    renamed = _renumber_channel_episodes(all_videos, channel)

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
    import os
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
            try:
                base = video.file_path.rsplit(".", 1)[0] if "." in video.file_path else video.file_path
                if os.path.exists(video.file_path):
                    os.remove(video.file_path)
                    deleted += 1
                for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                    extra = base + ext
                    if os.path.exists(extra):
                        os.remove(extra)
            except Exception as e:
                logger.warning("Failed to delete livestream file %s: %s", video.file_path, e)
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


@router.post("/{channel_id}/livestreams/detect-clean/preview")
async def detect_clean_livestreams_preview(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Preview what detect & clean would do for livestreams."""
    import os
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == False)
    )
    all_non_livestreams = result.scalars().all()

    new_livestreams = []
    files_to_delete = 0
    disk_space_freed = 0
    for video in all_non_livestreams:
        if _is_likely_livestream(video):
            new_livestreams.append({
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

    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == True)
        .where(Video.status == "completed")
    )
    existing_livestreams_with_files = len(result.scalars().all())

    return {
        "new_livestreams_count": len(new_livestreams),
        "new_livestreams": new_livestreams[:50],
        "existing_livestreams_with_files": existing_livestreams_with_files,
        "files_to_delete": files_to_delete + existing_livestreams_with_files,
        "disk_space_freed": disk_space_freed,
        "will_renumber": len(new_livestreams) > 0,
    }


@router.post("/{channel_id}/livestreams/detect-clean/confirm")
async def detect_clean_livestreams_confirm(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Detect livestreams, delete their files, mark as skipped, and renumber remaining episodes."""
    import os

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Step 1: Detect new livestreams
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == False)
    )
    detected = 0
    for video in result.scalars().all():
        if _is_likely_livestream(video):
            video.is_livestream = True
            detected += 1

    # Step 2: Delete files for all livestreams
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_livestream == True)
        .where(Video.status == "completed")
    )
    deleted = 0
    for video in result.scalars().all():
        if video.file_path:
            try:
                if os.path.exists(video.file_path):
                    os.remove(video.file_path)
                    if video.file_path.endswith(".mp4"):
                        base = video.file_path.rsplit(".mp4", 1)[0]
                        for ext in [".nfo", "-thumb.jpg", ".jpg", ".info.json", ".en.vtt", ".en.srt"]:
                            extra = base + ext
                            if os.path.exists(extra):
                                os.remove(extra)
                    deleted += 1
            except Exception as e:
                logger.warning("Failed to delete livestream file %s: %s", video.file_path, e)
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
    renamed = _renumber_channel_episodes(all_videos, channel)

    await db.commit()
    return {
        "message": f"Detected {detected} livestreams, deleted {deleted} files, renumbered {renamed} episodes",
        "detected": detected,
        "deleted": deleted,
        "renamed": renamed,
    }


# _renumber_channel_episodes now lives in app/utils/renumber.py (imported at top)


@router.post("/{channel_id}/force-rescan", status_code=200)
async def force_rescan(
    channel_id: int,
    db: AsyncSession = Depends(get_db),
):
    """Delete all video records for a channel and trigger a fresh scan.

    Use this to recover from stuck channels (e.g. orphan records from before
    foreign key fixes). Removes all videos, queue entries, and logs, then
    re-scans the channel from scratch.
    """
    from sqlalchemy import delete as sa_delete

    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

    # Delete all child records
    video_ids = select(Video.id).where(Video.channel_id == channel.id)
    await db.execute(sa_delete(DownloadLog).where(DownloadLog.video_id.in_(video_ids)))
    await db.execute(sa_delete(DownloadQueue).where(DownloadQueue.video_id.in_(video_ids)))
    deleted_count = await db.scalar(
        select(func.count(Video.id)).where(Video.channel_id == channel.id)
    ) or 0
    await db.execute(sa_delete(Video).where(Video.channel_id == channel.id))

    # Reset channel health
    channel.health_status = "healthy"
    channel.last_error_code = None
    channel.total_videos = 0
    channel.downloaded_count = 0

    await db.commit()

    # Trigger fresh scan
    service = ChannelService(db)
    try:
        new_count = await service.scan_channel(channel)
    except Exception as e:
        logger.error("Force rescan failed for %s: %s", channel.channel_name, e, exc_info=True)
        return {
            "message": f"Cleared {deleted_count} old records but scan failed: {e}",
            "deleted": deleted_count,
            "new_videos": 0,
        }

    return {
        "message": f"Cleared {deleted_count} old records, found {new_count} videos on rescan",
        "deleted": deleted_count,
        "new_videos": new_count,
    }


@router.post("/{channel_id}/import/confirm")
async def confirm_import(
    channel_id: int,
    body: ImportConfirmRequest,
    db: AsyncSession = Depends(get_db),
):
    """Import confirmed file matches: move, rename, write metadata, update DB."""
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Channel not found")

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
