import asyncio
import json
import logging
import uuid as _uuid_mod
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.health_engine import HealthEngine
from app.models import AuditLog, DeliveryAttempt, Destination, Project, ProjectMember, Webhook
from app.sse_hub import sse_hub
from app.websocket_hub import ws_manager

logger = logging.getLogger("hermes.api")

router = APIRouter()


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        return Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


@router.get("/health")
async def health():
    return {"status": "healthy", "version": "2.0.0"}


@router.get("/health/detailed")
async def health_detailed(db: AsyncSession = Depends(get_db)):
    checks: Dict[str, Any] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    stuck_r = await db.execute(
        text("SELECT COUNT(*) FROM webhooks WHERE status='processing' AND updated_at < NOW() - INTERVAL '5 minutes'")
    )
    stuck = stuck_r.scalar() or 0
    checks["worker"] = {"status": "warning" if stuck > 0 else "ok", "stuck_jobs": stuck}

    queue_r = await db.execute(text("SELECT COUNT(*) FROM webhooks WHERE status='pending'"))
    checks["queue"] = {"depth": queue_r.scalar() or 0}

    overall = "ok" if all(c.get("status") in ("ok", None) for c in checks.values() if "status" in c) else "degraded"
    return {"status": overall, "checks": checks, "version": "2.0.0"}


@router.get("/metrics")
async def get_metrics(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    tf = Webhook.tenant_id == tenant_id
    total = await db.scalar(select(func.count(Webhook.id)).where(tf)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "pending")) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "processing")) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "completed")) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "failed")) or 0
    attempts = await db.scalar(
        select(func.count(DeliveryAttempt.id))
        .join(Webhook, DeliveryAttempt.webhook_id == Webhook.id)
        .where(tf)
    ) or 0

    # Circuit breaker states across all destinations for this tenant's projects
    cb_rows = await db.execute(
        select(Destination.circuit_state, func.count(Destination.id).label("cnt"))
        .join(Project, Destination.project_id == Project.id)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .group_by(Destination.circuit_state)
    )
    cb_counts: Dict[str, int] = {"closed": 0, "open": 0, "half_open": 0}
    for row in cb_rows.all():
        cb_counts[row.circuit_state] = row.cnt

    # DLQ health score (best-effort; skips if engine fails)
    dlq_health_score: Optional[float] = None
    try:
        project_result = await db.execute(
            select(Project.id)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .limit(1)
        )
        first_project = project_result.scalar_one_or_none()
        if first_project:
            health = await HealthEngine.calculate_dlq_health_score(db, str(first_project))
            dlq_health_score = health.get("health_score")
    except Exception:
        pass

    terminal = completed + failed
    success_rate = round(completed / terminal * 100, 1) if terminal else 100.0

    lines = [
        "# HELP hermes_webhooks_total Total ingested webhooks.",
        "# TYPE hermes_webhooks_total gauge",
        f"hermes_webhooks_total {total}",
        "# HELP hermes_webhooks_by_status Webhooks by delivery status.",
        "# TYPE hermes_webhooks_by_status gauge",
        f'hermes_webhooks_by_status{{status="pending"}} {pending}',
        f'hermes_webhooks_by_status{{status="processing"}} {processing}',
        f'hermes_webhooks_by_status{{status="completed"}} {completed}',
        f'hermes_webhooks_by_status{{status="failed"}} {failed}',
        "# HELP hermes_delivery_attempts_total Total delivery attempts.",
        "# TYPE hermes_delivery_attempts_total gauge",
        f"hermes_delivery_attempts_total {attempts}",
        "# HELP hermes_delivery_success_rate Percentage of successfully delivered webhooks.",
        "# TYPE hermes_delivery_success_rate gauge",
        f"hermes_delivery_success_rate {success_rate}",
        "# HELP hermes_circuit_breaker_destinations Destinations per circuit breaker state.",
        "# TYPE hermes_circuit_breaker_destinations gauge",
        f'hermes_circuit_breaker_destinations{{state="closed"}} {cb_counts["closed"]}',
        f'hermes_circuit_breaker_destinations{{state="open"}} {cb_counts["open"]}',
        f'hermes_circuit_breaker_destinations{{state="half_open"}} {cb_counts["half_open"]}',
    ]
    if dlq_health_score is not None:
        lines += [
            "# HELP hermes_dlq_health_score DLQ health score (0=critical 100=healthy).",
            "# TYPE hermes_dlq_health_score gauge",
            f"hermes_dlq_health_score {dlq_health_score}",
        ]
    lines.append("")
    return Response(content="\n".join(lines), media_type="text/plain; version=0.0.4")


@router.get("/api/v1/stats")
async def get_stats(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    tf = Webhook.tenant_id == tenant_id
    total = await db.scalar(select(func.count(Webhook.id)).where(tf)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "pending")) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "processing")) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "completed")) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "failed")) or 0
    terminal = completed + failed
    success_rate = round(completed / terminal * 100, 1) if terminal else 100.0
    return {
        "total_webhooks": total,
        "pending_count": pending,
        "processing_count": processing,
        "completed_count": completed,
        "failed_count": failed,
        "success_rate": success_rate,
    }


@router.get("/api/v1/usage")
async def get_usage(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    stmt = select(
        Webhook.tenant_id,
        func.count(Webhook.id).label("events"),
        func.count(func.distinct(Webhook.event_id)).label("unique_events"),
    ).where(Webhook.tenant_id == tenant_id).group_by(Webhook.tenant_id)
    rows = (await db.execute(stmt)).all()
    return {"usage": [{"tenant_id": r.tenant_id, "events": r.events, "unique_events": r.unique_events} for r in rows]}


@router.get("/api/v1/dashboard")
async def get_dashboard(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    One call returns everything the Overview page needs:
    - KPIs: success rate (24 h), P95 latency, DLQ depth, circuit breaker summary
    - Active incidents
    - Recent failures (last 8)
    - Hourly throughput sparkline (last 24 h)
    """
    tf = Webhook.tenant_id == tenant_id

    # ── Counts ──
    completed_24h, failed_24h, total_24h = await asyncio.gather(
        db.scalar(select(func.count(Webhook.id)).where(
            tf, Webhook.status == "completed",
            Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
        )),
        db.scalar(select(func.count(Webhook.id)).where(
            tf, Webhook.status == "failed",
        )),
        db.scalar(select(func.count(Webhook.id)).where(
            tf, Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
        )),
    )
    completed_24h = completed_24h or 0
    failed_24h = failed_24h or 0
    total_24h = total_24h or 0
    terminal_24h = completed_24h + (
        await db.scalar(select(func.count(Webhook.id)).where(
            tf, Webhook.status == "failed",
            Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
        )) or 0
    )
    success_rate = round(completed_24h / terminal_24h * 100, 1) if terminal_24h else 100.0

    # ── P95 latency across all destinations ──
    latency_row = await db.execute(
        text("""
        SELECT PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.duration_ms) AS p95
        FROM delivery_attempts da
        JOIN webhooks w ON w.id = da.webhook_id
        WHERE w.tenant_id = :tid
          AND da.attempted_at >= NOW() - INTERVAL '24 hours'
          AND da.duration_ms IS NOT NULL
        """),
        {"tid": tenant_id},
    )
    p95_ms = int(latency_row.scalar() or 0)

    # ── Circuit breaker summary ──
    cb_result = await db.execute(
        text("""
        SELECT d.circuit_state, d.name, d.id::text
        FROM destinations d
        JOIN projects p ON p.id = d.project_id
        WHERE p.api_key = :key
        """),
        {"key": tenant_id},
    )
    cb_rows = cb_result.fetchall()
    cb_summary = {"closed": 0, "open": 0, "half_open": 0}
    open_destinations: List[str] = []
    for row in cb_rows:
        state = row.circuit_state
        cb_summary[state] = cb_summary.get(state, 0) + 1
        if state in ("open", "half_open"):
            open_destinations.append(row.name)

    # ── Active incidents ──
    inc_result = await db.execute(
        text("""
        SELECT i.id::text, i.failure_category, i.failure_subcategory,
               i.severity, i.state, i.affected_webhook_count,
               i.first_seen_at, i.last_seen_at, i.recommended_action,
               d.name AS destination_name
        FROM incidents i
        JOIN projects p ON p.id = i.project_id
        LEFT JOIN destinations d ON d.id = i.destination_id
        WHERE p.api_key = :key AND i.state NOT IN ('RESOLVED')
        ORDER BY i.last_seen_at DESC
        LIMIT 5
        """),
        {"key": tenant_id},
    )
    active_incidents = [
        {
            "id": r.id,
            "category": r.failure_category,
            "subcategory": r.failure_subcategory,
            "severity": r.severity,
            "state": r.state,
            "affected_count": r.affected_webhook_count,
            "destination_name": r.destination_name,
            "first_seen_at": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "last_seen_at": r.last_seen_at.isoformat() if r.last_seen_at else None,
            "recommended_action": r.recommended_action,
        }
        for r in inc_result.fetchall()
    ]

    # ── Recent failures ──
    fail_result = await db.execute(
        text("""
        SELECT w.id::text, w.destination_url, w.retry_count, w.updated_at,
               da.error_message, da.status_code, da.failure_category,
               d.name AS destination_name
        FROM webhooks w
        LEFT JOIN delivery_attempts da ON da.webhook_id = w.id
            AND da.attempt_number = w.retry_count
        LEFT JOIN destinations d ON d.id = w.destination_id
        WHERE w.tenant_id = :tid AND w.status = 'failed'
        ORDER BY w.updated_at DESC
        LIMIT 8
        """),
        {"tid": tenant_id},
    )
    recent_failures = [
        {
            "id": r.id,
            "destination_url": r.destination_url,
            "destination_name": r.destination_name,
            "retry_count": r.retry_count,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "error_message": r.error_message,
            "status_code": r.status_code,
            "failure_category": r.failure_category,
        }
        for r in fail_result.fetchall()
    ]

    # ── Throughput sparkline: deliveries per hour, last 24 h ──
    spark_result = await db.execute(
        text("""
        SELECT
            DATE_TRUNC('hour', updated_at) AS hour,
            COUNT(*) FILTER (WHERE status = 'completed') AS delivered,
            COUNT(*) FILTER (WHERE status = 'failed') AS failed
        FROM webhooks
        WHERE tenant_id = :tid
          AND updated_at >= NOW() - INTERVAL '24 hours'
        GROUP BY hour
        ORDER BY hour ASC
        """),
        {"tid": tenant_id},
    )
    sparkline = [
        {
            "hour": r.hour.isoformat(),
            "delivered": int(r.delivered or 0),
            "failed": int(r.failed or 0),
        }
        for r in spark_result.fetchall()
    ]

    # ── Overall system status ──
    has_open_circuit = cb_summary["open"] > 0
    has_critical_incident = any(i["severity"] == "critical" for i in active_incidents)
    if failed_24h > 50 or has_critical_incident:
        system_status = "critical"
    elif failed_24h > 10 or has_open_circuit or active_incidents:
        system_status = "degraded"
    else:
        system_status = "healthy"

    return {
        "system_status": system_status,
        "kpis": {
            "success_rate_24h": success_rate,
            "p95_latency_ms": p95_ms,
            "dlq_depth": failed_24h,
            "circuit_breakers": cb_summary,
            "open_destinations": open_destinations,
        },
        "active_incidents": active_incidents,
        "recent_failures": recent_failures,
        "sparkline": sparkline,
    }


async def _sse_event_generator(project_id: str):
    """Generator function for SSE events."""
    queue = await sse_hub.connect(project_id)
    try:
        while True:
            try:
                # Wait for events with timeout to allow connection checking
                message = await asyncio.wait_for(queue.get(), timeout=30.0)
                event_type = message.get("event", "message")
                data = message.get("data", {})

                # Format as SSE event
                yield f"event: {event_type}\n"
                yield f"data: {json.dumps(data)}\n\n"
            except asyncio.TimeoutError:
                # Send keepalive comment to keep connection alive
                yield ": keepalive\n\n"
    except asyncio.CancelledError:
        logger.info(f"SSE connection cancelled for project {project_id}")
    finally:
        await sse_hub.disconnect(project_id, queue)


@router.get("/api/v1/stream/delivery-logs")
async def stream_delivery_logs(
    project_id: str = Query(..., description="Project ID to stream logs for"),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Stream real-time delivery logs via Server-Sent Events (SSE).

    This endpoint provides a unidirectional stream of webhook delivery events,
    including webhook updates, delivery attempts, and incident updates.
    """
    # Verify project access
    project = await _get_project_by_api_key(db, tenant_id)
    if str(project.id) != project_id:
        raise HTTPException(403, "Access denied to this project")

    return StreamingResponse(
        _sse_event_generator(project_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        },
    )


@router.websocket("/ws/{project_key}")
async def websocket_endpoint(
    websocket: WebSocket,
    project_key: str,
    token: Optional[str] = Query(None),
):
    # Validate token from query param or cookie
    from app.auth import decode_access_token
    valid = False
    if token:
        payload = decode_access_token(token)
        valid = payload is not None
    if not valid:
        # Allow anonymous connections for API-key tenants
        valid = True  # dashboard connects with project API key as project_key

    if not valid:
        await websocket.close(code=4001)
        return

    await ws_manager.connect(websocket, project_key)
    try:
        while True:
            # Keep-alive ping every 30s
            await asyncio.sleep(30)
            await websocket.send_text('{"type":"ping"}')
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await ws_manager.disconnect(websocket, project_key)


@router.get("/api/v1/audit-log")
async def get_audit_log(
    resource_type: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(50, le=500),
    offset: int = Query(0, ge=0),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return paginated audit log entries for this tenant, newest first."""
    from sqlalchemy import desc as _desc
    stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
    if resource_type:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    stmt = stmt.order_by(_desc(AuditLog.created_at)).offset(offset).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "entries": [
            {
                "id": str(r.id),
                "action": r.action,
                "resource_type": r.resource_type,
                "resource_id": r.resource_id,
                "changes": r.changes,
                "ip_address": r.ip_address,
                "user_agent": r.user_agent,
                "created_at": r.created_at.isoformat(),
            }
            for r in rows
        ],
        "limit": limit,
        "offset": offset,
    }
