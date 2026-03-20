from datetime import datetime, date, timezone
from typing import Optional

from pydantic import BaseModel, Field, model_serializer


class UTCBaseModel(BaseModel):
    """Base model that ensures all naive datetimes are serialized as UTC (with Z suffix)."""

    model_config = {"from_attributes": True}

    def model_post_init(self, __context) -> None:
        """Attach UTC timezone to any naive datetime fields."""
        for field_name in self.model_fields:
            value = getattr(self, field_name, None)
            if isinstance(value, datetime) and value.tzinfo is None:
                object.__setattr__(self, field_name, value.replace(tzinfo=timezone.utc))


# --- Channel Schemas ---
class ChannelCreate(BaseModel):
    url: str = Field(..., description="Channel URL (YouTube, Rumble, Twitch, etc.)")
    quality: str = Field(default="best", pattern="^(best|1080p|720p|480p)$")
    naming_template: Optional[str] = None
    download_dir: Optional[str] = Field(default=None, description="Custom download directory (overrides global default)")
    enabled: bool = True


class ChannelUpdate(BaseModel):
    quality: Optional[str] = Field(default=None, pattern="^(best|1080p|720p|480p)$")
    naming_template: Optional[str] = None
    download_dir: Optional[str] = None
    check_schedule: Optional[str] = None
    enabled: Optional[bool] = None


class ChannelResponse(UTCBaseModel):
    id: int
    channel_id: str
    channel_name: str
    channel_url: str
    platform: str = "youtube"
    thumbnail_url: Optional[str]
    banner_url: Optional[str]
    description: Optional[str]
    quality: str
    naming_template: Optional[str]
    download_dir: Optional[str]
    check_schedule: Optional[str]
    enabled: bool
    last_scanned_at: Optional[datetime]
    total_videos: int
    downloaded_count: int
    health_status: str
    last_error_code: Optional[str]
    created_at: datetime
    updated_at: datetime


# --- Video Schemas ---
class VideoResponse(UTCBaseModel):
    id: int
    video_id: str
    channel_id: int
    channel_name: Optional[str] = None
    title: str
    upload_date: date
    duration: Optional[int]
    thumbnail_url: Optional[str]
    season: int
    episode: int
    status: str
    file_path: Optional[str]
    file_size: Optional[int]
    quality_downloaded: Optional[str]
    error_code: Optional[str]
    error_message: Optional[str]
    error_details: Optional[str]
    retry_count: int
    discovered_at: datetime
    downloaded_at: Optional[datetime]


class VideoSummary(UTCBaseModel):
    """Lightweight video schema for queue listings — omits large text blobs."""
    id: int
    video_id: str
    channel_id: int
    channel_name: Optional[str] = None
    title: str
    upload_date: date
    duration: Optional[int]
    thumbnail_url: Optional[str]
    status: str
    error_code: Optional[str]
    error_message: Optional[str]
    retry_count: int


# --- Download Queue Schemas ---
class QueueAdd(BaseModel):
    video_id: int
    priority: int = 0


class BulkQueueRemove(BaseModel):
    queue_ids: list[int] = Field(..., min_length=1, max_length=500)


# --- Import Schemas ---
class ImportScanRequest(BaseModel):
    folder_path: str = Field(..., min_length=1)
    threshold: float = Field(default=75.0, ge=50.0, le=100.0, description="Match confidence threshold (percentage)")


class ImportMatch(BaseModel):
    file_path: str
    matched_video_id: int


class ImportConfirmRequest(BaseModel):
    matches: list[ImportMatch] = Field(..., min_length=1)


class QueueEntryResponse(UTCBaseModel):
    id: int
    video_id: int
    priority: int
    queued_at: datetime
    started_at: Optional[datetime]
    progress_percent: float
    speed_bps: Optional[int]
    eta_seconds: Optional[int]
    video: VideoSummary


# --- Dashboard Schemas ---
class DashboardStats(UTCBaseModel):
    total_channels: int
    active_channels: int
    total_videos_known: int
    total_downloaded: int
    total_failed: int
    total_pending: int
    queue_length: int
    storage_used_bytes: int
    storage_used_formatted: str
    pot_status: str
    cookies_status: str
    api_key_configured: bool
    ytdlp_version: str
    last_scan_at: Optional[datetime]
    active_downloads: int
    cookies_expired: bool = False


# --- Settings Schemas ---
class SettingsUpdate(BaseModel):
    default_quality: Optional[str] = None
    global_schedule_cron: Optional[str] = None
    download_delay_min: Optional[int] = None
    download_delay_max: Optional[int] = None
    jitter_enabled: Optional[bool] = None
    max_concurrent_downloads: Optional[int] = None
    max_retries: Optional[int] = None
    user_agent_rotation: Optional[bool] = None
    youtube_api_key: Optional[str] = None
    pot_server_enabled: Optional[bool] = None
    naming_template: Optional[str] = None
    # Webhook notifications
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    pushover_app_token: Optional[str] = None
    pushover_user_key: Optional[str] = None
    webhook_events: Optional[list[str]] = None
    # Livestream / long video filter
    max_video_duration: Optional[int] = None  # Max duration in seconds (0 = disabled)
    # System
    log_level: Optional[str] = None  # debug, info, warning, error


class SettingValue(BaseModel):
    value: str


class NamingPreviewRequest(BaseModel):
    template: str
    channel_name: str = "TechChannel"
    title: str = "How to Build a PC"
    upload_date: str = "20240315"
    video_id: str = "dQw4w9WgXcQ"
    season: int = 2024
    episode: int = 3


class NamingPreviewResponse(BaseModel):
    preview_path: str
    full_path: str


# --- Auth Schemas ---
class AuthStatus(BaseModel):
    pot_status: str
    pot_message: Optional[str]
    cookies_status: str
    cookies_message: Optional[str]
    cookies_age_hours: Optional[float] = None
    api_key_configured: bool
    api_key_valid: Optional[bool]


# --- Diagnostics Schemas ---
class ErrorDiagnosis(BaseModel):
    code: str
    summary: str
    explanation: str
    suggested_fix: str
    retry_strategy: str
    severity: str
    raw_log: Optional[str]
    system_context: dict


class DiagnosticReport(UTCBaseModel):
    generated_at: datetime
    app_version: str
    ytdlp_version: str
    pot_status: str
    cookies_status: str
    api_key_configured: bool
    disk_free_bytes: int
    disk_free_formatted: str
    total_channels: int
    total_downloads: int
    total_failed: int
    recent_errors: list[dict]
    system_info: dict


class DownloadLogResponse(UTCBaseModel):
    id: int
    video_id: int
    event: str
    error_code: Optional[str]
    message: Optional[str]
    details: Optional[str]
    created_at: datetime
    video_title: Optional[str] = None
    channel_name: Optional[str] = None


# --- WebSocket Schemas ---
class WSMessage(BaseModel):
    type: str
    payload: dict


# --- Pagination ---
class PaginatedResponse(BaseModel):
    items: list
    total: int
    skip: int
    limit: int
