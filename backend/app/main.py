from contextlib import asynccontextmanager
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db, init_db
from app.logging_config import configure_logging
from app.models import Webhook, WebhookStatus, DeliveryAttempt
from app.schemas import WebhookResponse, WebhookDetailResponse, DashboardStats
from app.security import require_api_key, validate_destination_url
from app.worker import WorkerPool

configure_logging()
logger = logging.getLogger("hermes.api")

# Worker pool instance
worker_pool = WorkerPool(concurrency=settings.WORKER_CONCURRENCY)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events:
    1. Initializes DB tables.
    2. Starts the background worker pool.
    3. Gracefully shuts down the worker pool on termination.
    """
    if settings.AUTO_CREATE_TABLES:
        logger.info("Initializing database...")
        await init_db()
    else:
        logger.info("Skipping automatic table creation because AUTO_CREATE_TABLES=false")
    
    logger.info("Starting background worker pool...")
    worker_pool.start()
    
    yield
    
    logger.info("Shutting down background workers...")
    await worker_pool.stop()

app = FastAPI(
    title=settings.APP_NAME,
    description="High-reliability self-hostable webhook proxy & delivery manager.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend dashboard queries
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In development, allow all origins.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Headers to filter out during ingestion to prevent downstreams from getting confused
EXCLUDED_INGEST_HEADERS = {
    "host",
    "connection",
    "content-length",
    "accept-encoding",
    "user-agent",
    "x-real-ip",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-port",
}

@app.post("/api/v1/ingest", status_code=status.HTTP_200_OK)
async def ingest_webhook(
    request: Request,
    url: str = Query(..., description="The downstream destination URL for this webhook"),
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Generic ingestion endpoint. Accepts any headers and body, writes immediately
    to Postgres, and returns a 200 OK so the sender assumes delivery succeeded.
    """
    destination_url = validate_destination_url(url)

    # 1. Capture payload body (as JSON, fallback to raw string if parsing fails)
    try:
        payload = await request.json()
    except Exception:
        # If payload isn't JSON, read raw body as text and wrap it
        body_bytes = await request.body()
        payload = {"_raw_body": body_bytes.decode("utf-8", errors="replace")}

    # 2. Extract and filter headers
    headers: Dict[str, str] = {}
    for key, val in request.headers.items():
        if key.lower() not in EXCLUDED_INGEST_HEADERS:
            headers[key] = val

    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Hermes-Idempotency-Key")
    if idempotency_key:
        existing_result = await db.execute(
            select(Webhook).where(
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing_webhook = existing_result.scalar_one_or_none()
        if existing_webhook:
            logger.info(
                "Duplicate webhook ingestion resolved by idempotency key.",
                extra={
                    "event": "webhook.ingest.duplicate",
                    "webhook_id": str(existing_webhook.id),
                    "destination_url": destination_url,
                    "idempotency_key": idempotency_key,
                },
            )
            return {
                "success": True,
                "webhook_id": str(existing_webhook.id),
                "duplicate": True,
                "message": "Webhook already ingested for this idempotency key",
            }

    # 3. Write to PostgreSQL durably
    webhook = Webhook(
        destination_url=destination_url,
        payload=payload,
        headers=headers,
        idempotency_key=idempotency_key,
        status=WebhookStatus.PENDING.value,
        max_retries=settings.DEFAULT_MAX_RETRIES
    )
    
    db.add(webhook)
    try:
        await db.flush() # Flushes to get the database generated UUID instantly
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if not idempotency_key:
            raise

        existing_result = await db.execute(
            select(Webhook).where(
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing_webhook = existing_result.scalar_one_or_none()
        if not existing_webhook:
            raise

        logger.info(
            "Duplicate webhook ingestion resolved after unique constraint race.",
            extra={
                "event": "webhook.ingest.duplicate_race",
                "webhook_id": str(existing_webhook.id),
                "destination_url": destination_url,
                "idempotency_key": idempotency_key,
            },
        )
        return {
            "success": True,
            "webhook_id": str(existing_webhook.id),
            "duplicate": True,
            "message": "Webhook already ingested for this idempotency key",
        }
    
    logger.info(
        "Webhook ingested and queued.",
        extra={
            "event": "webhook.ingest.created",
            "webhook_id": str(webhook.id),
            "destination_url": destination_url,
            "idempotency_key": idempotency_key,
        },
    )
    
    # Return immediately to the client (200 OK)
    return {
        "success": True,
        "webhook_id": str(webhook.id),
        "duplicate": False,
        "message": "Webhook ingested and queued for delivery"
    }

@app.get("/api/v1/webhooks", response_model=Dict[str, Any])
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of all ingested webhooks, sorted by creation date descending.
    Allows filtering by status and supports pagination.
    """
    offset = (page - 1) * limit
    
    # 1. Build Query
    stmt = select(Webhook).order_by(desc(Webhook.created_at))
    count_stmt = select(func.count(Webhook.id))
    
    if status_filter:
        try:
            status_enum = WebhookStatus(status_filter.lower())
            stmt = stmt.where(Webhook.status == status_enum.value)
            count_stmt = count_stmt.where(Webhook.status == status_enum.value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status value. Choose from: {[s.value for s in WebhookStatus]}"
            )

    # 2. Execute paginated select and count
    result = await db.execute(stmt.offset(offset).limit(limit))
    webhooks = result.scalars().all()
    
    total_count_result = await db.execute(count_stmt)
    total_count = total_count_result.scalar_one()

    return {
        "webhooks": [w.to_dict() for w in webhooks],
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": (total_count + limit - 1) // limit
    }

@app.get("/api/v1/webhooks/{webhook_id}", response_model=WebhookDetailResponse)
async def get_webhook_details(
    webhook_id: UUID,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves full details for a single webhook, including its logs/attempts.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    result = await db.execute(stmt)
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found"
        )
        
    return webhook

@app.post("/api/v1/webhooks/{webhook_id}/replay")
async def replay_webhook(
    webhook_id: UUID,
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually replays a failed or dead-lettered webhook.
    Resets the attempt counter, sets status back to pending, and forces
    next_attempt_at to current timestamp.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    result = await db.execute(stmt)
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found"
        )

    # Reset attempt tracking parameters
    webhook.status = WebhookStatus.PENDING.value
    webhook.retry_count = 0
    webhook.next_attempt_at = func.now()
    webhook.updated_at = func.now()
    
    await db.commit()
    logger.info(
        "Manual replay triggered.",
        extra={"event": "webhook.replay.requested", "webhook_id": str(webhook_id)},
    )
    
    return {
        "success": True,
        "message": "Webhook rescheduled for immediate delivery attempt."
    }

@app.get("/api/v1/stats", response_model=DashboardStats)
async def get_stats(
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes real-time statistics of webhook executions for the dashboard.
    """
    # 1. Total count
    total = await db.scalar(select(func.count(Webhook.id))) or 0
    
    # 2. Count by status
    pending = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.FAILED.value)) or 0

    # 3. Calculate success rate based on terminal states (completed / (completed + failed))
    terminal_total = completed + failed
    success_rate = (completed / terminal_total * 100) if terminal_total > 0 else 100.0

    return {
        "total_webhooks": total,
        "pending_count": pending,
        "processing_count": processing,
        "completed_count": completed,
        "failed_count": failed,
        "success_rate": round(success_rate, 1)
    }

@app.get("/metrics")
async def get_metrics(
    _: None = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    total = await db.scalar(select(func.count(Webhook.id))) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.FAILED.value)) or 0
    attempts = await db.scalar(select(func.count(DeliveryAttempt.id))) or 0

    body = "\n".join([
        "# HELP hermes_webhooks_total Total ingested webhooks.",
        "# TYPE hermes_webhooks_total gauge",
        f"hermes_webhooks_total {total}",
        "# HELP hermes_webhooks_by_status Webhooks grouped by delivery status.",
        "# TYPE hermes_webhooks_by_status gauge",
        f'hermes_webhooks_by_status{{status="pending"}} {pending}',
        f'hermes_webhooks_by_status{{status="processing"}} {processing}',
        f'hermes_webhooks_by_status{{status="completed"}} {completed}',
        f'hermes_webhooks_by_status{{status="failed"}} {failed}',
        "# HELP hermes_delivery_attempts_total Total delivery attempts.",
        "# TYPE hermes_delivery_attempts_total gauge",
        f"hermes_delivery_attempts_total {attempts}",
        "",
    ])

    return Response(content=body, media_type="text/plain; version=0.0.4")

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Mount static frontend files path-safely
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
