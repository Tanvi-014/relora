import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.db import get_db

logger = logging.getLogger("relora.api")
router = APIRouter()

# In-memory per-tenant inbox: tenant_id -> {token, events[]}
_inbox: Dict[str, Dict[str, Any]] = {}
_token_to_tenant: Dict[str, str] = {}
_MAX_EVENTS = 50


def _get_or_create(tenant_id: str) -> Dict[str, Any]:
    if tenant_id not in _inbox:
        token = secrets.token_urlsafe(16)
        _inbox[tenant_id] = {"token": token, "events": []}
        _token_to_tenant[token] = tenant_id
    return _inbox[tenant_id]


@router.post("/api/v1/test-inbox/create")
async def create_inbox(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    inbox = _get_or_create(tenant_id)
    return {"token": inbox["token"]}


@router.get("/api/v1/test-inbox/events")
async def get_events(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    inbox = _get_or_create(tenant_id)
    return {"token": inbox["token"], "events": inbox["events"]}


@router.delete("/api/v1/test-inbox/clear")
async def clear_inbox(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    inbox = _get_or_create(tenant_id)
    inbox["events"] = []
    return {"ok": True}


@router.post("/api/v1/test-inbox/send")
async def send_to_inbox(
    request: Request,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Send a custom payload directly into the tenant's test inbox (server-side, no HTTP hop)."""
    try:
        body = await request.body()
        try:
            payload = json.loads(body)
        except Exception:
            payload = body.decode("utf-8", errors="replace")
    except Exception:
        payload = {}

    event_type = request.headers.get("X-Event-Type", "test.event")
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in ("host", "content-length", "connection", "cookie", "authorization",
                                    "x-project-id", "x-api-key", "x-relora-api-key")}

    inbox = _get_or_create(tenant_id)
    event = {
        "id": secrets.token_hex(8),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "headers": headers,
    }
    inbox["events"].insert(0, event)
    if len(inbox["events"]) > _MAX_EVENTS:
        inbox["events"] = inbox["events"][:_MAX_EVENTS]

    return {"ok": True, "id": event["id"]}


@router.post("/api/v1/test-inbox/demo")
async def send_demo(
    request: Request,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    inbox = _get_or_create(tenant_id)
    token = inbox["token"]
    base = str(request.base_url).rstrip("/")
    target = f"{base}/inbox/{token}"

    payload = {
        "event": "payment.succeeded",
        "id": f"evt_{secrets.token_hex(8)}",
        "amount": 4999,
        "currency": "usd",
        "customer": "cus_demo123",
        "created": int(datetime.now(timezone.utc).timestamp()),
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                target,
                json=payload,
                headers={"Content-Type": "application/json", "X-Event-Type": "payment.succeeded", "X-Source": "relora-demo"},
            )
    except Exception as exc:
        logger.warning("Test inbox demo send failed: %s", exc)
        raise HTTPException(500, "Failed to send demo event")

    return {"ok": True}


@router.post("/inbox/{token}")
async def receive(token: str, request: Request):
    """Public — accepts any POST and logs it to the inbox."""
    tenant_id = _token_to_tenant.get(token)
    if not tenant_id:
        raise HTTPException(404, "Inbox not found")

    try:
        body = await request.body()
        try:
            payload = json.loads(body)
        except Exception:
            payload = body.decode("utf-8", errors="replace")
    except Exception:
        payload = {}

    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length", "connection")}

    event = {
        "id": secrets.token_hex(8),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "payload": payload,
        "headers": headers,
    }

    inbox = _inbox[tenant_id]
    inbox["events"].insert(0, event)
    if len(inbox["events"]) > _MAX_EVENTS:
        inbox["events"] = inbox["events"][:_MAX_EVENTS]

    return {"ok": True, "id": event["id"]}
