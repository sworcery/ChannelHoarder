import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


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
        """Broadcast a message to all connected WebSocket clients."""
        message = json.dumps({"type": event_type, "payload": payload})
        disconnected = []
        for ws in cls._connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)

        for ws in disconnected:
            cls.remove_connection(ws)
