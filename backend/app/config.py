import os
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # App Settings
    APP_NAME: str = "Hermes Webhook Middleware"
    DEBUG: bool = False
    
    # Database Settings
    # Supports running locally (localhost) or inside docker (db)
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL", 
        "postgresql+asyncpg://postgres:postgres@localhost:5432/hermes"
    )
    
    # Sync DB URL for database initialization / scripts
    SYNC_DATABASE_URL: str = os.getenv(
        "SYNC_DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/hermes"
    )

    # Worker Settings
    WORKER_CONCURRENCY: int = int(os.getenv("WORKER_CONCURRENCY", "5"))
    WORKER_POLL_INTERVAL_SECONDS: float = float(os.getenv("WORKER_POLL_INTERVAL_SECONDS", "1.0"))
    DEFAULT_MAX_RETRIES: int = int(os.getenv("DEFAULT_MAX_RETRIES", "5"))
    HTTP_CLIENT_TIMEOUT_SECONDS: float = float(os.getenv("HTTP_CLIENT_TIMEOUT_SECONDS", "5.0"))
    
    # Backoff configuration (exponential backoff)
    BACKOFF_BASE_SECONDS: int = int(os.getenv("BACKOFF_BASE_SECONDS", "30"))

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

settings = Settings()
