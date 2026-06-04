import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.auth import get_tenant_from_auth
from app.db import get_db

router = APIRouter()


@router.post("/api/v1/consumers/{consumer_id}/poll")
async def poll_events(
    consumer_id: str,
    limit: int = Query(100, le=500),
    ack_token: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)

    # Acknowledge previously polled batch
    if ack_token:
        await db.execute(
            text("""
            UPDATE webhooks SET status='completed', updated_at=NOW()
            WHERE poll_ack_token = :token AND tenant_id = :tid
            """),
            {"token": ack_token, "tid": tenant_id},
        )
        await db.commit()

    new_ack_token = str(_uuid_mod.uuid4())

    result = await db.execute(
        text("""
        UPDATE webhooks
        SET poll_ack_token = :ack_token, status = 'processing', updated_at = NOW()
        WHERE id IN (
          SELECT id FROM webhooks
          WHERE tenant_id = :tid
            AND consumer_id = :consumer_id
            AND status = 'pending'
          ORDER BY created_at ASC
          LIMIT :limit
          FOR UPDATE SKIP LOCKED
        )
        RETURNING id, payload, destination_url, created_at, headers, event_id
        """),
        {"tid": tenant_id, "consumer_id": consumer_id, "limit": limit, "ack_token": new_ack_token},
    )
    await db.commit()

    rows = result.fetchall()
    events = [
        {
            "id": str(r.id),
            "payload": r.payload,
            "destination_url": r.destination_url,
            "event_id": r.event_id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]

    return {
        "events": events,
        "ack_token": new_ack_token if events else None,
        "count": len(events),
    }
