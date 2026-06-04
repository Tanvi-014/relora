"""
Audit logging — write tamper-evident records for every mutating action.

Usage inside a router handler:
    await audit(db, request, tenant_id, "CREATE", "destination", str(dest.id), after=dest.to_dict())
    await audit(db, request, tenant_id, "UPDATE", "destination", str(dest.id), before=old, after=new)
    await audit(db, request, tenant_id, "DELETE", "destination", str(dest.id), before=old)
"""
import logging
from typing import Any, Dict, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog

logger = logging.getLogger("relora.audit")


async def audit(
    db: AsyncSession,
    request: Request,
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: Optional[str] = None,
    *,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = None,
) -> None:
    changes: Optional[Dict[str, Any]] = None
    if before is not None or after is not None:
        changes = {}
        if before is not None:
            changes["before"] = before
        if after is not None:
            changes["after"] = after

    ip = (
        request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        or request.client.host
        if request.client else None
    )
    ua = request.headers.get("User-Agent", "")[:256]

    entry = AuditLog(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        changes=changes,
        ip_address=ip,
        user_agent=ua or None,
    )
    db.add(entry)
    # Flush without committing — the router's own commit will persist the audit row atomically.
    await db.flush()
    logger.info(
        "Audit: %s %s %s tenant=%s",
        action, resource_type, resource_id or "", tenant_id,
        extra={"event": "audit.write", "action": action, "resource_type": resource_type},
    )
