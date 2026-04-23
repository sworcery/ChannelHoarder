import logging
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
router = APIRouter()

def _get_allowed_origins() -> list[str] | None:
    if settings.CORS_ORIGINS:
        origins = [o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()]
        return origins or None
    return None


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    allowed = _get_allowed_origins()
    if allowed:
        origin = websocket.headers.get("origin", "")
        if origin and origin not in allowed:
            await websocket.close(code=4003)
            logger.warning("WebSocket rejected: origin %s not in allowed list", origin)
            return

    await websocket.accept()
    NotificationService.add_connection(websocket)
    logger.info("WebSocket client connected")

    try:
        while True:
            data = await websocket.receive_text()
    except WebSocketDisconnect:
        NotificationService.remove_connection(websocket)
        logger.info("WebSocket client disconnected")
    except Exception:
        NotificationService.remove_connection(websocket)
