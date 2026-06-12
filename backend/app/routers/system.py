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

logger = logging.getLogger("relora.api")

router = APIRouter()


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found for this API key")
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
        "# HELP relora_webhooks_total Total ingested webhooks.",
        "# TYPE relora_webhooks_total gauge",
        f"relora_webhooks_total {total}",
        "# HELP relora_webhooks_by_status Webhooks by delivery status.",
        "# TYPE relora_webhooks_by_status gauge",
        f'relora_webhooks_by_status{{status="pending"}} {pending}',
        f'relora_webhooks_by_status{{status="processing"}} {processing}',
        f'relora_webhooks_by_status{{status="completed"}} {completed}',
        f'relora_webhooks_by_status{{status="failed"}} {failed}',
        "# HELP relora_delivery_attempts_total Total delivery attempts.",
        "# TYPE relora_delivery_attempts_total gauge",
        f"relora_delivery_attempts_total {attempts}",
        "# HELP relora_delivery_success_rate Percentage of successfully delivered webhooks.",
        "# TYPE relora_delivery_success_rate gauge",
        f"relora_delivery_success_rate {success_rate}",
        "# HELP relora_circuit_breaker_destinations Destinations per circuit breaker state.",
        "# TYPE relora_circuit_breaker_destinations gauge",
        f'relora_circuit_breaker_destinations{{state="closed"}} {cb_counts["closed"]}',
        f'relora_circuit_breaker_destinations{{state="open"}} {cb_counts["open"]}',
        f'relora_circuit_breaker_destinations{{state="half_open"}} {cb_counts["half_open"]}',
    ]
    if dlq_health_score is not None:
        lines += [
            "# HELP relora_dlq_health_score DLQ health score (0=critical 100=healthy).",
            "# TYPE relora_dlq_health_score gauge",
            f"relora_dlq_health_score {dlq_health_score}",
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
    ns = Webhook.is_simulation == False  # noqa: E712
    completed_24h, failed_24h, total_24h = await asyncio.gather(
        db.scalar(select(func.count(Webhook.id)).where(
            tf, ns, Webhook.status == "completed",
            Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
        )),
        db.scalar(select(func.count(Webhook.id)).where(
            tf, ns, Webhook.status == "failed",
        )),
        db.scalar(select(func.count(Webhook.id)).where(
            tf, ns, Webhook.updated_at >= text("NOW() - INTERVAL '24 hours'"),
        )),
    )
    completed_24h = completed_24h or 0
    failed_24h = failed_24h or 0
    total_24h = total_24h or 0
    terminal_24h = completed_24h + (
        await db.scalar(select(func.count(Webhook.id)).where(
            tf, ns, Webhook.status == "failed",
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
        WHERE w.tenant_id = :tid AND w.status = 'failed' AND w.is_simulation = false
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

    # ── Last completed delivery time ──
    last_del_row = await db.execute(
        text("SELECT MAX(updated_at) FROM webhooks WHERE tenant_id = :tid AND status = 'completed'"),
        {"tid": tenant_id},
    )
    last_delivery_at = last_del_row.scalar()

    # ── Recent activity (for seeding the live feed) ──
    activity_result = await db.execute(
        text("""
        SELECT w.id::text, w.status, w.updated_at, w.destination_url,
               d.name AS destination_name
        FROM webhooks w
        LEFT JOIN destinations d ON d.id = w.destination_id
        WHERE w.tenant_id = :tid
          AND w.status IN ('completed', 'failed')
          AND w.is_simulation = false
          AND (d.is_sandbox IS NOT TRUE)
          AND w.updated_at >= NOW() - INTERVAL '24 hours'
        ORDER BY w.updated_at DESC
        LIMIT 20
        """),
        {"tid": tenant_id},
    )
    recent_activity = [
        {
            "id": r.id,
            "status": r.status,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "destination_url": r.destination_url,
            "destination_name": r.destination_name,
        }
        for r in activity_result.fetchall()
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
          AND is_simulation = false
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

    # ── Source vs destination health split ────────────────────────────────
    # Provider issue heuristic: if N≥2 distinct destinations fail on the same
    # event_id within 10 minutes, the problem is likely upstream, not local.
    provider_issue_result = await db.execute(
        text("""
        SELECT
            COUNT(DISTINCT w.destination_id) AS dest_count,
            COUNT(DISTINCT w.event_id)       AS event_count
        FROM webhooks w
        WHERE w.tenant_id = :tid
          AND w.status = 'failed'
          AND w.updated_at >= NOW() - INTERVAL '24 hours'
          AND w.event_id IS NOT NULL
          AND w.destination_id IS NOT NULL
        HAVING COUNT(DISTINCT w.destination_id) >= 2
        """),
        {"tid": tenant_id},
    )
    pi_row = provider_issue_result.fetchone()
    provider_issue_likely = pi_row is not None and (pi_row.dest_count or 0) >= 2

    has_open_circuit = cb_summary["open"] > 0

    source_health = "healthy"
    destination_health = "healthy"
    if provider_issue_likely:
        source_health = "degraded"
    if has_open_circuit or failed_24h > 10:
        destination_health = "degraded" if failed_24h <= 50 else "critical"

    # ── SLO summary ───────────────────────────────────────────────────────
    from app.routers.slo import calculate_slo as _calc_slo
    slo_result = await db.execute(
        text("""
        SELECT d.id, d.name, d.slo_target_pct, d.slo_window_minutes
        FROM destinations d
        JOIN projects p ON p.id = d.project_id
        WHERE p.api_key = :key AND d.slo_target_pct IS NOT NULL
        """),
        {"key": tenant_id},
    )
    slo_rows = slo_result.fetchall()
    slo_breaches = []
    for sr in slo_rows:
        metrics = await _calc_slo(db, sr.id, sr.slo_window_minutes or 60)
        if metrics["current_success_pct"] < sr.slo_target_pct:
            slo_breaches.append({
                "destination_name": sr.name,
                "target_pct": sr.slo_target_pct,
                "current_pct": metrics["current_success_pct"],
            })

    # ── Unacknowledged schema changes ─────────────────────────────────────
    from app.models import SchemaChange
    sc_count_result = await db.execute(
        select(func.count(SchemaChange.id)).where(
            SchemaChange.tenant_id == tenant_id,
            SchemaChange.acknowledged_at.is_(None),
        )
    )
    unacked_schema_changes = sc_count_result.scalar() or 0

    # ── Overall system status ──
    has_critical_incident = any(i["severity"] == "critical" for i in active_incidents)
    if failed_24h > 50 or has_critical_incident or slo_breaches:
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
        "health_split": {
            "source_health": source_health,
            "destination_health": destination_health,
            "provider_issue_likely": provider_issue_likely,
        },
        "slo_breaches": slo_breaches,
        "unacked_schema_changes": unacked_schema_changes,
        "active_incidents": active_incidents,
        "recent_failures": recent_failures,
        "recent_activity": recent_activity,
        "deliveries_today": completed_24h,
        "last_delivery_at": last_delivery_at.isoformat() if last_delivery_at else None,
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
    """
    Authenticated WebSocket stream.

    Accepts either:
      - ?token=<JWT>  — validated, then the sub must be a member of the project
        whose api_key == project_key.
      - project_key is itself a valid project api_key (SDK / programmatic use).

    Closes with 4001 if neither check passes.
    """
    from app.auth import decode_access_token
    from app.db import async_session as _session
    from sqlalchemy import select as _select

    authorized = False

    async with _session() as db:
        if token:
            payload = decode_access_token(token)
            if payload:
                user_id = payload.get("sub")
                if user_id:
                    # Verify the user is a member of the project identified by project_key
                    pr = await db.execute(
                        _select(Project).where(Project.api_key == project_key)
                    )
                    project = pr.scalar_one_or_none()
                    if project:
                        mr = await db.execute(
                            _select(ProjectMember).where(
                                ProjectMember.project_id == project.id,
                                ProjectMember.user_id == user_id,
                            )
                        )
                        if mr.scalar_one_or_none():
                            authorized = True

        if not authorized:
            # Fallback: project_key is the raw project api_key (SDK clients)
            pr = await db.execute(
                _select(Project).where(Project.api_key == project_key)
            )
            if pr.scalar_one_or_none():
                authorized = True

    if not authorized:
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


@router.delete("/api/v1/data/test")
async def clear_test_data(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete all simulated/test webhook events for this tenant."""
    from sqlalchemy import delete as _delete
    result = await db.execute(
        _delete(Webhook)
        .where(Webhook.tenant_id == tenant_id, Webhook.is_simulation == True)
        .returning(Webhook.id)
    )
    deleted = len(result.fetchall())
    await db.commit()
    return {"deleted": deleted, "message": f"Cleared {deleted} test event{'s' if deleted != 1 else ''}"}


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
