import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text, func, desc
from uuid import UUID

from fastapi import Request
from app.audit import audit
from app.auth import get_tenant_from_auth
from app.db import get_db
from app.destination_health import DestinationHealthAnalyzer
from app.models import Destination, Incident, Project
from app.schemas import DestinationCreate, DestinationUpdate
from app.security import validate_destination_url

router = APIRouter()


async def _get_project_by_api_key(db: AsyncSession, api_key: str):
    from app.models import Project as _Project
    result = await db.execute(select(_Project).where(_Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        return _Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


async def _get_dest_for_tenant(db: AsyncSession, dest_id: UUID, tenant_id: str) -> Destination:
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Destination.id == dest_id, Project.api_key == tenant_id)
    )
    dest = result.scalar_one_or_none()
    if not dest:
        raise HTTPException(404, "Destination not found")
    return dest


@router.get("/api/v1/destinations")
async def list_destinations(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Project.api_key == tenant_id)
        .order_by(Destination.created_at.desc())
    )
    return [d.to_dict() for d in result.scalars().all()]


@router.post("/api/v1/destinations", status_code=201)
async def create_destination(
    request: Request,
    body: DestinationCreate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    validate_destination_url(body.url)
    dest = Destination(
        project_id=project.id,
        name=body.name,
        url=body.url,
        description=body.description,
        is_enabled=body.is_enabled,
        max_retries=body.max_retries,
        backoff_base_seconds=body.backoff_base_seconds,
        ordering_key_field=body.ordering_key_field,
        transform_type=body.transform_type,
        transform_code=body.transform_code,
        transform_map=body.transform_map,
        filter_expression=body.filter_expression,
        webhook_secret=body.webhook_secret,
        custom_headers=body.custom_headers,
    )
    db.add(dest)
    await db.flush()
    await audit(db, request, tenant_id, "CREATE", "destination", str(dest.id), after=dest.to_dict())
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Destination name already exists in this project")
    await db.refresh(dest)
    return dest.to_dict()


@router.get("/api/v1/destinations/{dest_id}")
async def get_destination(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    return dest.to_dict()


@router.put("/api/v1/destinations/{dest_id}")
async def update_destination(
    request: Request,
    dest_id: UUID,
    body: DestinationUpdate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    before = dest.to_dict()
    update_data = body.model_dump(exclude_unset=True)
    if "url" in update_data:
        validate_destination_url(update_data["url"])
    for field, value in update_data.items():
        setattr(dest, field, value)
    dest.updated_at = datetime.now(timezone.utc)
    await audit(db, request, tenant_id, "UPDATE", "destination", str(dest_id), before=before, after=dest.to_dict())
    await db.commit()
    await db.refresh(dest)
    return dest.to_dict()


@router.delete("/api/v1/destinations/{dest_id}", status_code=204)
async def delete_destination(
    request: Request,
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    await audit(db, request, tenant_id, "DELETE", "destination", str(dest_id), before=dest.to_dict())
    await db.delete(dest)
    await db.commit()
    return Response(status_code=204)


@router.post("/api/v1/destinations/{dest_id}/reset-circuit")
async def reset_circuit_breaker(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    dest.circuit_state = "closed"
    dest.circuit_failure_count = 0
    dest.circuit_opened_at = None
    dest.circuit_next_retry_at = None
    await db.commit()
    await db.refresh(dest)
    return {"status": "reset", "circuit_state": dest.circuit_state}


@router.post("/api/v1/destinations/{dest_id}/test")
async def test_destination(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    import httpx as _httpx
    test_payload = {"event": "test", "source": "relora", "destination_id": str(dest_id)}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.post(dest.url, json=test_payload, headers={"X-Relora-Test": "true"})
        return {"success": 200 <= r.status_code < 300, "status_code": r.status_code, "body": r.text[:500]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@router.post("/api/v1/destinations/{dest_id}/send-test-event", status_code=201)
async def send_test_event(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a real test webhook that flows through the full delivery pipeline (is_simulation=True)."""
    from datetime import datetime, timezone
    from app.models import Webhook
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    webhook = Webhook(
        tenant_id=tenant_id,
        event_id=f"evt_test_{_uuid_mod.uuid4().hex[:10]}",
        destination_url=dest.url,
        destination_id=dest.id,
        payload={
            "event_type": "relora.test",
            "data": {
                "message": "Hello from Relora!",
                "destination": dest.name,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        },
        headers={"Content-Type": "application/json", "X-Relora-Test": "true"},
        is_simulation=True,
        max_retries=0,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return {"webhook_id": str(webhook.id), "status": "queued", "destination": dest.name}


@router.get("/api/v1/destinations/{dest_id}/stats")
async def destination_stats(
    dest_id: UUID,
    period_days: int = Query(7, le=90),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    await _get_dest_for_tenant(db, dest_id, tenant_id)
    result = await db.execute(
        text("""
        SELECT
          COUNT(da.id) as total_attempts,
          COUNT(da.id) FILTER (WHERE da.status_code BETWEEN 200 AND 299) as successes,
          PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY da.duration_ms) as p50_ms,
          PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.duration_ms) as p95_ms,
          PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY da.duration_ms) as p99_ms,
          AVG(da.duration_ms) as avg_ms
        FROM delivery_attempts da
        JOIN webhooks w ON w.id = da.webhook_id
        WHERE w.destination_id = :dest_id
          AND da.attempted_at >= NOW() - INTERVAL '1 day' * :days
        """),
        {"dest_id": dest_id, "days": period_days},
    )
    row = result.fetchone()
    total = row.total_attempts or 0
    successes = row.successes or 0
    return {
        "period_days": period_days,
        "total_attempts": total,
        "success_rate": round(successes / total * 100, 1) if total else 100.0,
        "latency": {
            "p50_ms": round(row.p50_ms or 0),
            "p95_ms": round(row.p95_ms or 0),
            "p99_ms": round(row.p99_ms or 0),
            "avg_ms": round(row.avg_ms or 0),
        },
    }


@router.get("/api/v1/destinations/{destination_id}/health")
async def get_destination_health(
    destination_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get health report for a specific destination."""
    health_data = await DestinationHealthAnalyzer.get_destination_health(db, str(destination_id))
    return health_data


@router.get("/api/v1/destinations/{dest_id}/reliability-trend")
async def reliability_trend(
    dest_id: UUID,
    days: int = Query(30, le=90),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return daily reliability snapshots for a destination (last N days)."""
    from app.models import DestinationReliabilitySnapshot
    await _get_dest_for_tenant(db, dest_id, tenant_id)
    rows = (await db.execute(
        select(DestinationReliabilitySnapshot)
        .where(DestinationReliabilitySnapshot.destination_id == dest_id)
        .where(DestinationReliabilitySnapshot.date >= text(f"NOW() - INTERVAL '{days} days'"))
        .order_by(DestinationReliabilitySnapshot.date)
    )).scalars().all()
    return {
        "destination_id": str(dest_id),
        "days": days,
        "snapshots": [
            {
                "date": r.date.date().isoformat() if hasattr(r.date, "date") else str(r.date)[:10],
                "total": r.total_deliveries,
                "successful": r.successful_deliveries,
                "failed": r.failed_deliveries,
                "success_rate": r.success_rate,
                "avg_latency_ms": r.avg_latency_ms,
                "p95_latency_ms": r.p95_latency_ms,
            }
            for r in rows
        ],
    }


@router.get("/api/v1/destinations/{destination_id}/incidents")
async def get_destination_incidents(
    destination_id: UUID,
    state: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get incidents for a specific destination."""
    offset = (page - 1) * limit

    query = select(Incident).where(Incident.destination_id == destination_id).order_by(
        desc(Incident.last_seen_at)
    )
    count_query = select(func.count(Incident.id)).where(Incident.destination_id == destination_id)

    if state:
        query = query.where(Incident.state == state)
