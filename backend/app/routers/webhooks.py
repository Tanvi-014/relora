import asyncio
import json
import logging
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.auth import get_tenant_from_auth
from app.config import settings
from app.db import get_db
from app.models import Destination, Project, ReplayJob, Webhook, WebhookStatus
from app.rate_limit import check_rate_limit
from app.routing import apply_transform, event_matches_filter, extract_event_id
from app.schema_validator import SchemaValidator
from app.security import validate_destination_url
from app.signatures import verify_webhook_signature
from app.telemetry import record_ingested
from app.routers.auth import EXCLUDED_INGEST_HEADERS

logger = logging.getLogger("relora.api")

# Strong references prevent GC from collecting tasks before they complete.
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro) -> asyncio.Task:
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    task.add_done_callback(_log_background_task_exception)
    return task


def _log_background_task_exception(task: asyncio.Task) -> None:
    if not task.cancelled() and (exc := task.exception()) is not None:
        logger.error("Background task %r raised an unhandled exception", task.get_name(), exc_info=exc)


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found for this API key")
    return project


router = APIRouter()


_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB — protects against memory exhaustion
_MAX_REPLAY_BATCH = 10_000         # safety cap for replay-window jobs


@router.post("/api/v1/ingest")
async def ingest_webhook(
    request: Request,
    url: Optional[str] = Query(None),
    urls: Optional[List[str]] = Query(None),
    destination_id: Optional[str] = Query(None),
    filter_expression: Optional[str] = Query(None, alias="filter"),
    transform: Optional[str] = Query(None),
    signature_provider: Optional[str] = Query(None),
    ordering_key: Optional[str] = Query(None),
    consumer_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, tenant_id, db)

    if settings.MONTHLY_EVENT_QUOTA > 0:
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # Lock the project row so that the count + subsequent insert are atomic.
        # This prevents concurrent requests from simultaneously passing the quota check.
        await db.execute(
            select(Project.id).where(Project.api_key == tenant_id).with_for_update()
        )
        monthly_usage = await db.scalar(
            select(func.count(Webhook.id)).where(
                Webhook.tenant_id == tenant_id,
                Webhook.created_at >= month_start,
            )
        ) or 0
        if monthly_usage >= settings.MONTHLY_EVENT_QUOTA:
            raise HTTPException(
                status_code=429,
                detail=f"Monthly quota of {settings.MONTHLY_EVENT_QUOTA:,} events exceeded.",
            )

    # Resolve destination candidates
    destination_candidates: List[str] = []
    dest_id_obj: Optional[UUID] = None

    if destination_id:
        dest_result = await db.execute(
            select(Destination)
            .join(Project, Project.id == Destination.project_id)
            .where(Destination.id == UUID(destination_id), Project.api_key == tenant_id)
        )
        dest_obj = dest_result.scalar_one_or_none()
        if not dest_obj:
            raise HTTPException(404, "Destination not found")
        destination_candidates.append(dest_obj.url)
        dest_id_obj = dest_obj.id
        # Override filter/transform from destination if not provided on request
        if not filter_expression:
            filter_expression = dest_obj.filter_expression
        if dest_obj.transform_type == "json_map" and dest_obj.transform_map and not transform:
            transform = json.dumps(dest_obj.transform_map)
        if dest_obj.ordering_key_field and not ordering_key:
            ordering_key = dest_obj.ordering_key_field  # will be resolved from payload below
    else:
        if url:
            destination_candidates.append(url)
        if urls:
            for item in urls:
                destination_candidates.extend([p.strip() for p in item.split(",") if p.strip()])

    if not destination_candidates:
        raise HTTPException(400, "At least one destination required via url, urls, or destination_id")

    destination_urls = [validate_destination_url(d) for d in destination_candidates]

    # Stream-read with size cap — handles both Content-Length and chunked-encoding.
    _body_chunks: list[bytes] = []
    _bytes_read = 0
    async for _chunk in request.stream():
        _bytes_read += len(_chunk)
        if _bytes_read > _MAX_BODY_BYTES:
            raise HTTPException(413, f"Payload too large. Maximum size is {_MAX_BODY_BYTES // 1024} KB.")
        _body_chunks.append(_chunk)
    raw_body = b"".join(_body_chunks)

    ingest_secret: Optional[str] = None
    if signature_provider:
        # Use a direct query so a missing project simply means no per-project secret —
        # the global signature secret from settings is the fallback.
        _proj_r = await db.execute(select(Project).where(Project.api_key == tenant_id))
        _proj_obj = _proj_r.scalar_one_or_none()
        _secrets = (_proj_obj.source_secrets or {}) if _proj_obj else {}
        ingest_secret = _secrets.get(signature_provider.lower())
    verify_webhook_signature(signature_provider, request, raw_body, secret=ingest_secret)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = {"_raw_body": raw_body.decode("utf-8", errors="replace")}

    explicit_event_id = request.headers.get("X-Event-Id") or request.headers.get("X-Relora-Event-Id")
    event_id = extract_event_id(payload, explicit_event_id)

    # Extract event type name from payload or headers for schema validation
    event_type_name = request.headers.get("X-Event-Type") or payload.get("event_type") or payload.get("type") or "unknown"

    # Validate payload against registered schema if destination has a project
    if dest_id_obj and dest_obj:
        project_id_str = str(dest_obj.project_id)
        is_valid, validation_error = await SchemaValidator.validate_payload(
            db, project_id_str, event_type_name, payload
        )
        if not is_valid:
            # Reject malformed payloads with clear error message
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "Schema validation failed",
                    "event_type": event_type_name,
                    "message": validation_error,
                }
            )

    # Schema drift detection — fire-and-forget, never blocks ingest
    if isinstance(payload, dict):
        import asyncio as _asyncio
        from app.schema_drift import check_and_update as _check_drift
        source_key = (
            request.headers.get("X-Source-Key")
            or signature_provider
            or event_type_name
            or "unknown"
        )
        _fire_and_forget(_check_drift(db, tenant_id, source_key, payload))

    # Resolve ordering_key from payload field if it looks like a path
    if ordering_key and isinstance(payload, dict) and "." in ordering_key:
        from app.routing import get_path
        resolved = get_path(payload, ordering_key)
        if resolved:
            ordering_key = str(resolved)

    try:
        if not event_matches_filter(payload, filter_expression):
            return {"success": True, "filtered": True, "webhook_ids": [], "message": "Filtered, not queued"}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    delivery_payload = apply_transform(payload, transform)

    headers: Dict[str, str] = {
        k: v for k, v in request.headers.items()
        if k.lower() not in EXCLUDED_INGEST_HEADERS
    }

    idempotency_key = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Relora-Idempotency-Key")
        or event_id
    )

    webhook_ids: List[str] = []
    duplicate_ids: List[str] = []

    for destination_url in destination_urls:
        # Idempotency check
        existing_r = await db.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing = existing_r.scalar_one_or_none()
        if existing:
            webhook_ids.append(str(existing.id))
            duplicate_ids.append(str(existing.id))
            continue

        webhook = Webhook(
            tenant_id=tenant_id,
            event_id=event_id,
            destination_url=destination_url,
            destination_id=dest_id_obj,
            payload=delivery_payload,
            headers=headers,
            idempotency_key=idempotency_key,
            ordering_key=ordering_key,
            consumer_id=consumer_id,
            status=WebhookStatus.PENDING.value,
            max_retries=settings.DEFAULT_MAX_RETRIES,
        )
        db.add(webhook)
        try:
            await db.flush()
            # NOTIFY within the same transaction — only delivered if commit succeeds.
            await db.execute(text("SELECT pg_notify('new_webhook', '')"))
            await db.commit()
            webhook_ids.append(str(webhook.id))
        except IntegrityError:
            await db.rollback()
            existing_r2 = await db.execute(
                select(Webhook).where(
                    Webhook.tenant_id == tenant_id,
                    Webhook.destination_url == destination_url,
                    Webhook.idempotency_key == idempotency_key,
                )
            )
            existing2 = existing_r2.scalar_one_or_none()
            if not existing2:
                raise
            webhook_ids.append(str(existing2.id))
            duplicate_ids.append(str(existing2.id))

    record_ingested(tenant_id)
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
        "message": "Webhook ingested and queued",
    }


@router.delete("/api/v1/webhooks/simulated", status_code=200)
async def clear_simulated_webhooks(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    from sqlalchemy import delete as sa_delete
    result = await db.execute(
        sa_delete(Webhook).where(
            Webhook.tenant_id == tenant_id,
            Webhook.is_simulation == True,
        )
    )
    await db.commit()
    return {"deleted": result.rowcount}


@router.get("/api/v1/webhooks")
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    destination_id: Optional[str] = Query(None),
    exclude_simulations: bool = Query(False),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit
    stmt = select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(desc(Webhook.created_at))
    count_stmt = select(func.count(Webhook.id)).where(Webhook.tenant_id == tenant_id)

    if exclude_simulations:
        stmt = stmt.where(Webhook.is_simulation == False)
        count_stmt = count_stmt.where(Webhook.is_simulation == False)

    if status_filter:
        try:
            sv = WebhookStatus(status_filter.lower()).value
            stmt = stmt.where(Webhook.status == sv)
            count_stmt = count_stmt.where(Webhook.status == sv)
        except ValueError:
            raise HTTPException(400, f"Invalid status. Valid: {[s.value for s in WebhookStatus]}")

    if destination_id:
        stmt = stmt.where(Webhook.destination_id == UUID(destination_id))
        count_stmt = count_stmt.where(Webhook.destination_id == UUID(destination_id))

    if search:
        stmt = stmt.where(
            text("webhooks.payload::text ILIKE :search").bindparams(search=f"%{search}%")
        )
        count_stmt = count_stmt.where(
            text("webhooks.payload::text ILIKE :search").bindparams(search=f"%{search}%")
        )

    result = await db.execute(stmt.offset(offset).limit(limit))
    webhooks = result.scalars().all()
    total = await db.scalar(count_stmt) or 0

    return {
        "webhooks": [w.to_dict() for w in webhooks],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/api/v1/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    d = wh.to_dict()
    d["payload"] = wh.payload
    d["headers"] = wh.headers
    d["attempts"] = [a.to_dict() for a in wh.attempts]
    return d


@router.post("/api/v1/webhooks/{webhook_id}/replay")
async def replay_webhook(
    request: Request,
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")

    # Reset max_retries to the destination's current value so webhooks that
    # originally had max_retries=0 (e.g. test events) don't instantly DLQ on replay.
    new_max_retries = settings.DEFAULT_MAX_RETRIES
    if wh.destination_id:
        dest_r = await db.execute(select(Destination).where(Destination.id == wh.destination_id))
        dest = dest_r.scalar_one_or_none()
        if dest:
            new_max_retries = dest.max_retries

    wh.status = WebhookStatus.PENDING.value
    wh.retry_count = 0
    wh.max_retries = new_max_retries
    wh.next_attempt_at = datetime.now(timezone.utc)
    wh.updated_at = datetime.now(timezone.utc)
    from app.audit import audit as _audit
    await _audit(db, request, tenant_id, "REPLAY", "webhook", str(webhook_id))
    await db.commit()
    logger.info("Replay triggered", extra={"event": "webhook.replay.requested", "webhook_id": str(webhook_id)})
    return {"success": True, "message": "Rescheduled for immediate delivery"}


@router.post("/api/v1/webhooks/replay-window")
async def replay_time_window(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    from_time = datetime.fromisoformat(body["from_time"].replace("Z", "+00:00"))
    to_time = datetime.fromisoformat(body["to_time"].replace("Z", "+00:00"))
    dest_id = body.get("destination_id")
    rate = int(body.get("replay_rate_per_minute", 100))
    # Default True: only replay failed webhooks to prevent accidental re-delivery
    # of already-completed or in-flight events.
    only_failed: bool = body.get("only_failed", True)
    force: bool = body.get("force", False)

    project = await _get_project_by_api_key(db, tenant_id)

    status_clause = "AND status = 'failed'" if only_failed else ""
    count_r = await db.execute(
        text(f"""
        SELECT COUNT(*) FROM webhooks
        WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
          AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
          {status_clause}
        """),
        {"tid": tenant_id, "from_t": from_time, "to_t": to_time,
         "dest_id": dest_id},
    )
    total = count_r.scalar() or 0

    if total > _MAX_REPLAY_BATCH and not force:
        raise HTTPException(
            400,
            f"Replay would affect {total:,} events, exceeding the {_MAX_REPLAY_BATCH:,}-event limit. "
            "Pass force=true to proceed anyway.",
        )

    job = ReplayJob(
        project_id=project.id,
        from_time=from_time,
        to_time=to_time,
        destination_id=UUID(dest_id) if dest_id else None,
        replay_rate_per_minute=rate,
        total_count=total,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    _fire_and_forget(_execute_replay_job(str(job.id), tenant_id, from_time, to_time, dest_id, rate, only_failed))

    return {
        "job_id": str(job.id),
        "total_count": total,
        "estimated_duration_minutes": round(total / rate, 1) if rate else 0,
    }


async def _execute_replay_job(job_id: str, tenant_id: str, from_time, to_time, dest_id, rate: int, only_failed: bool = True):
    from app.db import async_session as _session

    # Phase 1: mark running and fetch all IDs with a short-lived session.
    status_clause = "AND status = 'failed'" if only_failed else ""
    async with _session() as db:
        await db.execute(
            text("UPDATE replay_jobs SET status='running', updated_at=NOW() WHERE id=:id"),
            {"id": job_id},
        )
        await db.commit()
        result = await db.execute(
            text(f"""
            SELECT id FROM webhooks
            WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
              AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
              {status_clause}
            ORDER BY created_at ASC
            """),
            {"tid": tenant_id, "from_t": from_time, "to_t": to_time, "dest_id": dest_id},
        )
        rows = result.fetchall()

    # Phase 2: process each item with a fresh session so the DB connection is
    # released during the inter-item sleep (prevents pool exhaustion on long replays).
    delay_per_item = 60.0 / rate if rate > 0 else 0.1
    try:
        processed = 0
        for row in rows:
            async with _session() as db:
                upd = await db.execute(
                    text("""
                    UPDATE webhooks SET status='pending', retry_count=0,
                      max_retries=COALESCE(
                        (SELECT d.max_retries FROM destinations d WHERE d.id = webhooks.destination_id),
                        :default_mr
                      ),
                      next_attempt_at=NOW(), updated_at=NOW()
                    WHERE id=:id
                    RETURNING id
                    """),
                    {"id": row.id, "default_mr": settings.DEFAULT_MAX_RETRIES},
                )
                processed += len(upd.fetchall())
                await db.execute(
                    text("UPDATE replay_jobs SET processed_count=:n, updated_at=NOW() WHERE id=:id"),
                    {"n": processed, "id": job_id},
                )
                await db.execute(text("SELECT pg_notify('new_webhook', '')"))
                await db.commit()
            await asyncio.sleep(delay_per_item)

        async with _session() as db:
            await db.execute(
                text("UPDATE replay_jobs SET status='completed', updated_at=NOW() WHERE id=:id"),
                {"id": job_id},
            )
            await db.commit()
    except Exception as exc:
        logger.error("Replay job failed: %s", exc, exc_info=True)
        async with _session() as db:
            await db.execute(
                text("UPDATE replay_jobs SET status='failed', error_message=:err, updated_at=NOW() WHERE id=:id"),
                {"err": str(exc), "id": job_id},
            )
            await db.commit()


@router.get("/api/v1/replay-jobs")
async def list_replay_jobs(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, le=100),
):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(ReplayJob)
        .where(ReplayJob.project_id == project.id)
        .order_by(ReplayJob.created_at.desc())
        .limit(limit)
    )
    jobs = result.scalars().all()
    return [j.to_dict() for j in jobs]


@router.get("/api/v1/replay-jobs/{job_id}")
async def get_replay_job(
    job_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(ReplayJob).where(ReplayJob.id == job_id, ReplayJob.project_id == project.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Replay job not found")
    return job.to_dict()
