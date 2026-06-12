from typing import Dict, List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Relora"
    ENVIRONMENT: str = "development"  # development | production
    DEBUG: bool = False

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/relora"
    SYNC_DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/relora"

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
    RELORA_API_KEY: str = ""
    RELORA_API_KEYS: str = ""
    ALLOW_PRIVATE_DESTINATIONS: bool = False
    DESTINATION_HOST_ALLOWLIST: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""
    RELORA_WEBHOOK_SECRET: str = ""
    SIGNATURE_TOLERANCE_SECONDS: int = 300

    # JWT — MUST be overridden in production
    JWT_SECRET: str = "change-this-secret-in-production-use-openssl-rand-hex-32"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_DAYS: int = 1

    # Cookie settings
    COOKIE_SECURE: bool = False   # True in production (HTTPS only)
    COOKIE_DOMAIN: str = ""       # empty = current domain

    # Production
    FORCE_HTTPS: bool = False
    RATE_LIMIT_PER_MINUTE: int = 60
    # Auth endpoint rate limit — stricter than ingest (per-IP, per minute)
    AUTH_RATE_LIMIT_PER_MINUTE: int = 10
    # Per-tenant monthly event quota. 0 = unlimited (self-hosted default).
    MONTHLY_EVENT_QUOTA: int = 0

    # CORS — comma-separated list of allowed origins.
    # Leave empty (default) when frontend and API are co-served (nginx reverse proxy).
    # Set to e.g. "https://app.example.com,https://staging.example.com" when the
    # dashboard is on a different domain than the API.
    # WARNING: never use "*" with allow_credentials=True — it violates the CORS spec
    # and browsers will reject credentialed requests silently.
    CORS_ORIGINS: str = ""

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
    RESEND_FROM_EMAIL: str = "alerts@relora.example.com"

    # Email verification
    APP_BASE_URL: str = "http://localhost:8000"          # public-facing base URL for links
    # Internal URL for service-to-service calls (e.g. http://relora-api:8000 in Docker).
    # If empty, falls back to APP_BASE_URL. Used as the sandbox destination URL so the
    # delivery worker can reach the sandbox inbox endpoint from within the container network.
    INTERNAL_API_URL: str = ""
    EMAIL_VERIFICATION_REQUIRED: bool = False             # soft gate — warn but don't block
    EMAIL_VERIFICATION_EXPIRY_HOURS: int = 24

    # SMS alerts via Twilio
    TWILIO_ACCOUNT_SID: str = ""
    TWILIO_AUTH_TOKEN: str = ""
    TWILIO_FROM_NUMBER: str = ""                         # E.164 format: +15005550006

    # Error tracking
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = ""          # overrides ENVIRONMENT label sent to Sentry
    SENTRY_TRACES_SAMPLE_RATE: float = 0.1

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def internal_api_url(self) -> str:
        return self.INTERNAL_API_URL or self.APP_BASE_URL

    @property
    def cors_origins(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

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
        for item in self.RELORA_API_KEYS.split(","):
            if not item.strip() or ":" not in item:
                continue
            tenant_id, api_key = item.split(":", 1)
            if tenant_id.strip() and api_key.strip():
                tenants[api_key.strip()] = tenant_id.strip()
        if self.RELORA_API_KEY:
            tenants[self.RELORA_API_KEY] = "default"
        return tenants

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
