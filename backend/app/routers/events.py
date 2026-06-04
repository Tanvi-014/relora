"""Event journey endpoint — trace a single event_id across all deliveries."""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.models import DeliveryAttempt, Destination, Webhook

router = APIRouter()


@router.get("/api/v1/events/{event_id}/journey")
async def event_journey(
    event_id: str,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Return the full delivery journey for a single event_id.

    Groups all webhook records that share this event_id (fan-out creates one
    per destination) and includes every delivery attempt for each, ordered
    chronologically so you can see the complete lifecycle in one view.
    """
    wh_result = await db.execute(
        select(Webhook)
        .where(Webhook.event_id == event_id, Webhook.tenant_id == tenant_id)
        .order_by(Webhook.created_at)
    )
    webhooks = wh_result.scalars().all()

    if not webhooks:
        raise HTTPException(404, f"No webhooks found for event_id={event_id!r}")

    deliveries = []
    for wh in webhooks:
        attempts_result = await db.execute(
            select(DeliveryAttempt)
            .where(DeliveryAttempt.webhook_id == wh.id)
            .order_by(DeliveryAttempt.attempt_number)
        )
        attempts = attempts_result.scalars().all()

        # Resolve destination name
        dest_name = None
        if wh.destination_id:
            dr = await db.execute(select(Destination.name).where(Destination.id == wh.destination_id))
            dest_name = dr.scalar_one_or_none()

        deliveries.append({
            "webhook_id": str(wh.id),
            "destination_url": wh.destination_url,
            "destination_name": dest_name,
            "status": wh.status,
            "retry_count": wh.retry_count,
            "created_at": wh.created_at.isoformat() if wh.created_at else None,
            "updated_at": wh.updated_at.isoformat() if wh.updated_at else None,
            "attempts": [
                {
                    "attempt_number": a.attempt_number,
                    "status_code": a.status_code,
                    "duration_ms": a.duration_ms,
                    "error_message": a.error_message,
                    "failure_category": a.failure_category,
                    "attempted_at": a.attempted_at.isoformat() if a.attempted_at else None,
                }
                for a in attempts
            ],
        })

    first = webhooks[0]
    return {
        "event_id": event_id,
        "tenant_id": tenant_id,
        "ingest_time": first.created_at.isoformat() if first.created_at else None,
        "destination_count": len(deliveries),
        "overall_status": _overall_status(deliveries),
        "deliveries": deliveries,
    }


def _overall_status(deliveries: list) -> str:
    statuses = {d["status"] for d in deliveries}
    if statuses == {"completed"}:
        return "all_delivered"
    if "failed" in statuses and "completed" not in statuses:
        return "all_failed"
    if "failed" in statuses or "pending" in statuses or "processing" in statuses:
        return "partial"
    return "unknown"
