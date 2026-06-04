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

logger = logging.getLogger("hermes.api")


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        return Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


router = APIRouter()


_MAX_BODY_BYTES = 1 * 1024 * 1024  # 1 MB — protects against memory exhaustion


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
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > _MAX_BODY_BYTES:
        raise HTTPException(413, f"Payload too large. Maximum size is {_MAX_BODY_BYTES // 1024} KB.")

    await check_rate_limit(request, tenant_id, db)

    if settings.MONTHLY_EVENT_QUOTA > 0:
        month_start = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
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
        dest_result = await db.execute(select(Destination).where(Destination.id == UUID(destination_id)))
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

    raw_body = await request.body()
    verify_webhook_signature(signature_provider, request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = {"_raw_body": raw_body.decode("utf-8", errors="replace")}

    explicit_event_id = request.headers.get("X-Event-Id") or request.headers.get("X-Hermes-Event-Id")
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

    # Resolve ordering_key from payload field if it looks like a path
    if ordering_key and isinstance(payload, dict) and "." in ordering_key:
        from app.routing import get_path
        resolved = get_path(payload, ordering_key)
        if resolved:
            ordering_key = str(resolved)

    if not event_matches_filter(payload, filter_expression):
        return {"success": True, "filtered": True, "webhook_ids": [], "message": "Filtered, not queued"}

    delivery_payload = apply_transform(payload, transform)

    headers: Dict[str, str] = {
        k: v for k, v in request.headers.items()
        if k.lower() not in EXCLUDED_INGEST_HEADERS
    }

    idempotency_key = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Hermes-Idempotency-Key")
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


@router.get("/api/v1/webhooks")
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    destination_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit
    stmt = select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(desc(Webhook.created_at))
    count_stmt = select(func.count(Webhook.id)).where(Webhook.tenant_id == tenant_id)

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
    wh.status = WebhookStatus.PENDING.value
    wh.retry_count = 0
    wh.next_attempt_at = datetime.now(timezone.utc)
    wh.updated_at = datetime.now(timezone.utc)
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

    project = await _get_project_by_api_key(db, tenant_id)

    count_r = await db.execute(
        text("""
        SELECT COUNT(*) FROM webhooks
        WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
          AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
        """),
        {"tid": tenant_id, "from_t": from_time, "to_t": to_time,
         "dest_id": dest_id},
    )
    total = count_r.scalar() or 0

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

    asyncio.create_task(_execute_replay_job(str(job.id), tenant_id, from_time, to_time, dest_id, rate))

    return {
        "job_id": str(job.id),
        "total_count": total,
        "estimated_duration_minutes": round(total / rate, 1) if rate else 0,
    }


async def _execute_replay_job(job_id: str, tenant_id: str, from_time, to_time, dest_id, rate: int):
    from app.db import async_session as _session
    async with _session() as db:
        try:
            await db.execute(
                text("UPDATE replay_jobs SET status='running', updated_at=NOW() WHERE id=:id"),
                {"id": job_id},
            )
            await db.commit()

            result = await db.execute(
                text("""
                SELECT id FROM webhooks
                WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
                  AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
                ORDER BY created_at ASC
                """),
                {"tid": tenant_id, "from_t": from_time, "to_t": to_time, "dest_id": dest_id},
            )
            rows = result.fetchall()

            delay_per_item = 60.0 / rate if rate > 0 else 0.1
            for i, row in enumerate(rows):
                await db.execute(
                    text("""
                    UPDATE webhooks SET status='pending', retry_count=0,
                      next_attempt_at=NOW(), updated_at=NOW()
                    WHERE id=:id
                    """),
                    {"id": row.id},
                )
                await db.execute(
                    text("UPDATE replay_jobs SET processed_count=:n, updated_at=NOW() WHERE id=:id"),
                    {"n": i + 1, "id": job_id},
                )
                await db.commit()
                await asyncio.sleep(delay_per_item)

            await db.execute(
                text("UPDATE replay_jobs SET status='completed', updated_at=NOW() WHERE id=:id"),
                {"id": job_id},
            )
            await db.commit()
        except Exception as exc:
            logger.error("Replay job failed: %s", exc, exc_info=True)
            await db.execute(
                text("UPDATE replay_jobs SET status='failed', error_message=:err, updated_at=NOW() WHERE id=:id"),
                {"err": str(exc), "id": job_id},
            )
            await db.commit()


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
