"""
Onboarding router — sandbox inbox endpoint + demo event helpers.
"""
import uuid as _uuid_mod
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.config import settings
from app.db import get_db
from app.models import Destination, Project, Webhook

router = APIRouter(tags=["onboarding"])
logger = logging.getLogger("relora.api")


@router.post("/api/v1/sandbox/inbox", status_code=200)
async def sandbox_inbox():
    """
    Internal sandbox delivery target.
    Always returns 200 so the worker records a successful delivery.
    No auth — the URL itself is the secret (project-unguessable via worker delivery).
    """
    return {"ok": True, "message": "Relora sandbox received your event."}


@router.get("/api/v1/onboarding/state")
async def get_onboarding_state(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Return current onboarding state for the active project."""
    sandbox = (await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Project.api_key == tenant_id, Destination.is_sandbox.is_(True))
    )).scalar_one_or_none()

    real_dest = (await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Project.api_key == tenant_id, Destination.is_sandbox.is_(False))
        .limit(1)
    )).scalar_one_or_none()

    sandbox_deliveries = 0
    if sandbox:
        result = await db.execute(
            select(Webhook).where(
                Webhook.destination_id == sandbox.id,
                Webhook.status == "completed",
            )
        )
        sandbox_deliveries = len(result.scalars().all())

    return {
        "sandbox_destination": sandbox.to_dict() if sandbox else None,
        "has_real_destination": real_dest is not None,
        "sandbox_deliveries": sandbox_deliveries,
        "ingest_url": f"{settings.APP_BASE_URL}/api/v1/ingest",
    }


@router.post("/api/v1/onboarding/send-demo", status_code=201)
async def send_demo_event(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a demo webhook routed to the sandbox destination."""
    sandbox = (await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Project.api_key == tenant_id, Destination.is_sandbox.is_(True))
    )).scalar_one_or_none()

    if not sandbox:
        raise HTTPException(
            404,
            "No sandbox destination found. Your project may have been set up before this feature was added — "
            "create a destination first.",
        )

    webhook = Webhook(
        tenant_id=tenant_id,
        event_id=f"demo_{_uuid_mod.uuid4().hex[:10]}",
        destination_url=sandbox.url,
        destination_id=sandbox.id,
        payload={
            "event_type": "relora.demo",
            "data": {
                "message": "Your first event through Relora!",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "source": "onboarding",
            },
        },
        headers={"Content-Type": "application/json", "X-Relora-Demo": "true"},
        is_simulation=True,
        max_retries=0,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)

    logger.info(
        "Onboarding demo event queued",
        extra={"event": "onboarding.demo_sent", "tenant_id": tenant_id, "webhook_id": str(webhook.id)},
    )
    return {
        "webhook_id": str(webhook.id),
        "event_id": webhook.event_id,
        "status": "queued",
    }


@router.get("/api/v1/onboarding/progress")
async def get_onboarding_progress(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    3-step setup checklist derived entirely from real product state.

    Step 1 — Create Destination: at least one non-sandbox destination exists
    Step 2 — Send Test Webhook:  at least one ingest event has been received
    Step 3 — Verify Delivery:    at least one delivery completed successfully
    """
    project = (await db.execute(
        select(Project).where(Project.api_key == tenant_id)
    )).scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    dest_count = (await db.execute(
        select(func.count(Destination.id))
        .where(
            Destination.project_id == project.id,
            Destination.is_sandbox.is_(False),
        )
    )).scalar() or 0

    webhook_count = (await db.execute(
        select(func.count(Webhook.id))
        .where(Webhook.tenant_id == tenant_id)
    )).scalar() or 0

    completed_count = (await db.execute(
        select(func.count(Webhook.id))
        .where(
            Webhook.tenant_id == tenant_id,
            Webhook.status == "completed",
        )
    )).scalar() or 0

    step_1 = dest_count > 0
    step_2 = webhook_count > 0
    step_3 = completed_count > 0
    activated = step_1 and step_2 and step_3

    return {
        "steps": [
            {
                "id": 1,
                "title": "Create Destination",
                "completed": step_1,
            },
            {
                "id": 2,
                "title": "Send Test Webhook",
                "completed": step_2,
            },
            {
                "id": 3,
                "title": "Verify Delivery",
                "completed": step_3,
            },
        ],
        "progress_percent": round((sum([step_1, step_2, step_3]) / 3) * 100),
        "activated": activated,
    }

