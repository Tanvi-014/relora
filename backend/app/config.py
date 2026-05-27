from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "Hermes Webhook Middleware"
    DEBUG: bool = False
    
    # Database Settings
    # Supports running locally (localhost) or inside docker (db)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hermes"
    
    # Sync DB URL for database initialization / scripts
    SYNC_DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/hermes"

    # Worker Settings
    WORKER_CONCURRENCY: int = 5
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    DEFAULT_MAX_RETRIES: int = 5
    HTTP_CLIENT_TIMEOUT_SECONDS: float = 5.0
    
    # Backoff configuration (exponential backoff)
    BACKOFF_BASE_SECONDS: int = 30

    # Security settings
    HERMES_API_KEY: str = ""
    ALLOW_PRIVATE_DESTINATIONS: bool = True
    DESTINATION_HOST_ALLOWLIST: str = ""

    # Schema management
    AUTO_CREATE_TABLES: bool = True

    @property
    def destination_host_allowlist(self) -> List[str]:
        return [
            host.strip().lower()
            for host in self.DESTINATION_HOST_ALLOWLIST.split(",")
            if host.strip()
        ]

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
