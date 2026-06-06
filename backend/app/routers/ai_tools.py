from typing import Any, Dict

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai_intelligence import analyze_advisor, analyze_payload, suggest_filter, suggest_transform
from app.auth import get_tenant_from_auth
from app.config import settings
from app.db import get_db

router = APIRouter()


@router.post("/api/v1/ai/analyze-payload")
async def ai_analyze(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled. Set ENABLE_AI_FEATURES=true and ANTHROPIC_API_KEY.")
    payload = body.get("payload", body)
    result = await analyze_payload(payload)
    return result


@router.post("/api/v1/ai/suggest-filter")
async def ai_suggest_filter(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    expr = await suggest_filter(body.get("description", ""), body.get("sample_payload", {}))
    return {"expression": expr}


@router.post("/api/v1/ai/suggest-transform")
async def ai_suggest_transform(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    code = await suggest_transform(body.get("description", ""), body.get("sample_payload", {}))
    return {"transform_code": code}


@router.get("/api/v1/ai/advisor")
async def ai_advisor(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Gather infrastructure telemetry for this tenant and return Claude's analysis
    as structured recommendations. Falls back gracefully when AI is disabled.
    """
    # ── Telemetry queries (mirrors dashboard but lighter) ──────────────────
    counts = await db.execute(
        text("""
        SELECT
            COUNT(*) FILTER (WHERE status = 'completed'
                AND updated_at >= NOW() - INTERVAL '24 hours') AS completed_24h,
            COUNT(*) FILTER (WHERE status = 'failed'
                AND updated_at >= NOW() - INTERVAL '24 hours') AS failed_24h,
            COUNT(*) FILTER (WHERE status = 'failed') AS dlq_depth,
            COUNT(*) FILTER (WHERE updated_at >= NOW() - INTERVAL '24 hours') AS total_24h
        FROM webhooks WHERE tenant_id = :tid
        """),
        {"tid": tenant_id},
    )
    row = counts.fetchone()
    completed_24h = int(row.completed_24h or 0)
    failed_24h    = int(row.failed_24h or 0)
    dlq_depth     = int(row.dlq_depth or 0)
    total_24h     = int(row.total_24h or 0)
    terminal      = completed_24h + failed_24h
    success_rate  = round(completed_24h / terminal * 100, 1) if terminal else 100.0

    p95_row = await db.execute(
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
    p95_ms = int(p95_row.scalar() or 0)

    dest_rows = await db.execute(
        text("""
        SELECT d.name, d.circuit_state, d.is_enabled
        FROM destinations d
        JOIN projects p ON p.id = d.project_id
        WHERE p.api_key = :key
        """),
        {"key": tenant_id},
    )
    destinations_raw = dest_rows.fetchall()
    dest_list = [
        {"name": r.name, "circuit_state": r.circuit_state, "enabled": r.is_enabled}
        for r in destinations_raw
    ]
    cb_open = sum(1 for d in dest_list if d["circuit_state"] in ("open", "half_open"))

    inc_rows = await db.execute(
        text("""
        SELECT i.failure_category, i.failure_subcategory, i.severity,
               i.affected_webhook_count, i.first_seen_at, i.last_seen_at,
               d.name AS destination_name
        FROM incidents i
        JOIN projects p ON p.id = i.project_id
        LEFT JOIN destinations d ON d.id = i.destination_id
        WHERE p.api_key = :key AND i.state NOT IN ('RESOLVED')
        ORDER BY i.last_seen_at DESC LIMIT 5
        """),
        {"key": tenant_id},
    )
    incidents = [
        {
            "category": r.failure_category,
            "subcategory": r.failure_subcategory,
            "severity": r.severity,
            "affected": r.affected_webhook_count,
            "destination": r.destination_name,
            "first_seen": r.first_seen_at.isoformat() if r.first_seen_at else None,
            "last_seen":  r.last_seen_at.isoformat()  if r.last_seen_at  else None,
        }
        for r in inc_rows.fetchall()
    ]

    fail_rows = await db.execute(
        text("""
        SELECT da.failure_category, COUNT(*) AS cnt, d.name AS dest_name
        FROM webhooks w
        LEFT JOIN delivery_attempts da ON da.webhook_id = w.id
            AND da.attempt_number = w.retry_count
        LEFT JOIN destinations d ON d.id = w.destination_id
        WHERE w.tenant_id = :tid AND w.status = 'failed'
          AND w.updated_at >= NOW() - INTERVAL '24 hours'
        GROUP BY da.failure_category, d.name
        ORDER BY cnt DESC LIMIT 8
        """),
        {"tid": tenant_id},
    )
    failure_breakdown = [
        {"category": r.failure_category or "unknown", "count": int(r.cnt), "destination": r.dest_name}
        for r in fail_rows.fetchall()
    ]

    # ── Build telemetry context for Claude ──────────────────────────────────
    telemetry = {
        "success_rate_24h_pct": success_rate,
        "deliveries_24h": completed_24h,
        "failures_24h": failed_24h,
        "dlq_depth": dlq_depth,
        "p95_latency_ms": p95_ms,
        "circuit_breakers_open": cb_open,
        "total_destinations": len(dest_list),
        "destinations": dest_list,
        "active_incidents": incidents,
        "failure_breakdown_24h": failure_breakdown,
    }

    result = await analyze_advisor(telemetry)
    return result
