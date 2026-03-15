import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.deps import get_db
from app.models import Channel, DownloadLog, Video
from app.schemas import DiagnosticReport, DownloadLogResponse
from app.services.diagnostics_service import DiagnosticsService
from app.services.ytdlp_service import YtdlpService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/health")
async def health_check():
    return {"status": "healthy", "version": settings.APP_VERSION}


@router.get("/ytdlp/version")
async def get_ytdlp_version():
    ytdlp = YtdlpService()
    return {"version": ytdlp.get_version()}


@router.post("/ytdlp/update")
async def update_ytdlp():
    ytdlp = YtdlpService()
    success, message = ytdlp.update()
    return {"success": success, "message": message, "version": ytdlp.get_version()}


@router.get("/diagnostics", response_model=DiagnosticReport)
async def get_diagnostics(db: AsyncSession = Depends(get_db)):
    service = DiagnosticsService(db)
    return await service.generate_report()


@router.get("/diagnostics/{video_id}")
async def get_video_diagnostics(video_id: int, db: AsyncSession = Depends(get_db)):
    service = DiagnosticsService(db)
    report = await service.generate_video_report(video_id)
    if "error" in report:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=report["error"])
    return report


@router.get("/logs")
async def get_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    error_code: Optional[str] = None,
    channel_id: Optional[int] = None,
    event: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    query = select(DownloadLog).order_by(DownloadLog.created_at.desc())

    if error_code:
        query = query.where(DownloadLog.error_code == error_code)
    if event:
        query = query.where(DownloadLog.event == event)
    if search:
        query = query.where(DownloadLog.message.ilike(f"%{search}%"))
    if channel_id:
        video_ids = select(Video.id).where(Video.channel_id == channel_id)
        query = query.where(DownloadLog.video_id.in_(video_ids))

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    result = await db.execute(query.offset(skip).limit(limit))
    logs = result.scalars().all()

    # Enrich with video/channel info
    items = []
    for log in logs:
        video_result = await db.execute(select(Video).where(Video.id == log.video_id))
        video = video_result.scalar_one_or_none()
        channel_name = None
        if video:
            ch_result = await db.execute(select(Channel).where(Channel.id == video.channel_id))
            ch = ch_result.scalar_one_or_none()
            channel_name = ch.channel_name if ch else None

        items.append(DownloadLogResponse(
            id=log.id,
            video_id=log.video_id,
            event=log.event,
            error_code=log.error_code,
            message=log.message,
            details=log.details,
            created_at=log.created_at,
            video_title=video.title if video else None,
            channel_name=channel_name,
        ))

    return {"items": items, "total": total or 0, "skip": skip, "limit": limit}


@router.post("/scan-all", status_code=202)
async def trigger_scan_all(db: AsyncSession = Depends(get_db)):
    from app.services.channel_service import ChannelService

    result = await db.execute(select(Channel).where(Channel.enabled == True))
    channels = result.scalars().all()

    total_new = 0
    errors = []
    for channel in channels:
        try:
            service = ChannelService(db)
            new_count = await service.scan_channel(channel)
            total_new += new_count
        except Exception as e:
            errors.append({"channel": channel.channel_name, "error": str(e)})

    return {
        "message": f"Scanned {len(channels)} channels. Found {total_new} new videos.",
        "channels_scanned": len(channels),
        "new_videos": total_new,
        "errors": errors,
    }
