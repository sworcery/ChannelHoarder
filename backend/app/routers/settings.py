import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import get_db
from app.models import AppSetting
from app.schemas import SettingsUpdate, NamingPreviewRequest, NamingPreviewResponse
from app.services.naming_service import preview_naming, DEFAULT_TEMPLATE

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def get_all_settings(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSetting))
    settings_list = result.scalars().all()
    return {s.key: json.loads(s.value) for s in settings_list}


@router.put("/")
async def update_settings(body: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        result = await db.execute(select(AppSetting).where(AppSetting.key == key))
        setting = result.scalar_one_or_none()
        if setting:
            setting.value = json.dumps(value)
        else:
            db.add(AppSetting(key=key, value=json.dumps(value)))

    await db.commit()
    return {"message": "Settings updated", "updated": list(update_data.keys())}


@router.get("/{key}")
async def get_setting(key: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if not setting:
        raise HTTPException(status_code=404, detail=f"Setting '{key}' not found")
    return {"key": key, "value": json.loads(setting.value)}


@router.put("/{key}")
async def update_setting(key: str, value: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AppSetting).where(AppSetting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = json.dumps(value)
    else:
        db.add(AppSetting(key=key, value=json.dumps(value)))

    await db.commit()
    return {"key": key, "value": value}


@router.post("/naming/preview", response_model=NamingPreviewResponse)
async def preview_naming_template(body: NamingPreviewRequest):
    template = body.template or DEFAULT_TEMPLATE
    result = preview_naming(
        template=template,
        channel_name=body.channel_name,
        title=body.title,
        upload_date=body.upload_date,
        video_id=body.video_id,
        season=body.season,
        episode=body.episode,
    )
    return NamingPreviewResponse(
        preview_path=result,
        full_path=f"/downloads/{result}.mp4",
    )
