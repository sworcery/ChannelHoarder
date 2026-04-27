import os

from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "ChannelHoarder"
    APP_VERSION: str = "1.7.34"
    DEBUG: bool = False
    LOG_LEVEL: str = "info"

    CONFIG_DIR: str = "/config"
    DOWNLOAD_DIR: str = "/downloads"
    STANDALONE_DOWNLOAD_DIR: str = ""  # Separate directory for standalone downloads (defaults to DOWNLOAD_DIR/Standalone Downloads)
    EXTRA_DOWNLOAD_DIRS: str = ""  # Comma-separated additional allowed download paths (e.g. /cartoons,/media)
    COOKIE_WATCH_DIR: str = "/cookies"
    DATABASE_URL: str = ""

    YOUTUBE_API_KEY: str = ""
    POT_SERVER_URL: str = "http://127.0.0.1:4416"
    POT_SERVER_ENABLED: bool = True
    YTDLP_PLAYER_CLIENT: str = "default"

    DEFAULT_QUALITY: str = "best"
    DEFAULT_SCAN_CRON: str = "0 3 * * *"
    MAX_CONCURRENT_DOWNLOADS: int = 1
    MAX_RETRIES: int = 3
    DOWNLOAD_DELAY_MIN: int = 10
    DOWNLOAD_DELAY_MAX: int = 30
    JITTER_ENABLED: bool = True
    USER_AGENT_ROTATION: bool = True
    CORS_ORIGINS: str = ""

    PUID: int = 1000
    PGID: int = 1000
    TZ: str = "America/New_York"

    model_config = {"env_prefix": "", "case_sensitive": True}

    @property
    def standalone_dir(self) -> str:
        if self.STANDALONE_DOWNLOAD_DIR:
            return self.STANDALONE_DOWNLOAD_DIR
        return "/StandaloneDownloads"

    @property
    def allowed_download_roots(self) -> list[str]:
        roots = [self.DOWNLOAD_DIR]
        if self.EXTRA_DOWNLOAD_DIRS:
            roots.extend(d.strip() for d in self.EXTRA_DOWNLOAD_DIRS.split(",") if d.strip())
        return roots

    @property
    def db_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        db_path = Path(self.CONFIG_DIR) / "archiver.db"
        return f"sqlite+aiosqlite:///{db_path}"

    @property
    def cookies_path(self) -> Path:
        return Path(self.CONFIG_DIR) / "cookies.txt"

    @property
    def has_youtube_api_key(self) -> bool:
        return bool(self.YOUTUBE_API_KEY)

    @property
    def ytdlp_cache_dir(self) -> Path:
        return Path(self.CONFIG_DIR) / "ytdlp-cache"

    @property
    def has_cookies(self) -> bool:
        return self.cookies_path.exists()


settings = Settings()

# Load persisted YouTube API key if not set via environment
if not settings.YOUTUBE_API_KEY:
    _api_key_file = Path(settings.CONFIG_DIR) / "youtube_api_key"
    if _api_key_file.exists():
        settings.YOUTUBE_API_KEY = _api_key_file.read_text().strip()

# Load persisted player client strategy
_player_client_file = Path(settings.CONFIG_DIR) / "player_client"
if _player_client_file.exists():
    settings.YTDLP_PLAYER_CLIENT = _player_client_file.read_text().strip()
