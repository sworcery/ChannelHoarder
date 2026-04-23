from datetime import datetime, date
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Channel(Base):
    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    channel_name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel_url: Mapped[str] = mapped_column(String(512), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="youtube", server_default="youtube")
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    banner_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quality: Mapped[str] = mapped_column(String(10), nullable=False, default="best")
    quality_cutoff: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    min_video_duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Seconds; skip videos shorter than this
    naming_template: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    download_dir: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    check_schedule: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    include_shorts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_livestreams: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_download: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_scanned_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    next_scan_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    total_videos: Mapped[int] = mapped_column(Integer, default=0)
    downloaded_count: Mapped[int] = mapped_column(Integer, default=0)
    health_status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    last_error_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )

    videos: Mapped[list["Video"]] = relationship("Video", back_populates="channel", cascade="all, delete-orphan", lazy="noload")


class Video(Base):
    __tablename__ = "videos"
    __table_args__ = (
        Index("ix_videos_channel_season", "channel_id", "season"),
        Index("ix_videos_status_downloaded_at", "status", "downloaded_at"),
        Index("ix_videos_channel_status_monitored", "channel_id", "status", "monitored"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    channel_id: Mapped[int] = mapped_column(Integer, ForeignKey("channels.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    upload_date: Mapped[date] = mapped_column(Date, nullable=False)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    thumbnail_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending", index=True)
    is_short: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    is_livestream: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    monitored: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="1")
    file_path: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    quality_downloaded: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    downloaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())

    channel: Mapped["Channel"] = relationship("Channel", back_populates="videos", lazy="noload")
    queue_entry: Mapped[Optional["DownloadQueue"]] = relationship(
        "DownloadQueue", back_populates="video", uselist=False, cascade="all, delete-orphan", lazy="noload"
    )
    logs: Mapped[list["DownloadLog"]] = relationship(
        "DownloadLog", back_populates="video", cascade="all, delete-orphan", lazy="noload"
    )

    @property
    def channel_name(self) -> str | None:
        return self.channel.channel_name if self.channel else None

    @property
    def platform(self) -> str:
        return self.channel.platform if self.channel else "youtube"


class DownloadQueue(Base):
    __tablename__ = "download_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id"), unique=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=0, index=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0.0)
    speed_bps: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    eta_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    target_quality: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    estimated_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    video: Mapped["Video"] = relationship("Video", back_populates="queue_entry")


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DownloadLog(Base):
    __tablename__ = "download_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(Integer, ForeignKey("videos.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(32), nullable=False)
    error_code: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    details: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), index=True)

    video: Mapped["Video"] = relationship("Video", back_populates="logs")


class SystemHealthLog(Base):
    __tablename__ = "system_health_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    component: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    checked_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
