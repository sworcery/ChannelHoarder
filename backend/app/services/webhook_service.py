"""Push notifications via Telegram and Pushover.

Reads provider credentials from AppSettings. All failures are logged
but silently swallowed  - webhook errors must never disrupt downloads.
"""

import json
import logging
from typing import Any

import httpx
from sqlalchemy import select

from app.database import async_session
from app.models import AppSetting

logger = logging.getLogger(__name__)

# Map event types to human-readable titles
EVENT_TITLES: dict[str, str] = {
    "download_complete": "✅ Download Complete",
    "download_failed": "❌ Download Failed",
    "cookies_expired": "🔑 Cookies Expired",
    "cookies_refreshed": "🔑 Cookies Refreshed",
    "health_alert": "⚠️ Health Alert",
    "ytdlp_updated": "🔄 yt-dlp Updated",
    "critical_alert": "🚨 Critical Alert",
    "review_required": "👀 Review Required",
}


def _format_message(event_type: str, payload: dict[str, Any]) -> str:
    """Build a short plain-text message for push notifications."""
    title = EVENT_TITLES.get(event_type, event_type)
    parts = [title]

    if event_type == "download_complete":
        parts.append(f"{payload.get('title', 'Unknown')}")
        if payload.get("file_size"):
            parts.append(f"Size: {payload['file_size']}")

    elif event_type == "download_failed":
        parts.append(f"{payload.get('title', 'Unknown')}")
        if payload.get("summary"):
            parts.append(payload["summary"])
        if payload.get("suggested_fix"):
            parts.append(f"Fix: {payload['suggested_fix']}")

    elif event_type in ("cookies_expired", "cookies_refreshed"):
        if payload.get("message"):
            parts.append(payload["message"])

    elif event_type == "health_alert":
        comp = payload.get("component", "")
        status = payload.get("status", "")
        msg = payload.get("message", "")
        parts.append(f"{comp}: {status}" if comp else msg)

    elif event_type == "critical_alert":
        parts.append(payload.get("message", ""))

    elif event_type == "review_required":
        parts.append(f"{payload.get('channel', '')}: {payload.get('title', 'Unknown')}")
        if payload.get("duration"):
            parts.append(f"Duration: {payload['duration']}")
        parts.append("Queue manually in the web UI if you want this video.")

    else:
        # Generic fallback
        if payload.get("message"):
            parts.append(payload["message"])

    return "\n".join(p for p in parts if p)


async def _get_webhook_settings() -> dict[str, str]:
    """Read all webhook-related AppSettings."""
    keys = [
        "telegram_bot_token",
        "telegram_chat_id",
        "pushover_app_token",
        "pushover_user_key",
        "webhook_events",
    ]
    settings: dict[str, str] = {}
    async with async_session() as db:
        for key in keys:
            result = await db.execute(
                select(AppSetting).where(AppSetting.key == key)
            )
            row = result.scalar_one_or_none()
            if row:
                try:
                    settings[key] = json.loads(row.value)
                except (json.JSONDecodeError, TypeError):
                    settings[key] = row.value
    return settings


def _is_event_enabled(cfg: dict[str, str], event_type: str) -> bool:
    """Check if this event type should fire a notification."""
    events = cfg.get("webhook_events", "")
    if not events:
        # Default: send all events
        return True
    if isinstance(events, list):
        return event_type in events
    # Comma-separated string fallback
    return event_type in str(events).split(",")


async def _send_telegram(token: str, chat_id: str, text: str) -> None:
    """POST a message to the Telegram Bot API."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if resp.status_code != 200:
            logger.warning("Telegram webhook failed: %s %s", resp.status_code, resp.text[:200])


async def _send_pushover(app_token: str, user_key: str, title: str, message: str) -> None:
    """POST a message to the Pushover API."""
    url = "https://api.pushover.net/1/messages.json"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, data={
            "token": app_token,
            "user": user_key,
            "title": title,
            "message": message,
        })
        if resp.status_code != 200:
            logger.warning("Pushover webhook failed: %s %s", resp.status_code, resp.text[:200])


async def send_notification(event_type: str, payload: dict[str, Any]) -> None:
    """Dispatch push notification to all configured providers.

    Called from NotificationService.broadcast()  - must never raise.
    """
    try:
        cfg = await _get_webhook_settings()
    except Exception:
        logger.debug("Could not read webhook settings", exc_info=True)
        return

    if not _is_event_enabled(cfg, event_type):
        return

    text = _format_message(event_type, payload)
    title = EVENT_TITLES.get(event_type, "ChannelHoarder")

    # Telegram
    token = cfg.get("telegram_bot_token", "")
    chat_id = cfg.get("telegram_chat_id", "")
    if token and chat_id:
        try:
            await _send_telegram(str(token), str(chat_id), text)
        except Exception:
            logger.debug("Telegram notification failed", exc_info=True)

    # Pushover
    app_token = cfg.get("pushover_app_token", "")
    user_key = cfg.get("pushover_user_key", "")
    if app_token and user_key:
        try:
            await _send_pushover(str(app_token), str(user_key), title, text)
        except Exception:
            logger.debug("Pushover notification failed", exc_info=True)


async def send_test_notification(provider: str) -> dict[str, Any]:
    """Send a test message to verify provider config. Returns result dict."""
    cfg = await _get_webhook_settings()
    test_text = "🧪 ChannelHoarder test notification  - your webhook is working!"

    if provider == "telegram":
        token = cfg.get("telegram_bot_token", "")
        chat_id = cfg.get("telegram_chat_id", "")
        if not token or not chat_id:
            return {"success": False, "error": "Telegram bot token or chat ID not configured"}
        try:
            await _send_telegram(str(token), str(chat_id), test_text)
            return {"success": True, "message": "Test message sent to Telegram"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    elif provider == "pushover":
        app_token = cfg.get("pushover_app_token", "")
        user_key = cfg.get("pushover_user_key", "")
        if not app_token or not user_key:
            return {"success": False, "error": "Pushover app token or user key not configured"}
        try:
            await _send_pushover(str(app_token), str(user_key), "ChannelHoarder", test_text)
            return {"success": True, "message": "Test message sent to Pushover"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    return {"success": False, "error": f"Unknown provider: {provider}"}
