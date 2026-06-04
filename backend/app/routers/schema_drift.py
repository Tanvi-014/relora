"""Schema drift endpoints — list changes and acknowledge them."""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.models import SchemaChange, SchemaFingerprint

router = APIRouter()


@router.get("/api/v1/schema-changes")
async def list_schema_changes(
    unacknowledged_only: bool = Query(True),
    limit: int = Query(50, le=200),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """List detected schema changes for this tenant, newest first."""
    stmt = select(SchemaChange).where(SchemaChange.tenant_id == tenant_id)
    if unacknowledged_only:
        stmt = stmt.where(SchemaChange.acknowledged_at.is_(None))
    stmt = stmt.order_by(desc(SchemaChange.detected_at)).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {
        "total": len(rows),
        "changes": [
            {
                "id": str(r.id),
                "source_key": r.source_key,
                "added_keys": r.added_keys or [],
                "removed_keys": r.removed_keys or [],
                "detected_at": r.detected_at.isoformat(),
                "acknowledged_at": r.acknowledged_at.isoformat() if r.acknowledged_at else None,
            }
            for r in rows
        ],
    }


@router.post("/api/v1/schema-changes/{change_id}/acknowledge")
async def acknowledge_change(
    change_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Mark a schema change as acknowledged (dismiss from dashboard)."""
    result = await db.execute(
        select(SchemaChange).where(
            SchemaChange.id == change_id,
            SchemaChange.tenant_id == tenant_id,
        )
    )
    change = result.scalar_one_or_none()
    if not change:
        raise HTTPException(404, "Schema change not found")
    change.acknowledged_at = datetime.now(timezone.utc)
    await db.commit()
    return {"acknowledged": True, "change_id": str(change_id)}


@router.get("/api/v1/schema-fingerprints")
async def list_fingerprints(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """List all tracked payload schemas for this tenant."""
    rows = (await db.execute(
        select(SchemaFingerprint)
        .where(SchemaFingerprint.tenant_id == tenant_id)
        .order_by(desc(SchemaFingerprint.last_seen_at))
    )).scalars().all()
    return [
        {
            "source_key": r.source_key,
            "fingerprint": r.fingerprint[:12] + "…",
            "key_count": len(r.key_structure or []),
            "key_structure": r.key_structure,
            "event_count": r.event_count,
            "first_seen_at": r.first_seen_at.isoformat(),
            "last_seen_at": r.last_seen_at.isoformat(),
        }
        for r in rows
    ]
