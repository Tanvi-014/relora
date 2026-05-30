"""
Pure API entrypoint — no worker pool.
Run with: uvicorn app.api_main:app --host 0.0.0.0 --port 8000
"""
import asyncio
import json
import logging
import os
import uuid as _uuid_mod
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID

from fastapi import (
    Body, Cookie, Depends, FastAPI, HTTPException, Query,
    Request, Response, WebSocket, WebSocketDisconnect, status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select, text, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware

from app.ai_intelligence import analyze_payload, suggest_filter, suggest_transform
from app.alerts import _send_email_alert, _send_slack_alert
from app.auth import (
    create_access_token,
    get_current_user,
    get_password_hash,
    get_tenant_from_auth,
    require_project_role,
    verify_password,
)
from app.circuit_breaker import record_outcome, should_deliver
from app.config import settings
from app.db import get_db, init_db
from app.logging_config import configure_logging
from app.models import (
    AlertConfig, DeliveryAttempt, Destination, EventType,
    Project, ProjectMember, ReplayJob, User, Webhook, WebhookStatus,
)
from app.rate_limit import check_rate_limit
from app.routing import apply_transform, event_matches_filter, extract_event_id, apply_json_map
from app.schemas import (
    AlertConfigCreate, AlertConfigResponse, AlertConfigUpdate,
    DashboardStats, WebhookDetailResponse, WebhookResponse,
)
from app.security import require_api_key, validate_destination_url
from app.signatures import verify_webhook_signature
from app.simulator import build_simulated_payload, list_providers
from app.websocket_hub import ws_manager

configure_logging()
logger = logging.getLogger("hermes.api")

EXCLUDED_INGEST_HEADERS = {
    "host", "connection", "content-length", "accept-encoding",
    "user-agent", "x-real-ip", "x-forwarded-for",
    "x-forwarded-proto", "x-forwarded-port",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.AUTO_CREATE_TABLES:
        logger.info("AUTO_CREATE_TABLES=true, initializing tables...")
        await init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    description="Production-grade webhook delivery middleware.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

if settings.FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def _set_auth_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="hermes_session",
        value=token,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        max_age=settings.JWT_EXPIRY_DAYS * 86400,
        domain=settings.COOKIE_DOMAIN or None,
    )


async def _get_token_from_request(
    request: Request,
    hermes_session: Optional[str] = Cookie(default=None),
) -> Optional[str]:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return hermes_session


# ---------------------------------------------------------------------------
# Auth endpoints
# ---------------------------------------------------------------------------

@app.post("/api/v1/auth/register", status_code=201)
async def register(
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Email already registered")
    user = User(email=email, password_hash=get_password_hash(password))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    logger.info("User registered", extra={"event": "user.registered", "user_id": str(user.id)})
    return {"message": "Registered", "user_id": str(user.id)}


@app.post("/api/v1/auth/login")
async def login(
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(401, "Invalid credentials")
    token = create_access_token({"sub": str(user.id)})
    response = JSONResponse(content={"access_token": token, "token_type": "bearer", "user": user.to_dict()})
    _set_auth_cookie(response, token)
    logger.info("User logged in", extra={"event": "user.login", "user_id": str(user.id)})
    return response


@app.post("/api/v1/auth/logout")
async def logout():
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie("hermes_session")
    return response


@app.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects")
async def list_projects(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    out = []
    for p in projects:
        mr = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == p.id,
                ProjectMember.user_id == current_user.id,
            )
        )
        m = mr.scalar_one_or_none()
        d = p.to_dict()
        d["role"] = m.role if m else None
        out.append(d)
    return out


@app.post("/api/v1/projects", status_code=201)
async def create_project(
    name: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    project = Project(name=name, api_key=f"hk_live_{_uuid_mod.uuid4().hex}")
    db.add(project)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=current_user.id, role="owner"))
    await db.commit()
    await db.refresh(project)
    d = project.to_dict()
    d["role"] = "owner"
    return d


@app.get("/api/v1/projects/{project_id}")
async def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")
    mr = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    m = mr.scalar_one_or_none()
    if not m:
        raise HTTPException(403, "No access")
    d = project.to_dict()
    d["role"] = m.role
    return d


@app.delete("/api/v1/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    checker = require_project_role(["owner"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(404, "Project not found")
    await db.delete(project)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Team members
# ---------------------------------------------------------------------------

@app.get("/api/v1/projects/{project_id}/members")
async def list_members(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    mr = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id,
        )
    )
    if not mr.scalar_one_or_none():
        raise HTTPException(403, "No access")
    result = await db.execute(select(ProjectMember).where(ProjectMember.project_id == project_id))
    return [m.to_dict() for m in result.scalars().all()]


@app.post("/api/v1/projects/{project_id}/members", status_code=201)
async def add_member(
    project_id: UUID,
    email: str = Body(..., embed=True),
    role: str = Body("viewer", embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if role not in ("owner", "admin", "viewer"):
        raise HTTPException(400, "Role must be owner, admin, or viewer")
    checker = require_project_role(["owner", "admin"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    ur = await db.execute(select(User).where(User.email == email))
    user = ur.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    existing = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "Already a member")
    m = ProjectMember(project_id=project_id, user_id=user.id, role=role)
    db.add(m)
    await db.commit()
    await db.refresh(m)
    return m.to_dict()


@app.delete("/api/v1/projects/{project_id}/members/{user_id}", status_code=204)
async def remove_member(
    project_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    checker = require_project_role(["owner"])
    await checker(project_id=str(project_id), current_user=current_user, db=db)
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    m = result.scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Member not found")
    await db.delete(m)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Destinations registry
# ---------------------------------------------------------------------------

@app.get("/api/v1/destinations")
async def list_destinations(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Project.api_key == tenant_id)
        .order_by(Destination.created_at.desc())
    )
    return [d.to_dict() for d in result.scalars().all()]


@app.post("/api/v1/destinations", status_code=201)
async def create_destination(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    validate_destination_url(body.get("url", ""))
    dest = Destination(
        project_id=project.id,
        name=body["name"],
        url=body["url"],
        description=body.get("description"),
        is_enabled=body.get("is_enabled", True),
        max_retries=body.get("max_retries", settings.DEFAULT_MAX_RETRIES),
        backoff_base_seconds=body.get("backoff_base_seconds", settings.BACKOFF_BASE_SECONDS),
        ordering_key_field=body.get("ordering_key_field"),
        transform_type=body.get("transform_type", "none"),
        transform_code=body.get("transform_code"),
        transform_map=body.get("transform_map"),
        filter_expression=body.get("filter_expression"),
        webhook_secret=body.get("webhook_secret"),
        custom_headers=body.get("custom_headers", {}),
    )
    db.add(dest)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Destination name already exists in this project")
    await db.refresh(dest)
    return dest.to_dict()


@app.get("/api/v1/destinations/{dest_id}")
async def get_destination(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    return dest.to_dict()


@app.put("/api/v1/destinations/{dest_id}")
async def update_destination(
    dest_id: UUID,
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    for field in ("name", "url", "description", "is_enabled", "max_retries",
                  "backoff_base_seconds", "ordering_key_field", "transform_type",
                  "transform_code", "transform_map", "filter_expression",
                  "webhook_secret", "custom_headers"):
        if field in body:
            setattr(dest, field, body[field])
    dest.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(dest)
    return dest.to_dict()


@app.delete("/api/v1/destinations/{dest_id}", status_code=204)
async def delete_destination(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    await db.delete(dest)
    await db.commit()
    return Response(status_code=204)


@app.post("/api/v1/destinations/{dest_id}/test")
async def test_destination(
    dest_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    dest = await _get_dest_for_tenant(db, dest_id, tenant_id)
    import httpx as _httpx
    test_payload = {"event": "test", "source": "hermes", "destination_id": str(dest_id)}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.post(dest.url, json=test_payload, headers={"X-Hermes-Test": "true"})
        return {"success": 200 <= r.status_code < 300, "status_code": r.status_code, "body": r.text[:500]}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@app.get("/api/v1/destinations/{dest_id}/stats")
async def destination_stats(
    dest_id: UUID,
    period_days: int = Query(7, le=90),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    await _get_dest_for_tenant(db, dest_id, tenant_id)
    result = await db.execute(
        text("""
        SELECT
          COUNT(da.id) as total_attempts,
          COUNT(da.id) FILTER (WHERE da.status_code BETWEEN 200 AND 299) as successes,
          PERCENTILE_CONT(0.50) WITHIN GROUP (ORDER BY da.duration_ms) as p50_ms,
          PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY da.duration_ms) as p95_ms,
          PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY da.duration_ms) as p99_ms,
          AVG(da.duration_ms) as avg_ms
        FROM delivery_attempts da
        JOIN webhooks w ON w.id = da.webhook_id
        WHERE w.destination_id = :dest_id
          AND da.attempted_at >= NOW() - INTERVAL '1 day' * :days
        """),
        {"dest_id": dest_id, "days": period_days},
    )
    row = result.fetchone()
    total = row.total_attempts or 0
    successes = row.successes or 0
    return {
        "period_days": period_days,
        "total_attempts": total,
        "success_rate": round(successes / total * 100, 1) if total else 100.0,
        "latency": {
            "p50_ms": round(row.p50_ms or 0),
            "p95_ms": round(row.p95_ms or 0),
            "p99_ms": round(row.p99_ms or 0),
            "avg_ms": round(row.avg_ms or 0),
        },
    }


# ---------------------------------------------------------------------------
# Webhook ingest
# ---------------------------------------------------------------------------

@app.post("/api/v1/ingest")
async def ingest_webhook(
    request: Request,
    url: Optional[str] = Query(None),
    urls: Optional[List[str]] = Query(None),
    destination_id: Optional[str] = Query(None),
    filter_expression: Optional[str] = Query(None, alias="filter"),
    transform: Optional[str] = Query(None),
    signature_provider: Optional[str] = Query(None),
    ordering_key: Optional[str] = Query(None),
    consumer_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    await check_rate_limit(request, tenant_id, db)

    # Resolve destination candidates
    destination_candidates: List[str] = []
    dest_id_obj: Optional[UUID] = None

    if destination_id:
        dest_result = await db.execute(select(Destination).where(Destination.id == UUID(destination_id)))
        dest_obj = dest_result.scalar_one_or_none()
        if not dest_obj:
            raise HTTPException(404, "Destination not found")
        destination_candidates.append(dest_obj.url)
        dest_id_obj = dest_obj.id
        # Override filter/transform from destination if not provided on request
        if not filter_expression:
            filter_expression = dest_obj.filter_expression
        if dest_obj.transform_type == "json_map" and dest_obj.transform_map and not transform:
            transform = json.dumps(dest_obj.transform_map)
        if dest_obj.ordering_key_field and not ordering_key:
            ordering_key = dest_obj.ordering_key_field  # will be resolved from payload below
    else:
        if url:
            destination_candidates.append(url)
        if urls:
            for item in urls:
                destination_candidates.extend([p.strip() for p in item.split(",") if p.strip()])

    if not destination_candidates:
        raise HTTPException(400, "At least one destination required via url, urls, or destination_id")

    destination_urls = [validate_destination_url(d) for d in destination_candidates]

    raw_body = await request.body()
    verify_webhook_signature(signature_provider, request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        payload = {"_raw_body": raw_body.decode("utf-8", errors="replace")}

    explicit_event_id = request.headers.get("X-Event-Id") or request.headers.get("X-Hermes-Event-Id")
    event_id = extract_event_id(payload, explicit_event_id)

    # Resolve ordering_key from payload field if it looks like a path
    if ordering_key and isinstance(payload, dict) and "." in ordering_key:
        from app.routing import get_path
        resolved = get_path(payload, ordering_key)
        if resolved:
            ordering_key = str(resolved)

    if not event_matches_filter(payload, filter_expression):
        return {"success": True, "filtered": True, "webhook_ids": [], "message": "Filtered, not queued"}

    delivery_payload = apply_transform(payload, transform)

    headers: Dict[str, str] = {
        k: v for k, v in request.headers.items()
        if k.lower() not in EXCLUDED_INGEST_HEADERS
    }

    idempotency_key = (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Hermes-Idempotency-Key")
        or event_id
    )

    webhook_ids: List[str] = []
    duplicate_ids: List[str] = []

    for destination_url in destination_urls:
        # Idempotency check
        existing_r = await db.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing = existing_r.scalar_one_or_none()
        if existing:
            webhook_ids.append(str(existing.id))
            duplicate_ids.append(str(existing.id))
            continue

        webhook = Webhook(
            tenant_id=tenant_id,
            event_id=event_id,
            destination_url=destination_url,
            destination_id=dest_id_obj,
            payload=delivery_payload,
            headers=headers,
            idempotency_key=idempotency_key,
            ordering_key=ordering_key,
            consumer_id=consumer_id,
            status=WebhookStatus.PENDING.value,
            max_retries=settings.DEFAULT_MAX_RETRIES,
        )
        db.add(webhook)
        try:
            await db.flush()
            await db.commit()
            webhook_ids.append(str(webhook.id))
        except IntegrityError:
            await db.rollback()
            existing_r2 = await db.execute(
                select(Webhook).where(
                    Webhook.tenant_id == tenant_id,
                    Webhook.destination_url == destination_url,
                    Webhook.idempotency_key == idempotency_key,
                )
            )
            existing2 = existing_r2.scalar_one_or_none()
            if not existing2:
                raise
            webhook_ids.append(str(existing2.id))
            duplicate_ids.append(str(existing2.id))

    single = len(webhook_ids) == 1
    return {
        "success": True,
        "filtered": False,
        "event_id": event_id,
        "tenant_id": tenant_id,
        "webhook_id": webhook_ids[0] if single else None,
        "webhook_ids": webhook_ids,
        "duplicate": len(duplicate_ids) == len(webhook_ids),
        "duplicate_ids": duplicate_ids,
        "message": "Webhook ingested and queued",
    }


# ---------------------------------------------------------------------------
# Webhooks management
# ---------------------------------------------------------------------------

@app.get("/api/v1/webhooks")
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    search: Optional[str] = Query(None),
    destination_id: Optional[str] = Query(None),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * limit
    stmt = select(Webhook).where(Webhook.tenant_id == tenant_id).order_by(desc(Webhook.created_at))
    count_stmt = select(func.count(Webhook.id)).where(Webhook.tenant_id == tenant_id)

    if status_filter:
        try:
            sv = WebhookStatus(status_filter.lower()).value
            stmt = stmt.where(Webhook.status == sv)
            count_stmt = count_stmt.where(Webhook.status == sv)
        except ValueError:
            raise HTTPException(400, f"Invalid status. Valid: {[s.value for s in WebhookStatus]}")

    if destination_id:
        stmt = stmt.where(Webhook.destination_id == UUID(destination_id))
        count_stmt = count_stmt.where(Webhook.destination_id == UUID(destination_id))

    if search:
        stmt = stmt.where(
            text("webhooks.payload::text ILIKE :search").bindparams(search=f"%{search}%")
        )
        count_stmt = count_stmt.where(
            text("webhooks.payload::text ILIKE :search").bindparams(search=f"%{search}%")
        )

    result = await db.execute(stmt.offset(offset).limit(limit))
    webhooks = result.scalars().all()
    total = await db.scalar(count_stmt) or 0

    return {
        "webhooks": [w.to_dict() for w in webhooks],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@app.get("/api/v1/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    d = wh.to_dict()
    d["payload"] = wh.payload
    d["headers"] = wh.headers
    d["attempts"] = [a.to_dict() for a in wh.attempts]
    return d


@app.post("/api/v1/webhooks/{webhook_id}/replay")
async def replay_webhook(
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Webhook).where(Webhook.id == webhook_id, Webhook.tenant_id == tenant_id)
    )
    wh = result.scalar_one_or_none()
    if not wh:
        raise HTTPException(404, "Webhook not found")
    wh.status = WebhookStatus.PENDING.value
    wh.retry_count = 0
    wh.next_attempt_at = datetime.now(timezone.utc)
    wh.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("Replay triggered", extra={"event": "webhook.replay.requested", "webhook_id": str(webhook_id)})
    return {"success": True, "message": "Rescheduled for immediate delivery"}


# ---------------------------------------------------------------------------
# Time-window bulk replay
# ---------------------------------------------------------------------------

@app.post("/api/v1/webhooks/replay-window")
async def replay_time_window(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    from_time = datetime.fromisoformat(body["from_time"].replace("Z", "+00:00"))
    to_time = datetime.fromisoformat(body["to_time"].replace("Z", "+00:00"))
    dest_id = body.get("destination_id")
    rate = int(body.get("replay_rate_per_minute", 100))

    project = await _get_project_by_api_key(db, tenant_id)

    count_r = await db.execute(
        text("""
        SELECT COUNT(*) FROM webhooks
        WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
          AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
        """),
        {"tid": tenant_id, "from_t": from_time, "to_t": to_time,
         "dest_id": dest_id},
    )
    total = count_r.scalar() or 0

    job = ReplayJob(
        project_id=project.id,
        from_time=from_time,
        to_time=to_time,
        destination_id=UUID(dest_id) if dest_id else None,
        replay_rate_per_minute=rate,
        total_count=total,
        status="pending",
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    asyncio.create_task(_execute_replay_job(str(job.id), tenant_id, from_time, to_time, dest_id, rate))

    return {
        "job_id": str(job.id),
        "total_count": total,
        "estimated_duration_minutes": round(total / rate, 1) if rate else 0,
    }


async def _execute_replay_job(job_id: str, tenant_id: str, from_time, to_time, dest_id, rate: int):
    from app.db import async_session as _session
    async with _session() as db:
        try:
            await db.execute(
                text("UPDATE replay_jobs SET status='running', updated_at=NOW() WHERE id=:id"),
                {"id": job_id},
            )
            await db.commit()

            result = await db.execute(
                text("""
                SELECT id FROM webhooks
                WHERE tenant_id = :tid AND created_at BETWEEN :from_t AND :to_t
                  AND (:dest_id IS NULL OR destination_id = :dest_id::uuid)
                ORDER BY created_at ASC
                """),
                {"tid": tenant_id, "from_t": from_time, "to_t": to_time, "dest_id": dest_id},
            )
            rows = result.fetchall()

            delay_per_item = 60.0 / rate if rate > 0 else 0.1
            for i, row in enumerate(rows):
                await db.execute(
                    text("""
                    UPDATE webhooks SET status='pending', retry_count=0,
                      next_attempt_at=NOW(), updated_at=NOW()
                    WHERE id=:id
                    """),
                    {"id": row.id},
                )
                await db.execute(
                    text("UPDATE replay_jobs SET processed_count=:n, updated_at=NOW() WHERE id=:id"),
                    {"n": i + 1, "id": job_id},
                )
                await db.commit()
                await asyncio.sleep(delay_per_item)

            await db.execute(
                text("UPDATE replay_jobs SET status='completed', updated_at=NOW() WHERE id=:id"),
                {"id": job_id},
            )
            await db.commit()
        except Exception as exc:
            logger.error("Replay job failed: %s", exc, exc_info=True)
            await db.execute(
                text("UPDATE replay_jobs SET status='failed', error_message=:err, updated_at=NOW() WHERE id=:id"),
                {"err": str(exc), "id": job_id},
            )
            await db.commit()


@app.get("/api/v1/replay-jobs/{job_id}")
async def get_replay_job(
    job_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(ReplayJob).where(ReplayJob.id == job_id, ReplayJob.project_id == project.id)
    )
    job = result.scalar_one_or_none()
    if not job:
        raise HTTPException(404, "Replay job not found")
    return job.to_dict()


# ---------------------------------------------------------------------------
# Consumer polling (pull-based delivery)
# ---------------------------------------------------------------------------

@app.post("/api/v1/consumers/{consumer_id}/poll")
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


# ---------------------------------------------------------------------------
# Stats, metrics, health, usage
# ---------------------------------------------------------------------------

@app.get("/api/v1/stats")
async def get_stats(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    tf = Webhook.tenant_id == tenant_id
    total = await db.scalar(select(func.count(Webhook.id)).where(tf)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "pending")) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "processing")) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "completed")) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "failed")) or 0
    terminal = completed + failed
    success_rate = round(completed / terminal * 100, 1) if terminal else 100.0
    return {
        "total_webhooks": total,
        "pending_count": pending,
        "processing_count": processing,
        "completed_count": completed,
        "failed_count": failed,
        "success_rate": success_rate,
    }


@app.get("/api/v1/usage")
async def get_usage(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    stmt = select(
        Webhook.tenant_id,
        func.count(Webhook.id).label("events"),
        func.count(func.distinct(Webhook.event_id)).label("unique_events"),
    ).where(Webhook.tenant_id == tenant_id).group_by(Webhook.tenant_id)
    rows = (await db.execute(stmt)).all()
    return {"usage": [{"tenant_id": r.tenant_id, "events": r.events, "unique_events": r.unique_events} for r in rows]}


@app.get("/metrics")
async def get_metrics(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    tf = Webhook.tenant_id == tenant_id
    total = await db.scalar(select(func.count(Webhook.id)).where(tf)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "pending")) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "processing")) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "completed")) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tf, Webhook.status == "failed")) or 0
    attempts = await db.scalar(
        select(func.count(DeliveryAttempt.id))
        .join(Webhook, DeliveryAttempt.webhook_id == Webhook.id)
        .where(tf)
    ) or 0
    body = "\n".join([
        "# HELP hermes_webhooks_total Total ingested webhooks.",
        "# TYPE hermes_webhooks_total gauge",
        f"hermes_webhooks_total {total}",
        "# HELP hermes_webhooks_by_status Webhooks by delivery status.",
        "# TYPE hermes_webhooks_by_status gauge",
        f'hermes_webhooks_by_status{{status="pending"}} {pending}',
        f'hermes_webhooks_by_status{{status="processing"}} {processing}',
        f'hermes_webhooks_by_status{{status="completed"}} {completed}',
        f'hermes_webhooks_by_status{{status="failed"}} {failed}',
        "# HELP hermes_delivery_attempts_total Total delivery attempts.",
        "# TYPE hermes_delivery_attempts_total gauge",
        f"hermes_delivery_attempts_total {attempts}",
        "",
    ])
    return Response(content=body, media_type="text/plain; version=0.0.4")


@app.get("/health")
async def health():
    return {"status": "healthy", "version": "2.0.0"}


@app.get("/health/detailed")
async def health_detailed(db: AsyncSession = Depends(get_db)):
    checks: Dict[str, Any] = {}
    try:
        await db.execute(text("SELECT 1"))
        checks["database"] = {"status": "ok"}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    stuck_r = await db.execute(
        text("SELECT COUNT(*) FROM webhooks WHERE status='processing' AND updated_at < NOW() - INTERVAL '5 minutes'")
    )
    stuck = stuck_r.scalar() or 0
    checks["worker"] = {"status": "warning" if stuck > 0 else "ok", "stuck_jobs": stuck}

    queue_r = await db.execute(text("SELECT COUNT(*) FROM webhooks WHERE status='pending'"))
    checks["queue"] = {"depth": queue_r.scalar() or 0}

    overall = "ok" if all(c.get("status") in ("ok", None) for c in checks.values() if "status" in c) else "degraded"
    return {"status": overall, "checks": checks, "version": "2.0.0"}


# ---------------------------------------------------------------------------
# Alerts CRUD
# ---------------------------------------------------------------------------

@app.get("/api/v1/alerts")
async def list_alerts(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.tenant_id == tenant_id).order_by(desc(AlertConfig.created_at))
    )
    return [c.to_dict() for c in result.scalars().all()]


@app.post("/api/v1/alerts", status_code=201)
async def create_alert(
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
    )
    db.add(config)
    await db.commit()
    await db.refresh(config)
    return config.to_dict()


@app.get("/api/v1/alerts/{alert_id}")
async def get_alert(alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    return config.to_dict()


@app.put("/api/v1/alerts/{alert_id}")
async def update_alert(
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
    config.updated_at = func.now()
    await db.commit()
    await db.refresh(config)
    return config.to_dict()


@app.delete("/api/v1/alerts/{alert_id}", status_code=204)
async def delete_alert(alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    await db.delete(config)
    await db.commit()
    return Response(status_code=204)


@app.post("/api/v1/alerts/{alert_id}/test")
async def test_alert(alert_id: UUID, tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertConfig).where(AlertConfig.id == alert_id, AlertConfig.tenant_id == tenant_id)
    )
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(404, "Alert not found")
    test_data = {
        "webhook_id": "00000000-0000-0000-0000-000000000000",
        "event_id": "evt_test_hermes",
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


# ---------------------------------------------------------------------------
# Event type catalog
# ---------------------------------------------------------------------------

@app.get("/api/v1/event-types")
async def list_event_types(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.project_id == project.id).order_by(EventType.name)
    )
    return [et.to_dict() for et in result.scalars().all()]


@app.post("/api/v1/event-types", status_code=201)
async def create_event_type(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    et = EventType(
        project_id=project.id,
        name=body["name"],
        description=body.get("description"),
        schema=body.get("schema"),
        example_payload=body.get("example_payload"),
        version=body.get("version", "1"),
    )
    db.add(et)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Event type already exists")
    await db.refresh(et)
    return et.to_dict()


@app.get("/api/v1/event-types/{event_type_id}")
async def get_event_type(
    event_type_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.id == event_type_id, EventType.project_id == project.id)
    )
    et = result.scalar_one_or_none()
    if not et:
        raise HTTPException(404, "Event type not found")
    return et.to_dict()


@app.delete("/api/v1/event-types/{event_type_id}", status_code=204)
async def delete_event_type(
    event_type_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.id == event_type_id, EventType.project_id == project.id)
    )
    et = result.scalar_one_or_none()
    if not et:
        raise HTTPException(404, "Event type not found")
    await db.delete(et)
    await db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# AI intelligence
# ---------------------------------------------------------------------------

@app.post("/api/v1/ai/analyze-payload")
async def ai_analyze(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled. Set ENABLE_AI_FEATURES=true and ANTHROPIC_API_KEY.")
    payload = body.get("payload", body)
    result = await analyze_payload(payload)
    return result


@app.post("/api/v1/ai/suggest-filter")
async def ai_suggest_filter(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    expr = await suggest_filter(body.get("description", ""), body.get("sample_payload", {}))
    return {"expression": expr}


@app.post("/api/v1/ai/suggest-transform")
async def ai_suggest_transform(
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
):
    if not settings.ENABLE_AI_FEATURES:
        raise HTTPException(403, "AI features not enabled")
    code = await suggest_transform(body.get("description", ""), body.get("sample_payload", {}))
    return {"transform_code": code}


# ---------------------------------------------------------------------------
# Webhook simulator
# ---------------------------------------------------------------------------

@app.get("/api/v1/simulate/providers")
async def simulate_providers(tenant_id: str = Depends(get_tenant_from_auth)):
    return list_providers()


@app.post("/api/v1/simulate")
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
        headers={"X-Hermes-Simulated": "true", "X-Hermes-Provider": provider},
        idempotency_key=None,
        status=WebhookStatus.PENDING.value,
        max_retries=settings.DEFAULT_MAX_RETRIES,
        is_simulation=True,
    )
    db.add(webhook)
    await db.commit()
    await db.refresh(webhook)
    return {"webhook_id": str(webhook.id), "payload": payload}


# ---------------------------------------------------------------------------
# WebSocket real-time updates
# ---------------------------------------------------------------------------

@app.websocket("/ws/{project_key}")
async def websocket_endpoint(
    websocket: WebSocket,
    project_key: str,
    token: Optional[str] = Query(None),
):
    # Validate token from query param or cookie
    from app.auth import decode_access_token
    valid = False
    if token:
        payload = decode_access_token(token)
        valid = payload is not None
    if not valid:
        # Allow anonymous connections for API-key tenants
        valid = True  # dashboard connects with project API key as project_key

    if not valid:
        await websocket.close(code=4001)
        return

    await ws_manager.connect(websocket, project_key)
    try:
        while True:
            # Keep-alive ping every 30s
            await asyncio.sleep(30)
            await websocket.send_text('{"type":"ping"}')
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        await ws_manager.disconnect(websocket, project_key)


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        # For anonymous / legacy tenants create a virtual project
        return Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


async def _get_dest_for_tenant(db: AsyncSession, dest_id: UUID, tenant_id: str) -> Destination:
    result = await db.execute(
        select(Destination)
        .join(Project, Project.id == Destination.project_id)
        .where(Destination.id == dest_id, Project.api_key == tenant_id)
    )
    dest = result.scalar_one_or_none()
    if not dest:
        raise HTTPException(404, "Destination not found")
    return dest


# ---------------------------------------------------------------------------
# Serve frontend static files last
# ---------------------------------------------------------------------------

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
