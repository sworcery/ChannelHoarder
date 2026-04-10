import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.utils.file_utils import escape_like
from app.models import Channel, Video, DownloadQueue
from pydantic import BaseModel, Field

from app.schemas import (
    ChannelCreate, ChannelUpdate, ChannelResponse, VideoResponse,
    ImportScanRequest, ImportConfirmRequest,
)
from app.services.channel_service import ChannelService
from app.services.import_service import scan_folder_for_imports, import_matched_files

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
            func.count(Video.id).label("total"),
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
            func.count(Video.id),
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

    await db.delete(channel)
    await db.commit()


@router.get("/{channel_id}/videos")
async def list_channel_videos(
    channel_id: int,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Video).where(Video.channel_id == channel_id).order_by(Video.season.desc(), Video.episode.desc())
    if status:
        query = query.where(Video.status == status)
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
    import os
    import shutil
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
    renamed = 0
    updated = 0

    for video in videos:
        season = video.upload_date.year
        season_counts.setdefault(season, 0)
        season_counts[season] += 1
        new_episode = season_counts[season]

        if video.season != season or video.episode != new_episode:
            old_path = video.file_path
            video.season = season
            video.episode = new_episode
            updated += 1

            if old_path and os.path.exists(old_path):
                new_path = build_output_path(
                    channel_name=channel.channel_name,
                    video_title=video.title,
                    video_id=video.video_id,
                    upload_date=video.upload_date,
                    season=season,
                    episode=new_episode,
                    naming_template=channel.naming_template,
                    base_dir=channel.download_dir,
                ) + ".mp4"

                if old_path != new_path:
                    os.makedirs(os.path.dirname(new_path), exist_ok=True)
                    shutil.move(old_path, new_path)
                    video.file_path = new_path

                    for ext in [".nfo", "-thumb.jpg", ".jpg"]:
                        old_extra = old_path.rsplit(".mp4", 1)[0] + ext
                        new_extra = new_path.rsplit(".mp4", 1)[0] + ext
                        if os.path.exists(old_extra):
                            shutil.move(old_extra, new_extra)

                    renamed += 1

    await db.commit()
    return {
        "message": f"Re-numbered {updated} episodes, renamed {renamed} files on disk",
        "updated": updated,
        "renamed": renamed,
    }


@router.post("/{channel_id}/scan", status_code=202)
async def trigger_scan(channel_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")

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

    # Find all pending videos not already in queue
    subquery = select(DownloadQueue.video_id)
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
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
        for ext in [".mp4", ".nfo", "-thumb.jpg", ".jpg", ".info.json"]:
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
                if os.path.exists(video.file_path):
                    os.remove(video.file_path)
                    # Also remove .nfo if present
                    nfo_path = video.file_path.rsplit(".", 1)[0] + ".nfo"
                    if os.path.exists(nfo_path):
                        os.remove(nfo_path)
                    deleted += 1
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
    """Scan existing videos and mark any with duration <= 60s as shorts."""
    result = await db.execute(
        select(Video)
        .where(Video.channel_id == channel_id)
        .where(Video.is_short == False)
    )
    videos = result.scalars().all()

    detected = 0
    for video in videos:
        if video.duration and video.duration <= 60:
            video.is_short = True
            detected += 1

    await db.commit()
    return {"message": f"Detected {detected} shorts", "detected": detected}


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
