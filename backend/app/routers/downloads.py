import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.deps import get_db
from app.models import Channel, DownloadLog, DownloadQueue, Video
from app.schemas import QueueAdd, QueueEntryResponse, VideoResponse, DownloadLogResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/queue", response_model=list[QueueEntryResponse])
async def get_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .order_by(DownloadQueue.priority.desc(), DownloadQueue.queued_at.asc())
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().unique().all()


@router.post("/queue", status_code=201)
async def add_to_queue(body: QueueAdd, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.id == body.video_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")

    # Check if already queued
    existing = await db.execute(
        select(DownloadQueue).where(DownloadQueue.video_id == body.video_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Video already in queue")

    video.status = "queued"
    db.add(DownloadQueue(video_id=body.video_id, priority=body.priority))
    await db.commit()
    return {"message": "Video added to queue"}


@router.delete("/queue/{queue_id}", status_code=204)
async def remove_from_queue(queue_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(DownloadQueue).where(DownloadQueue.id == queue_id))
    entry = result.scalar_one_or_none()
    if not entry:
        raise HTTPException(status_code=404, detail="Queue entry not found")

    # Reset video status
    result = await db.execute(select(Video).where(Video.id == entry.video_id))
    video = result.scalar_one_or_none()
    if video and video.status in ("queued", "downloading"):
        video.status = "pending"

    await db.delete(entry)
    await db.commit()


@router.get("/history")
async def get_history(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    channel_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = None,
    error_code: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(Video).order_by(Video.downloaded_at.desc().nullslast(), Video.created_at.desc())

    if channel_id:
        query = query.where(Video.channel_id == channel_id)
    if status:
        query = query.where(Video.status == status)
    if search:
        query = query.where(Video.title.ilike(f"%{search}%"))
    if error_code:
        query = query.where(Video.error_code == error_code)

    # Get total count
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query)

    result = await db.execute(query.offset(skip).limit(limit))
    videos = result.scalars().all()

    return {
        "items": [VideoResponse.model_validate(v) for v in videos],
        "total": total or 0,
        "skip": skip,
        "limit": limit,
    }


@router.post("/retry/{video_id}", status_code=202)
async def retry_download(video_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.id == video_id))
    video = result.scalar_one_or_none()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in ("failed", "skipped"):
        raise HTTPException(status_code=400, detail=f"Cannot retry video with status '{video.status}'")

    video.status = "queued"
    video.error_code = None
    video.error_message = None
    video.error_details = None

    # Check if already in queue
    existing = await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == video.id))
    if not existing.scalar_one_or_none():
        db.add(DownloadQueue(video_id=video.id))

    await db.commit()
    return {"message": f"Video '{video.title}' queued for retry"}


@router.post("/retry-all-failed", status_code=202)
async def retry_all_failed(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Video).where(Video.status == "failed"))
    videos = result.scalars().all()

    count = 0
    for video in videos:
        video.status = "queued"
        video.error_code = None
        video.error_message = None

        existing = await db.execute(select(DownloadQueue).where(DownloadQueue.video_id == video.id))
        if not existing.scalar_one_or_none():
            db.add(DownloadQueue(video_id=video.id))
            count += 1

    await db.commit()
    return {"message": f"Queued {count} failed videos for retry", "count": count}


@router.get("/active")
async def get_active_downloads(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(DownloadQueue)
        .options(joinedload(DownloadQueue.video))
        .where(DownloadQueue.started_at.isnot(None))
    )
    entries = result.scalars().unique().all()
    return [QueueEntryResponse.model_validate(e) for e in entries]
