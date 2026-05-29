from contextlib import asynccontextmanager
import json
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
from app.routing import apply_transform, event_matches_filter, extract_event_id
from app.schemas import WebhookResponse, WebhookDetailResponse, DashboardStats
from app.security import require_api_key, validate_destination_url
from app.signatures import verify_webhook_signature
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
    url: Optional[str] = Query(None, description="The downstream destination URL for this webhook"),
    urls: Optional[List[str]] = Query(None, description="Additional downstream URLs for fan-out delivery"),
    filter_expression: Optional[str] = Query(None, alias="filter", description="Simple filter, for example event.type == 'payment.succeeded'"),
    transform: Optional[str] = Query(None, description="JSON object mapping output fields to source paths"),
    signature_provider: Optional[str] = Query(None, description="Optional signature provider: stripe, github, or hermes"),
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Generic ingestion endpoint. Accepts any headers and body, writes immediately
    to Postgres, and returns a 200 OK so the sender assumes delivery succeeded.
    """
    destination_candidates: List[str] = []
    if url:
        destination_candidates.append(url)
    if urls:
        for item in urls:
            destination_candidates.extend([part.strip() for part in item.split(",") if part.strip()])

    if not destination_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one destination is required via url or urls",
        )

    destination_urls = [validate_destination_url(destination) for destination in destination_candidates]

    # 1. Capture raw body once so signature verification and JSON parsing use identical bytes.
    raw_body = await request.body()
    verify_webhook_signature(signature_provider, request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        # If payload isn't JSON, read raw body as text and wrap it
        payload = {"_raw_body": raw_body.decode("utf-8", errors="replace")}

    explicit_event_id = request.headers.get("X-Event-Id") or request.headers.get("X-Hermes-Event-Id")
    event_id = extract_event_id(payload, explicit_event_id)

    if not event_matches_filter(payload, filter_expression):
        logger.info(
            "Webhook filtered before queueing.",
            extra={
                "event": "webhook.ingest.filtered",
                "tenant_id": tenant_id,
                "event_id": event_id,
                "filter": filter_expression,
                "destination_count": len(destination_urls),
            },
        )
        return {
            "success": True,
            "filtered": True,
            "webhook_ids": [],
            "message": "Webhook did not match filter and was not queued",
        }

    delivery_payload = apply_transform(payload, transform)

    # 2. Extract and filter headers
    headers: Dict[str, str] = {}
    for key, val in request.headers.items():
        if key.lower() not in EXCLUDED_INGEST_HEADERS:
            headers[key] = val

    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Hermes-Idempotency-Key") or event_id
    webhook_ids: List[str] = []
    duplicate_ids: List[str] = []

    for destination_url in destination_urls:
        existing_result = await db.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing_webhook = existing_result.scalar_one_or_none()
        if existing_webhook:
            webhook_ids.append(str(existing_webhook.id))
            duplicate_ids.append(str(existing_webhook.id))
            logger.info(
                "Duplicate webhook ingestion resolved by idempotency key.",
                extra={
                    "event": "webhook.ingest.duplicate",
                    "webhook_id": str(existing_webhook.id),
                    "tenant_id": tenant_id,
                    "event_id": event_id,
                    "destination_url": destination_url,
                    "idempotency_key": idempotency_key,
                },
            )
            continue

        webhook = Webhook(
            tenant_id=tenant_id,
            event_id=event_id,
            destination_url=destination_url,
            payload=delivery_payload,
            headers=headers,
            idempotency_key=idempotency_key,
            status=WebhookStatus.PENDING.value,
            max_retries=settings.DEFAULT_MAX_RETRIES
        )

        db.add(webhook)
        try:
            await db.flush()
            await db.commit()
            webhook_ids.append(str(webhook.id))
        except IntegrityError:
            await db.rollback()
            existing_result = await db.execute(
                select(Webhook).where(
                    Webhook.tenant_id == tenant_id,
                    Webhook.destination_url == destination_url,
                    Webhook.idempotency_key == idempotency_key,
                )
            )
            existing_webhook = existing_result.scalar_one_or_none()
            if not existing_webhook:
                raise
            webhook_ids.append(str(existing_webhook.id))
            duplicate_ids.append(str(existing_webhook.id))

        logger.info(
            "Webhook destination queued.",
            extra={
                "event": "webhook.ingest.destination_queued",
                "webhook_id": webhook_ids[-1],
                "tenant_id": tenant_id,
                "event_id": event_id,
                "destination_url": destination_url,
                "idempotency_key": idempotency_key,
            },
        )

    logger.info(
        "Webhook ingestion completed.",
        extra={
            "event": "webhook.ingest.completed",
            "tenant_id": tenant_id,
            "event_id": event_id,
            "destination_count": len(destination_urls),
            "queued_count": len(webhook_ids) - len(duplicate_ids),
            "duplicate_count": len(duplicate_ids),
            "signature_provider": signature_provider,
        },
    )
    
    # Return immediately to the client (200 OK)
    single = len(webhook_ids) == 1
    return {
        "success": True,
        "filtered": False,
        "event_id": event_id,
        "tenant_id": tenant_id,
        "webhook_id": webhook_ids[0] if single else None,
        "webhook_ids": webhook_ids,
        "duplicate": len(duplicate_ids) == len(webhook_ids),
        "duplicate_ids": duplicate_ids,
        "message": "Webhook ingested and queued for delivery"
    }

@app.get("/api/v1/webhooks", response_model=Dict[str, Any])
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    tenant_id: str = Depends(require_api_key),
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
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
        count_stmt = count_stmt.where(Webhook.tenant_id == tenant_id)
    
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
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves full details for a single webhook, including its logs/attempts.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
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
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually replays a failed or dead-lettered webhook.
    Resets the attempt counter, sets status back to pending, and forces
    next_attempt_at to current timestamp.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
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
        extra={"event": "webhook.replay.requested", "webhook_id": str(webhook_id), "tenant_id": tenant_id, "event_id": webhook.event_id},
    )
    
    return {
        "success": True,
        "message": "Webhook rescheduled for immediate delivery attempt."
    }

@app.get("/api/v1/stats", response_model=DashboardStats)
async def get_stats(
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes real-time statistics of webhook executions for the dashboard.
    """
    # 1. Total count
    tenant_filter = Webhook.tenant_id == tenant_id if settings.api_key_tenants else True
    total = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter)) or 0
    
    # 2. Count by status
    pending = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.FAILED.value)) or 0

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
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    tenant_filter = Webhook.tenant_id == tenant_id if settings.api_key_tenants else True
    total = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.FAILED.value)) or 0
    attempts = await db.scalar(
        select(func.count(DeliveryAttempt.id))
        .join(Webhook, DeliveryAttempt.webhook_id == Webhook.id)
        .where(tenant_filter)
    ) or 0

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

@app.get("/api/v1/usage")
async def get_usage(
    tenant_id: str = Depends(require_api_key),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(
            Webhook.tenant_id,
            func.count(Webhook.id).label("events"),
            func.count(func.distinct(Webhook.event_id)).label("unique_events"),
        )
        .group_by(Webhook.tenant_id)
        .order_by(Webhook.tenant_id)
    )
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)

    rows = (await db.execute(stmt)).all()
    return {
        "usage": [
            {
                "tenant_id": row.tenant_id,
                "events": row.events,
                "unique_events": row.unique_events,
            }
            for row in rows
        ]
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Mount static frontend files path-safely
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
