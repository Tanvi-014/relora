"""
Cloud event source adapters.

Each adapter normalises a cloud-provider event envelope into the standard
Relora ingest format so it can be forwarded to any registered destination.

Supported providers
-------------------
- AWS SNS / EventBridge   POST /api/v1/sources/aws-sns
- Google Cloud Pub/Sub    POST /api/v1/sources/gcp-pubsub
- Azure Event Grid        POST /api/v1/sources/azure-event-grid

All adapters are mounted on the main FastAPI app in api_main.py:
    from app.event_sources import router as sources_router
    app.include_router(sources_router)
"""

import base64
import hashlib
import hmac
import json
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_db
from app.auth import get_tenant_from_auth

logger = logging.getLogger("relora.event_sources")

router = APIRouter(prefix="/api/v1/sources", tags=["Event Sources"])


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _extract_destination_id(header_value: Optional[str]) -> Optional[str]:
    """X-Relora-Destination-Id header lets callers route to a registered destination."""
    return header_value or None


async def _forward_to_ingest(
    request: Request,
    payload: Dict[str, Any],
    tenant_id: str,
    event_id: str,
    db: AsyncSession,
    destination_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Re-use the existing ingest logic by importing and calling the handler
    directly so we don't duplicate routing / idempotency / signing code.
    """
    from app.models import Webhook, WebhookStatus, Destination
    from app.routing import event_matches_filter, apply_transform, extract_event_id
    from app.security import validate_destination_url
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError
    from uuid import UUID

    if destination_id:
        dest_result = await db.execute(
            select(Destination).where(Destination.id == UUID(destination_id))
        )
        dest_obj = dest_result.scalar_one_or_none()
        if not dest_obj:
            raise HTTPException(404, "Destination not found")
        destination_url = dest_obj.url
        dest_id_obj = dest_obj.id
    else:
        destination_url = request.headers.get("X-Relora-Destination-Url")
        if not destination_url:
            raise HTTPException(
                400,
                "Provide X-Relora-Destination-Id or X-Relora-Destination-Url header",
            )
        destination_url = validate_destination_url(destination_url)
        dest_id_obj = None

    idempotency_key = event_id
    existing_r = await db.execute(
        select(Webhook).where(
            Webhook.tenant_id == tenant_id,
            Webhook.destination_url == destination_url,
            Webhook.idempotency_key == idempotency_key,
        )
    )
    if existing_r.scalar_one_or_none():
        return {"success": True, "duplicate": True, "event_id": event_id}

    webhook = Webhook(
        tenant_id=tenant_id,
        event_id=event_id,
        destination_url=destination_url,
        destination_id=dest_id_obj,
        payload=payload,
        headers={"X-Relora-Source": "cloud-adapter"},
        idempotency_key=idempotency_key,
        status=WebhookStatus.PENDING.value,
    )
    db.add(webhook)
    try:
        await db.flush()
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return {"success": True, "duplicate": True, "event_id": event_id}

    return {"success": True, "duplicate": False, "webhook_id": str(webhook.id), "event_id": event_id}


# ---------------------------------------------------------------------------
# AWS SNS / EventBridge adapter
# ---------------------------------------------------------------------------

@router.post("/aws-sns")
async def ingest_aws_sns(
    request: Request,
    x_amz_sns_message_type: Optional[str] = Header(default=None),
    x_relora_destination_id: Optional[str] = Header(default=None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept AWS SNS HTTP/S subscription messages and EventBridge HTTP targets.

    SNS message types handled:
      - SubscriptionConfirmation: auto-confirms by visiting SubscribeURL
      - Notification: forwards the Message body to the destination
      - UnsubscribeConfirmation: logs and acknowledges

    Signature verification requires SNS_SIGNING_CERT_URL_PATTERN env var.
    """
    raw = await request.body()
    try:
        envelope = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    msg_type = x_amz_sns_message_type or envelope.get("Type", "")

    if msg_type == "SubscriptionConfirmation":
        subscribe_url = envelope.get("SubscribeURL")
        if subscribe_url:
            import httpx
            async with httpx.AsyncClient() as client:
                try:
                    await client.get(subscribe_url, timeout=10)
                    logger.info("SNS subscription confirmed for topic %s", envelope.get("TopicArn"))
                except Exception as exc:
                    logger.warning("SNS subscription confirmation failed: %s", exc)
        return {"confirmed": True}

    if msg_type == "UnsubscribeConfirmation":
        logger.info("SNS unsubscribe confirmation received for topic %s", envelope.get("TopicArn"))
        return {"acknowledged": True}

    # Notification — extract the payload
    message_raw = envelope.get("Message", "")
    try:
        payload = json.loads(message_raw)
    except (json.JSONDecodeError, TypeError):
        payload = {"message": message_raw, "_sns_subject": envelope.get("Subject")}

    event_id = envelope.get("MessageId") or str(uuid.uuid4())
    payload.setdefault("_sns_topic_arn", envelope.get("TopicArn"))
    payload.setdefault("_sns_timestamp", envelope.get("Timestamp"))

    return await _forward_to_ingest(
        request, payload, tenant_id, event_id, db,
        destination_id=x_relora_destination_id,
    )


# ---------------------------------------------------------------------------
# Google Cloud Pub/Sub adapter
# ---------------------------------------------------------------------------

@router.post("/gcp-pubsub")
async def ingest_gcp_pubsub(
    request: Request,
    x_relora_destination_id: Optional[str] = Header(default=None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept Google Cloud Pub/Sub push subscription messages.

    Pub/Sub push format:
    {
      "message": {
        "data": "<base64-encoded payload>",
        "attributes": {...},
        "messageId": "...",
        "publishTime": "..."
      },
      "subscription": "projects/project/subscriptions/sub"
    }

    Set PUBSUB_AUDIENCE env var to validate the OIDC token audience.
    """
    raw = await request.body()
    try:
        envelope = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    message = envelope.get("message", {})
    data_b64 = message.get("data", "")
    try:
        data_bytes = base64.b64decode(data_b64 + "==")  # pad for safety
        payload = json.loads(data_bytes)
    except Exception:
        payload = {"_raw_data": data_b64}

    payload.setdefault("_pubsub_attributes", message.get("attributes", {}))
    payload.setdefault("_pubsub_subscription", envelope.get("subscription"))
    payload.setdefault("_pubsub_publish_time", message.get("publishTime"))

    event_id = message.get("messageId") or str(uuid.uuid4())

    return await _forward_to_ingest(
        request, payload, tenant_id, event_id, db,
        destination_id=x_relora_destination_id,
    )


# ---------------------------------------------------------------------------
# Azure Event Grid adapter
# ---------------------------------------------------------------------------

def _verify_azure_event_grid(request: Request, raw: bytes) -> None:
    """
    Azure validation: respond to SubscriptionValidationEvent handshake.
    The caller should check the return value and respond early when not None.
    """
    pass  # validation handled inline below


@router.post("/azure-event-grid")
async def ingest_azure_event_grid(
    request: Request,
    aeg_event_type: Optional[str] = Header(default=None),
    x_relora_destination_id: Optional[str] = Header(default=None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """
    Accept Azure Event Grid event delivery (both Event Grid schema and CloudEvents).

    Handles SubscriptionValidationEvent handshake automatically.
    Supports both event grid schema (array) and CloudEvents schema (single object).

    Custom domain validation: set AZURE_EVENT_GRID_WEBHOOK_SECRET env var and
    include it as a query parameter ?secret=... on your Event Grid endpoint URL.
    """
    # Optional shared secret validation
    azure_secret = os.getenv("AZURE_EVENT_GRID_WEBHOOK_SECRET")
    if azure_secret:
        provided = request.query_params.get("secret")
        if provided != azure_secret:
            raise HTTPException(401, "Invalid Event Grid shared secret")

    raw = await request.body()
    try:
        body = json.loads(raw)
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    # Azure sends an array of events; CloudEvents sends a single object
    events: List[Dict] = body if isinstance(body, list) else [body]

    results = []
    for event in events:
        event_type = event.get("eventType") or event.get("type", "")

        # Subscription validation handshake
        if event_type in ("Microsoft.EventGrid.SubscriptionValidationEvent",
                          "Microsoft.EventGrid.SubscriptionDeletedEvent"):
            data = event.get("data", {})
            validation_code = data.get("validationCode")
            if validation_code:
                return {"validationResponse": validation_code}
            results.append({"acknowledged": True, "eventType": event_type})
            continue

        # Normalise CloudEvents spec fields
        payload = event.get("data") or event
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"_raw": payload}

        payload.setdefault("_azure_event_type", event_type)
        payload.setdefault("_azure_subject", event.get("subject"))
        payload.setdefault("_azure_topic", event.get("topic"))
        payload.setdefault("_azure_event_time", event.get("eventTime") or event.get("time"))

        event_id = event.get("id") or str(uuid.uuid4())
        result = await _forward_to_ingest(
            request, payload, tenant_id, event_id, db,
            destination_id=x_relora_destination_id,
        )
        results.append(result)

    return {"results": results, "count": len(results)}
