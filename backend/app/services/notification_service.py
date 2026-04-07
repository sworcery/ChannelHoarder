import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)

# Events that should NOT trigger push notifications (too noisy / internal)
_SKIP_WEBHOOK_EVENTS = {"download_progress", "queue_update"}


class NotificationService:
    """Manages WebSocket connections and broadcasts messages."""

    _connections: list[WebSocket] = []

    @classmethod
    def add_connection(cls, ws: WebSocket):
        cls._connections.append(ws)

    @classmethod
    def remove_connection(cls, ws: WebSocket):
        if ws in cls._connections:
            cls._connections.remove(ws)

    @classmethod
    async def broadcast(cls, event_type: str, payload: dict[str, Any]):
        """Broadcast a message to all connected WebSocket clients and push providers."""
        message = json.dumps({"type": event_type, "payload": payload})
        disconnected = []
        for ws in cls._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            cls.remove_connection(ws)

        # Dispatch to push notification providers (fire-and-forget)
        if event_type not in _SKIP_WEBHOOK_EVENTS:
            try:
                from app.services.webhook_service import send_notification
                asyncio.create_task(send_notification(event_type, payload))
            except Exception:
                pass  # Never let webhook errors disrupt the app
