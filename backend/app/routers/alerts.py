from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.alerts import _send_email_alert, _send_slack_alert
from app.audit import audit
from app.auth import get_tenant_from_auth
from app.db import get_db
from app.models import AlertConfig
from app.schemas import AlertConfigCreate, AlertConfigResponse, AlertConfigUpdate

router = APIRouter()


@router.get("/api/v1/alerts")
async def list_alerts(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.tenant_id == tenant_id).order_by(desc(AlertConfig.created_at))
    )
    return [c.to_dict() for c in result.scalars().all()]


@router.post("/api/v1/alerts", status_code=201)
async def create_alert(
    request: Request,
    config_in: AlertConfigCreate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    config = AlertConfig(
        tenant_id=tenant_id,
        name=config_in.name,
        channel_type=config_in.channel_type,
        config=config_in.config,
        enabled=config_in.enabled if config_in.enabled is not None else True,
        dlq_threshold=config_in.dlq_threshold,
        error_rate_threshold=config_in.error_rate_threshold,
    )
    db.add(config)
    await db.flush()
    await audit(db, request, tenant_id, "CREATE", "alert_config", str(config.id), after=config.to_dict())
    await db.commit()
    await db.refresh(config)
    return config.to_dict()


@router.get("/api/v1/alerts/{alert_id}")
async def get_alert(alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    return config.to_dict()


@router.put("/api/v1/alerts/{alert_id}")
async def update_alert(
    request: Request,
    alert_id: UUID,
    config_in: AlertConfigUpdate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    before = config.to_dict()
    if config_in.name is not None:
        config.name = config_in.name
    if config_in.config is not None:
        new_cfg = {**config_in.config}
        existing = config.config or {}
        for sk in ("password", "smtp_password"):
            if new_cfg.get(sk) == "••••••••" and sk in existing:
                new_cfg[sk] = existing[sk]
        if "webhook_url" in new_cfg and new_cfg["webhook_url"].startswith("…") and "webhook_url" in existing:
            new_cfg["webhook_url"] = existing["webhook_url"]
        config.config = new_cfg
    if config_in.enabled is not None:
        config.enabled = config_in.enabled
    if config_in.dlq_threshold is not None:
        config.dlq_threshold = config_in.dlq_threshold
    if config_in.error_rate_threshold is not None:
        config.error_rate_threshold = config_in.error_rate_threshold
    config.updated_at = func.now()
    await audit(db, request, tenant_id, "UPDATE", "alert_config", str(alert_id), before=before, after=config.to_dict())
    await db.commit()
    await db.refresh(config)
    return config.to_dict()


@router.delete("/api/v1/alerts/{alert_id}", status_code=204)
async def delete_alert(request: Request, alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    await audit(db, request, tenant_id, "DELETE", "alert_config", str(alert_id), before=config.to_dict())
    await db.delete(config)
    await db.commit()
    return Response(status_code=204)


@router.post("/api/v1/alerts/{alert_id}/test")
async def test_alert(alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    test_data = {
        "webhook_id": "00000000-0000-0000-0000-000000000000",
        "event_id": "evt_test_relora",
        "destination_url": "https://example.com/webhook",
        "retry_count": 5,
        "last_error": "HTTP 500: Internal Server Error",
        "tenant_id": tenant_id,
    }
    try:
        if config.channel_type == "slack":
            await _send_slack_alert(config, test_data)
        elif config.channel_type == "email":
            await _send_email_alert(config, test_data)
        else:
            raise HTTPException(400, f"Unsupported channel: {config.channel_type}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(500, f"Test failed: {exc}")
    return {"success": True, "message": f"Test alert sent to {config.name}"}
