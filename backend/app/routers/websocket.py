import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.notification_service import NotificationService

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/progress")
async def websocket_progress(websocket: WebSocket):
    await websocket.accept()
    NotificationService.add_connection(websocket)
    logger.info("WebSocket client connected")

    try:
        while True:
            # Keep connection alive, listen for client messages
            data = await websocket.receive_text()
            # Client can send ping/pong or commands if needed
    except WebSocketDisconnect:
        NotificationService.remove_connection(websocket)
        logger.info("WebSocket client disconnected")
    except Exception:
        NotificationService.remove_connection(websocket)
