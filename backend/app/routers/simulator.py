import uuid as _uuid_mod
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.config import settings
from app.db import get_db
from app.models import Destination, Webhook, WebhookStatus
from app.security import validate_destination_url
from app.simulator import build_simulated_payload, list_providers

router = APIRouter()


@router.get("/api/v1/simulate/providers")
async def simulate_providers(tenant_id: str = Depends(get_tenant_from_auth)):
    return list_providers()


@router.post("/api/v1/simulate")
async def simulate_webhook(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    if not settings.ENABLE_SIMULATOR:
        raise HTTPException(403, "Simulator not enabled")
    provider = body.get("provider", "stripe")
    event_type = body.get("event_type", "payment_intent.succeeded")
    destination_id = body.get("destination_id")
    overrides = body.get("overrides", {})

    payload = build_simulated_payload(provider, event_type, overrides)
    if payload is None:
        raise HTTPException(400, f"Unknown provider/event: {provider}/{event_type}")

    dest_url = None
    dest_id_obj = None
    if destination_id:
        dest_result = await db.execute(select(Destination).where(Destination.id == UUID(destination_id)))
        dest_obj = dest_result.scalar_one_or_none()
        if not dest_obj:
            raise HTTPException(404, "Destination not found")
        dest_url = dest_obj.url
        dest_id_obj = dest_obj.id
    elif body.get("url"):
        dest_url = validate_destination_url(body["url"])
    else:
        raise HTTPException(400, "destination_id or url required")

    webhook = Webhook(
        tenant_id=tenant_id,
        event_id=payload.get("id", str(_uuid_mod.uuid4())),
        destination_url=dest_url,
        destination_id=dest_id_obj,
        payload=payload,
        headers={"X-Relora-Simulated": "true", "X-Relora-Provider": provider},
        idempotency_key=None,
        status=WebhookStatus.PENDING.value,
        max_retries=settings.DEFAULT_MAX_RETRIES,
        is_simulation=True,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return {"webhook_id": str(webhook.id), "payload": payload}
