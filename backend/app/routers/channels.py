import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
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
    query = select(Channel).order_by(Channel.channel_name)
    if search:
        query = query.where(Channel.channel_name.ilike(f"%{search}%"))
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return result.scalars().all()


@router.post("/", response_model=ChannelResponse, status_code=201)
async def add_channel(
    body: ChannelCreate,
    db: AsyncSession = Depends(get_db),
):
    service = ChannelService(db)
    try:
        channel = await service.add_channel(body)
        return channel
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("Failed to add channel: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to add channel: {e}")


@router.get("/{channel_id}", response_model=ChannelResponse)
async def get_channel(channel_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Channel).where(Channel.id == channel_id))
    channel = result.scalar_one_or_none()
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found")
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
        query = query.where(Video.title.ilike(f"%{search}%"))

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
        .where(Video.status.in_(["pending", "failed"]))
    )
    videos = result.scalars().all()

    skipped = 0
    for video in videos:
        # Remove from queue if present
        queue_result = await db.execute(
            select(DownloadQueue).where(DownloadQueue.video_id == video.id)
        )
        queue_entry = queue_result.scalar_one_or_none()
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


@router.post("/{channel_id}/import/scan")
async def scan_for_import(
    channel_id: int,
    body: ImportScanRequest,
    db: AsyncSession = Depends(get_db),
):
    """Scan a folder for video files that match un-downloaded videos for this channel."""
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
