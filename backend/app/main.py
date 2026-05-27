from contextlib import asynccontextmanager
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException, Query, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db, init_db
from app.models import Webhook, WebhookStatus, DeliveryAttempt
from app.schemas import WebhookResponse, WebhookDetailResponse, DashboardStats
from app.worker import WorkerPool

# Configure logging
logging.basicConfig(level=logging.INFO)
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
    logger.info("Initializing database...")
    await init_db()
    
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
    db: AsyncSession = Depends(get_db)
):
    """
    Generic ingestion endpoint. Accepts any headers and body, writes immediately
    to Postgres, and returns a 200 OK so the sender assumes delivery succeeded.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="Destination URL must begin with http:// or https://"
        )

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

    # 3. Write to PostgreSQL durably
    webhook = Webhook(
        destination_url=url,
        payload=payload,
        headers=headers,
        status=WebhookStatus.PENDING,
        max_retries=settings.DEFAULT_MAX_RETRIES
    )
    
    db.add(webhook)
    await db.flush() # Flushes to get the database generated UUID instantly
    
    logger.info(f"Ingested webhook {webhook.id} targeting {url} successfully.")
    
    # Return immediately to the client (200 OK)
    return {
        "success": True,
        "webhook_id": str(webhook.id),
        "message": "Webhook ingested and queued for delivery"
    }

@app.get("/api/v1/webhooks", response_model=Dict[str, Any])
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
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
    webhook.status = WebhookStatus.PENDING
    webhook.retry_count = 0
    webhook.next_attempt_at = func.now()
    webhook.updated_at = func.now()
    
    await db.commit()
    logger.info(f"Manual replay triggered for webhook {webhook_id}")
    
    return {
        "success": True,
        "message": "Webhook rescheduled for immediate delivery attempt."
    }

@app.get("/api/v1/stats", response_model=DashboardStats)
async def get_stats(db: AsyncSession = Depends(get_db)):
    """
    Computes real-time statistics of webhook executions for the dashboard.
    """
    # 1. Total count
    total = await db.scalar(select(func.count(Webhook.id))) or 0
    
    # 2. Count by status
    pending = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PENDING)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.PROCESSING)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.COMPLETED)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(Webhook.status == WebhookStatus.FAILED)) or 0

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

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Mount static frontend files path-safely
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

