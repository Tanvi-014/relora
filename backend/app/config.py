from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Hermes Webhook Middleware"
    ENVIRONMENT: str = "development"  # development | production
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hermes"
    SYNC_DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/hermes"

    # DB connection pool
    DB_POOL_SIZE: int = 20
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 3600

    # Worker
    WORKER_CONCURRENCY: int = 10
    WORKER_POLL_INTERVAL_SECONDS: float = 1.0
    DEFAULT_MAX_RETRIES: int = 5
    HTTP_CLIENT_TIMEOUT_SECONDS: float = 10.0
    BACKOFF_BASE_SECONDS: int = 30

    # Security
    HERMES_API_KEY: str = ""
    HERMES_API_KEYS: str = ""
    ALLOW_PRIVATE_DESTINATIONS: bool = False
    DESTINATION_HOST_ALLOWLIST: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    HERMES_WEBHOOK_SECRET: str = ""
    SIGNATURE_TOLERANCE_SECONDS: int = 300

    # JWT — MUST be overridden in production
    JWT_SECRET: str = "change-this-secret-in-production-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 7

    # Cookie settings
    COOKIE_SECURE: bool = False   # True in production (HTTPS only)
    COOKIE_DOMAIN: str = ""       # empty = current domain

    # Production
    FORCE_HTTPS: bool = False
    RATE_LIMIT_PER_MINUTE: int = 60

    # Schema management — NEVER True in production
    AUTO_CREATE_TABLES: bool = False

    # AI features
    ANTHROPIC_API_KEY: str = ""
    ENABLE_AI_FEATURES: bool = False

    # Feature flags
    ENABLE_JS_TRANSFORMS: bool = False   # requires Deno in worker image
    ENABLE_SIMULATOR: bool = True

    # Standard Webhooks signing (whsec_ prefixed base64)
    STANDARD_WEBHOOKS_SECRET: str = ""

    # Email alerts via Resend
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "alerts@hermes.example.com"

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def destination_host_allowlist(self) -> List[str]:
        return [
            host.strip().lower()
            for host in self.DESTINATION_HOST_ALLOWLIST.split(",")
            if host.strip()
        ]

    @property
    def api_key_tenants(self) -> Dict[str, str]:
        tenants: Dict[str, str] = {}
        for item in self.HERMES_API_KEYS.split(","):
            if not item.strip() or ":" not in item:
                continue
            tenant_id, api_key = item.split(":", 1)
            if tenant_id.strip() and api_key.strip():
                tenants[api_key.strip()] = tenant_id.strip()
        if self.HERMES_API_KEY:
            tenants[self.HERMES_API_KEY] = "default"
        return tenants

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
