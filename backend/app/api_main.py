"""
Pure API entrypoint — no worker pool.
Run with: uvicorn app.api_main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

from app.config import settings
from app.db import init_db
from app.event_sources import router as sources_router
from app.incident_scheduler import start_incident_scheduler, stop_incident_scheduler
from app.telemetry import setup_telemetry
from app.logging_config import configure_logging

from app.routers import auth, projects, destinations, webhooks, alerts, event_types, ai_tools, dlq, simulator, consumer, system
from app.routers import slo, schema_drift, events as events_router, insights as insights_router

configure_logging()
logger = logging.getLogger("relora.api")

# ── Sentry (optional — activate by setting SENTRY_DSN) ───────────────────────
if settings.SENTRY_DSN:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration

        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            environment=settings.SENTRY_ENVIRONMENT or settings.ENVIRONMENT,
            traces_sample_rate=settings.SENTRY_TRACES_SAMPLE_RATE,
            integrations=[FastApiIntegration(), SqlalchemyIntegration()],
            send_default_pii=False,
        )
        logger.info("Sentry initialised", extra={"event": "sentry.init"})
    except ImportError:
        logger.warning("sentry-sdk not installed — SENTRY_DSN is set but Sentry is disabled")

_INSECURE_JWT_DEFAULT = "change-this-secret-in-production-use-openssl-rand-hex-32"


def _validate_production_config() -> None:
    """Fail fast if unsafe defaults are detected in production."""
    errors = []
    if settings.is_production:
        if settings.JWT_SECRET == _INSECURE_JWT_DEFAULT:
            errors.append("JWT_SECRET is still the default placeholder. Generate one with: openssl rand -hex 32")
        if len(settings.JWT_SECRET) < 32:
            errors.append("JWT_SECRET must be at least 32 characters.")
        if settings.COOKIE_SECURE is False:
            errors.append("COOKIE_SECURE must be true in production (requires HTTPS).")
        if settings.ALLOW_PRIVATE_DESTINATIONS:
            errors.append("ALLOW_PRIVATE_DESTINATIONS must be false in production (SSRF risk).")
        if settings.AUTO_CREATE_TABLES:
            errors.append("AUTO_CREATE_TABLES must be false in production.")
        if "*" in settings.CORS_ORIGINS:
            errors.append("CORS_ORIGINS must not contain '*' in production. Use explicit origin URLs.")
        if settings.RESEND_API_KEY and settings.RESEND_FROM_EMAIL.endswith("@relora.example.com"):
            errors.append("RESEND_FROM_EMAIL is still the placeholder. Set it to a verified Resend sender address.")
        if settings.EMAIL_VERIFICATION_REQUIRED and not settings.RESEND_API_KEY:
            errors.append(
                "EMAIL_VERIFICATION_REQUIRED=true but RESEND_API_KEY is not set. "
                "Users would be blocked from login with no way to verify. "
                "Set RESEND_API_KEY or set EMAIL_VERIFICATION_REQUIRED=false."
            )
    if errors:
        for e in errors:
            logger.critical("PRODUCTION CONFIG ERROR: %s", e)
        sys.exit(1)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers to every response."""
    async def dispatch(self, request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        if settings.FORCE_HTTPS:
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        return response


async def _recover_stuck_webhooks() -> None:
    """
    Reset webhooks stuck in 'processing' back to 'pending' so they will be retried.

    A webhook is considered stuck when it has been 'processing' for longer than
    10 minutes — well above the maximum possible delivery time
    (HTTP_CLIENT_TIMEOUT_SECONDS default 10s + DB overhead + 5× retry headroom).
    This covers worker crashes, OOM kills, and ungraceful pod evictions.
    """
    from app.db import async_session as _s
    async with _s() as db:
        result = await db.execute(
            text("""
            UPDATE webhooks
            SET status = 'pending',
                updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - INTERVAL '10 minutes'
            """)
        )
        await db.commit()
        if result.rowcount:
            logger.warning(
                "Recovered %d stuck webhooks on startup (processing → pending)",
                result.rowcount,
                extra={"event": "webhook.stuck.recovered", "count": result.rowcount},
            )


async def _recover_stuck_replay_jobs() -> None:
    """Reset replay jobs stuck in 'running' at startup (process crashed mid-replay)."""
    from app.db import async_session as _s
    async with _s() as db:
        result = await db.execute(
            text("""
            UPDATE replay_jobs
            SET status = 'failed',
                error_message = 'Interrupted: API process restarted while job was running',
                updated_at = NOW()
            WHERE status = 'running'
              AND updated_at < NOW() - INTERVAL '5 minutes'
            """)
        )
        await db.commit()
        if result.rowcount:
            logger.warning(
                "Recovered %d stuck replay jobs on startup", result.rowcount,
                extra={"event": "replay_job.recovered", "count": result.rowcount},
            )


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_production_config()
    setup_telemetry(service_name="relora-api")
    if settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=true, initializing tables...")
        await init_db()

    await _recover_stuck_webhooks()
    await _recover_stuck_replay_jobs()

    logger.info("Starting incident scheduler...")
    await start_incident_scheduler()

    yield

    logger.info("Stopping incident scheduler...")
    await stop_incident_scheduler()


app = FastAPI(
    title="Relora",
    description="""
**Relora** is a self-hosted, production-grade webhook relay and delivery engine.

It sits between webhook publishers (Stripe, GitHub, Shopify, internal services) and
your application, accepting events immediately and delivering them asynchronously with
exponential retry, circuit breaking, fan-out routing, and a full DLQ intelligence layer.

## Key capabilities

- **Guaranteed delivery** — at-least-once delivery with exponential backoff and configurable retries
- **Circuit breaker** — per-destination open/half-open/closed state machine prevents thundering herd
- **DLQ Intelligence** — automatic failure classification, incident lifecycle, and 0-100 health scoring
- **Fan-out routing** — broadcast one inbound event to N destinations in a single API call
- **Filtering & transforms** — drop or reshape payloads before delivery with expression-based rules
- **Cloud adapters** — native ingest from AWS SNS, GCP Pub/Sub, and Azure Event Grid
- **Audit log** — tamper-evident record of every config mutation, queryable via API
- **Standard Webhooks** — HMAC-SHA256 outbound signing (svix-compatible)

## Authentication

All endpoints (except `/health`) require either:
- **JWT cookie** — obtained via `POST /auth/login` (SaaS dashboard users)
- **API key header** — `X-Relora-API-Key: <key>` (programmatic / SDK access)
""",
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    openapi_tags=[
        {"name": "ingest", "description": "Webhook ingestion — the hot path. Returns immediately after writing to Postgres."},
        {"name": "webhooks", "description": "Query and manage webhook records and delivery attempts."},
        {"name": "destinations", "description": "Registered delivery targets with circuit breaker state and per-destination config."},
        {"name": "dlq", "description": "Dead Letter Queue — inspect, analyse, and replay permanently failed webhooks."},
        {"name": "alerts", "description": "Alert channel configuration — Slack, email, and webhook notifications on DLQ events."},
        {"name": "event-types", "description": "Event type catalog with optional JSON Schema validation."},
        {"name": "projects", "description": "Project management and API key administration."},
        {"name": "auth", "description": "User registration, login, and session management."},
        {"name": "ai", "description": "Claude-powered DLQ analysis, filter suggestions, and transform generation."},
        {"name": "simulator", "description": "Generate realistic test payloads for Stripe, GitHub, Shopify, and more."},
        {"name": "system", "description": "Health, metrics, stats, audit log, and real-time streaming endpoints."},
        {"name": "sources", "description": "Cloud event source adapters — AWS SNS, GCP Pub/Sub, Azure Event Grid."},
    ],
)

app.add_middleware(SecurityHeadersMiddleware)

# CORS: only enabled when CORS_ORIGINS is explicitly configured.
# When the frontend is co-served with the API (nginx reverse proxy on the same
# domain) CORS is not needed and should stay off.
# When CORS_ORIGINS is set, allow_credentials=True is safe because we are using
# specific origins, not the wildcard "*".
_cors_origins = settings.cors_origins
if _cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
        allow_headers=["Authorization", "Content-Type", "X-Relora-API-Key"],
    )
    logger.info("CORS enabled for origins: %s", _cors_origins)

if settings.FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

app.include_router(sources_router)
app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(destinations.router)
app.include_router(webhooks.router)
app.include_router(alerts.router)
app.include_router(event_types.router)
app.include_router(ai_tools.router)
app.include_router(dlq.router)
app.include_router(simulator.router)
app.include_router(consumer.router)
app.include_router(system.router)
app.include_router(slo.router)
app.include_router(schema_drift.router)
app.include_router(events_router.router)
app.include_router(insights_router.router)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
