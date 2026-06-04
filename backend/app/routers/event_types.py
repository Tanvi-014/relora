import uuid as _uuid_mod
from typing import Any, Dict
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_tenant_from_auth
from app.db import get_db
from app.models import EventType, Project
from app.schema_validator import SchemaValidator

router = APIRouter()


async def _get_project_by_api_key(db: AsyncSession, api_key: str) -> Project:
    result = await db.execute(select(Project).where(Project.api_key == api_key))
    project = result.scalar_one_or_none()
    if not project:
        return Project(id=_uuid_mod.uuid4(), name="default", api_key=api_key)
    return project


@router.get("/api/v1/event-types")
async def list_event_types(tenant_id: str = Depends(get_tenant_from_auth), db: AsyncSession = Depends(get_db)):
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.project_id == project.id).order_by(EventType.name)
    )
    return [et.to_dict() for et in result.scalars().all()]


@router.post("/api/v1/event-types", status_code=201)
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


@router.get("/api/v1/event-types/{event_type_id}")
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


@router.delete("/api/v1/event-types/{event_type_id}", status_code=204)
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


@router.post("/api/v1/event-types/{event_type_id}/validate-schema")
async def validate_event_type_schema(
    event_type_id: UUID,
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Validate a schema definition before saving it."""
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.id == event_type_id, EventType.project_id == project.id)
    )
    et = result.scalar_one_or_none()
    if not et:
        raise HTTPException(404, "Event type not found")

    schema = body.get("schema")
    if not schema:
        raise HTTPException(400, "Schema is required")

    is_valid, error_message = SchemaValidator.validate_schema_definition(schema)
    if not is_valid:
        raise HTTPException(400, error_message)

    return {"valid": True, "message": "Schema is valid"}


@router.post("/api/v1/event-types/{event_type_id}/validate-payload")
async def validate_event_payload(
    event_type_id: UUID,
    body: Dict[str, Any] = Body(...),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
):
    """Validate a payload against an event type's schema."""
    project = await _get_project_by_api_key(db, tenant_id)
    result = await db.execute(
        select(EventType).where(EventType.id == event_type_id, EventType.project_id == project.id)
    )
    et = result.scalar_one_or_none()
    if not et:
        raise HTTPException(404, "Event type not found")

    payload = body.get("payload")
    if not payload:
        raise HTTPException(400, "Payload is required")

    if not et.schema:
        return {"valid": True, "message": "No schema defined for this event type"}

    errors = SchemaValidator.get_schema_errors(payload, et.schema)
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "message": f"Validation failed with {len(errors)} error(s)"
        }

    return {"valid": True, "message": "Payload is valid"}
