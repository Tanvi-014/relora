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

configure_logging()
logger = logging.getLogger("hermes.api")

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
    setup_telemetry(service_name="hermes-api")
    if settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=true, initializing tables...")
        await init_db()

    await _recover_stuck_replay_jobs()

    logger.info("Starting incident scheduler...")
    await start_incident_scheduler()

    yield

    logger.info("Stopping incident scheduler...")
    await stop_incident_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-grade webhook delivery middleware.",
    version="2.0.0",
    lifespan=lifespan,
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
        allow_headers=["Authorization", "Content-Type", "X-Hermes-API-Key"],
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

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
