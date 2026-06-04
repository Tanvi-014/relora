"""SLO tracking endpoints — configure targets and query live status."""
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.models import Destination, Project, Webhook, WebhookStatus

router = APIRouter()


async def _get_dest(db: AsyncSession, dest_id: UUID, tenant_id: str) -> Destination:
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Destination.id == dest_id, Project.api_key == tenant_id)
    )
    dest = result.scalar_one_or_none()
    if not dest:
        raise HTTPException(404, "Destination not found")
    return dest


async def calculate_slo(
    db: AsyncSession,
    dest_id: UUID,
    window_minutes: int,
) -> dict:
    """Calculate current SLO metrics for a destination over the given window."""
    since = datetime.now(timezone.utc) - timedelta(minutes=window_minutes)
    rows = await db.execute(
        select(
            func.count(Webhook.id).label("total"),
            func.count(Webhook.id).filter(Webhook.status == WebhookStatus.COMPLETED.value).label("successful"),
            func.count(Webhook.id).filter(Webhook.status == WebhookStatus.FAILED.value).label("failed"),
            func.count(Webhook.id).filter(Webhook.status.in_(
                [WebhookStatus.PENDING.value, WebhookStatus.PROCESSING.value]
            )).label("in_flight"),
        ).where(
            Webhook.destination_id == dest_id,
            Webhook.created_at >= since,
        )
    )
    row = rows.one()
    total = row.total or 0
    successful = row.successful or 0
    terminal = total - (row.in_flight or 0)
    current_pct = round(successful / terminal * 100, 2) if terminal > 0 else 100.0
    return {
        "window_minutes": window_minutes,
        "since": since.isoformat(),
        "total": total,
        "successful": successful,
        "failed": row.failed or 0,
        "in_flight": row.in_flight or 0,
        "current_success_pct": current_pct,
    }


@router.get("/api/v1/destinations/{dest_id}/slo")
async def get_slo_status(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return live SLO status for a destination."""
    dest = await _get_dest(db, dest_id, tenant_id)
    window = dest.slo_window_minutes or 60
    metrics = await calculate_slo(db, dest_id, window)
    target = dest.slo_target_pct
    breached = target is not None and metrics["current_success_pct"] < target
    return {
        "destination_id": str(dest_id),
        "destination_name": dest.name,
        "slo_target_pct": target,
        "slo_window_minutes": window,
        "breached": breached,
        "margin_pct": round(metrics["current_success_pct"] - target, 2) if target else None,
        **metrics,
    }


@router.put("/api/v1/destinations/{dest_id}/slo")
async def set_slo(
    dest_id: UUID,
    target_pct: float,
    window_minutes: int = 60,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Set or update the SLO target for a destination."""
    if not (0 < target_pct <= 100):
        raise HTTPException(422, "target_pct must be between 0 and 100")
    if not (1 <= window_minutes <= 10080):
        raise HTTPException(422, "window_minutes must be between 1 and 10080 (one week)")
    dest = await _get_dest(db, dest_id, tenant_id)
    dest.slo_target_pct = target_pct
    dest.slo_window_minutes = window_minutes
    await db.commit()
    return {"destination_id": str(dest_id), "slo_target_pct": target_pct, "slo_window_minutes": window_minutes}


@router.delete("/api/v1/destinations/{dest_id}/slo")
async def clear_slo(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Remove the SLO target from a destination."""
    dest = await _get_dest(db, dest_id, tenant_id)
    dest.slo_target_pct = None
    await db.commit()
    return {"destination_id": str(dest_id), "slo_target_pct": None}


@router.get("/api/v1/slo/summary")
async def slo_summary(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return SLO status for every destination that has a target configured."""
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(
            Project.api_key == tenant_id,
            Destination.slo_target_pct.isnot(None),
        )
    )
    destinations = result.scalars().all()
    summaries = []
    for dest in destinations:
        metrics = await calculate_slo(db, dest.id, dest.slo_window_minutes or 60)
        breached = metrics["current_success_pct"] < dest.slo_target_pct
        summaries.append({
            "destination_id": str(dest.id),
            "destination_name": dest.name,
            "slo_target_pct": dest.slo_target_pct,
            "current_success_pct": metrics["current_success_pct"],
            "breached": breached,
            "margin_pct": round(metrics["current_success_pct"] - dest.slo_target_pct, 2),
        })
    return {
        "total": len(summaries),
        "breached": sum(1 for s in summaries if s["breached"]),
        "destinations": summaries,
    }
