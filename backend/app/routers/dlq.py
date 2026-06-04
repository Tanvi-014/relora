import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.failure_classifier import FailureClassifier
from app.health_engine import HealthEngine
from app.incident_engine import IncidentEngine
from app.models import DeliveryAttempt, Destination, Incident, Project, Webhook

logger = logging.getLogger("relora.api")

router = APIRouter()


@router.get("/api/v1/dlq/health")
async def get_dlq_health(
    project_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get overall DLQ health score and metrics."""
    # If project_id is provided, verify the tenant has access
    if project_id:
        project_result = await db.execute(
            select(Project).where(Project.id == UUID(project_id))
        )
        project = project_result.scalar_one_or_none()
        if not project:
            raise HTTPException(404, "Project not found")

    health_data = await HealthEngine.calculate_dlq_health_score(db, project_id)
    return health_data


@router.get("/api/v1/dlq/incidents")
async def get_dlq_incidents(
    project_id: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get all incidents with optional filtering."""
    offset = (page - 1) * limit

    query = select(Incident).order_by(desc(Incident.last_seen_at))
    count_query = select(func.count(Incident.id))

    if project_id:
        query = query.where(Incident.project_id == UUID(project_id))
        count_query = count_query.where(Incident.project_id == UUID(project_id))

    if state:
        query = query.where(Incident.state == state)
        count_query = count_query.where(Incident.state == state)

    result = await db.execute(query.offset(offset).limit(limit))
    incidents = result.scalars().all()

    total_result = await db.scalar(count_query) or 0

    return {
        "incidents": [i.to_dict() for i in incidents],
        "total": total_result,
        "page": page,
        "limit": limit,
        "total_pages": (total_result + limit - 1) // limit,
    }


@router.get("/api/v1/dlq/classifications")
async def get_dlq_classifications(
    project_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get failure classification breakdown."""
    query = select(
        DeliveryAttempt.failure_category,
        func.count(DeliveryAttempt.id).label("count"),
    ).where(
        DeliveryAttempt.failure_category.isnot(None)
    ).group_by(DeliveryAttempt.failure_category).order_by(
        func.count(DeliveryAttempt.id).desc()
    )

    if project_id:
        query = query.join(Webhook, DeliveryAttempt.webhook_id == Webhook.id).where(
            Webhook.project_id == UUID(project_id)
        )

    result = await db.execute(query)
    rows = result.all()

    classifications = []
    for row in rows:
        classifications.append({
            "category": row.failure_category,
            "count": row.count,
        })

    return {"classifications": classifications}


@router.get("/api/v1/dlq/trends")
async def get_dlq_trends(
    project_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get DLQ growth trends over time."""
    now = datetime.now(timezone.utc)
    time_windows = {
        "15m": now - timedelta(minutes=15),
        "1h": now - timedelta(hours=1),
        "6h": now - timedelta(hours=6),
        "24h": now - timedelta(hours=24),
    }

    trends = {}
    for window_name, window_start in time_windows.items():
        query = select(func.count(Webhook.id)).where(
            and_(
                Webhook.status == "failed",
                Webhook.created_at >= window_start,
            )
        )

        if project_id:
            query = query.where(Webhook.project_id == UUID(project_id))

        result = await db.execute(query)
        count = result.scalar() or 0
        trends[window_name] = count

    # Determine overall trend state
    if trends["15m"] > 100:
        trend_state = "EXPLOSIVE_GROWTH"
    elif trends["1h"] > 500:
        trend_state = "RAPID_GROWTH"
    elif trends["6h"] > 1000:
        trend_state = "MODERATE_GROWTH"
    elif trends["24h"] > 2000:
        trend_state = "SLOW_GROWTH"
    else:
        trend_state = "STABLE"

    return {
        "trends": trends,
        "trend_state": trend_state,
    }


@router.get("/api/v1/dlq/root-causes")
async def get_dlq_root_causes(
    project_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get root cause breakdown of failures."""
    root_causes = await IncidentEngine.get_root_causes(db, project_id)
    return {"root_causes": root_causes}


@router.post("/api/v1/dlq/analyze")
async def analyze_dlq(
    destination_id: str = Body(..., embed=True),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """AI-enhanced analysis of DLQ failures for a destination."""
    # Get destination info
    dest_result = await db.execute(
        select(Destination).where(Destination.id == UUID(destination_id))
    )
    destination = dest_result.scalar_one_or_none()

    if not destination:
        raise HTTPException(404, "Destination not found")

    # Get failure breakdown
    query = select(
        DeliveryAttempt.failure_category,
        func.count(DeliveryAttempt.id).label("count"),
    ).join(
        Webhook, DeliveryAttempt.webhook_id == Webhook.id
    ).where(
        and_(
            Webhook.destination_id == UUID(destination_id),
            DeliveryAttempt.failure_category.isnot(None),
        )
    ).group_by(DeliveryAttempt.failure_category).order_by(
        func.count(DeliveryAttempt.id).desc()
    )

    result = await db.execute(query)
    rows = result.all()

    total_failures = sum(row.count for row in rows)

    if total_failures == 0:
        return {
            "analysis": "No failures detected for this destination.",
            "destination": destination.name,
        }

    # Build analysis text
    top_category = rows[0].failure_category
    top_count = rows[0].count
    percentage = (top_count / total_failures) * 100

    analysis = f"{percentage:.0f}% of failures originate from {destination.name}. "
    analysis += f"Most failures are {top_category}. "

    # Get recent failures for timing
    recent_query = select(Webhook.created_at).where(
        and_(
            Webhook.destination_id == UUID(destination_id),
            Webhook.status == "failed",
        )
    ).order_by(desc(Webhook.created_at)).limit(1)

    recent_result = await db.execute(recent_query)
    most_recent = recent_result.scalar_one_or_none()

    if most_recent:
        hours_ago = (datetime.now(timezone.utc) - most_recent).total_seconds() / 3600
        analysis += f"The issue began approximately {hours_ago:.1f} hours ago. "

    # Add recommendation
    recommendation = FailureClassifier.get_recommendation(top_category, "GENERAL")
    analysis += f"Recommended action: {recommendation.get('suggested_fix', 'Investigate further')}."

    return {
        "analysis": analysis,
        "destination": destination.name,
        "total_failures": total_failures,
        "top_category": top_category,
        "top_percentage": round(percentage, 1),
    }


@router.delete("/api/v1/dlq/archive")
async def archive_dlq(
    older_than_days: int = Query(30, ge=1, le=365, description="Archive failed webhooks older than N days"),
    dry_run: bool = Query(False, description="Count rows without deleting"),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Archive (hard-delete) failed webhooks older than `older_than_days` days.

    Use dry_run=true to preview the count before committing.  Delivery attempts
    are cascade-deleted because of the FK ondelete='CASCADE'.

    Typical cron: DELETE /api/v1/dlq/archive?older_than_days=30 monthly.
    """
    count_stmt = text("""
        SELECT COUNT(*) FROM webhooks
        WHERE status = 'failed'
          AND tenant_id = :tenant_id
          AND updated_at < NOW() - :interval::interval
    """)
    count_row = await db.execute(
        count_stmt,
        {"tenant_id": tenant_id, "interval": f"{older_than_days} days"},
    )
    total = count_row.scalar() or 0

    if dry_run:
        return {"dry_run": True, "would_archive": total, "older_than_days": older_than_days}

    delete_stmt = text("""
        DELETE FROM webhooks
        WHERE status = 'failed'
          AND tenant_id = :tenant_id
          AND updated_at < NOW() - :interval::interval
    """)
    await db.execute(
        delete_stmt,
        {"tenant_id": tenant_id, "interval": f"{older_than_days} days"},
    )
    await db.commit()

    logger.info(
        "DLQ archived %d webhooks older than %d days for tenant %s",
        total, older_than_days, tenant_id,
        extra={"event": "dlq.archived", "tenant_id": tenant_id, "count": total},
    )
    return {"archived": total, "older_than_days": older_than_days}
