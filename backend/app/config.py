from pydantic_settings import BaseSettings
from pathlib import Path


class Settings(BaseSettings):
    APP_NAME: str = "ChannelHoarder"
    APP_VERSION: str = "0.6.0"
    DEBUG: bool = False
    LOG_LEVEL: str = "info"

    CONFIG_DIR: str = "/config"
    DOWNLOAD_DIR: str = "/downloads"
    DATABASE_URL: str = ""

    YOUTUBE_API_KEY: str = ""
    POT_SERVER_URL: str = "http://127.0.0.1:4416"
    POT_SERVER_ENABLED: bool = True

    DEFAULT_QUALITY: str = "best"
    DEFAULT_SCAN_CRON: str = "0 3 * * *"
    MAX_CONCURRENT_DOWNLOADS: int = 1
    MAX_RETRIES: int = 3
    DOWNLOAD_DELAY_MIN: int = 10
    DOWNLOAD_DELAY_MAX: int = 30
    JITTER_ENABLED: bool = True
    USER_AGENT_ROTATION: bool = True

    PUID: int = 1000
    PGID: int = 1000
    TZ: str = "America/New_York"

    model_config = {"env_prefix": "", "case_sensitive": True}

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
    def has_cookies(self) -> bool:
        return self.cookies_path.exists()


settings = Settings()
