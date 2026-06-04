"""
Schema drift detection — fingerprints the key structure of every inbound payload
and alerts when it changes. Catches silent breaking changes from providers (e.g.
Stripe adding a required field, GitHub renaming a key) before your app breaks.

Called from the ingest path after the webhook is persisted.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import SchemaChange, SchemaFingerprint

logger = logging.getLogger("relora.schema_drift")

_MAX_DEPTH = 4          # how deep to traverse nested objects
_MAX_SAMPLE_KEYS = 64   # cap key count to avoid huge fingerprints


def _extract_keys(obj: Any, prefix: str = "", depth: int = 0) -> List[str]:
    """Return sorted dot-separated key paths up to _MAX_DEPTH."""
    if depth >= _MAX_DEPTH or not isinstance(obj, dict):
        return []
    keys: List[str] = []
    for k, v in obj.items():
        path = f"{prefix}.{k}" if prefix else k
        keys.append(path)
        if isinstance(v, dict):
            keys.extend(_extract_keys(v, path, depth + 1))
        elif isinstance(v, list) and v and isinstance(v[0], dict):
            keys.extend(_extract_keys(v[0], f"{path}[]", depth + 1))
    return sorted(set(keys))[:_MAX_SAMPLE_KEYS]


def _fingerprint(keys: List[str]) -> str:
    return hashlib.sha256(json.dumps(keys).encode()).hexdigest()


def _redact(obj: Any, depth: int = 0) -> Any:
    """Return payload with leaf values replaced by their type name."""
    if depth > 2:
        return "..."
    if isinstance(obj, dict):
        return {k: _redact(v, depth + 1) for k, v in list(obj.items())[:20]}
    if isinstance(obj, list):
        return [_redact(obj[0], depth + 1)] if obj else []
    return type(obj).__name__


async def check_and_update(
    db: AsyncSession,
    tenant_id: str,
    source_key: str,
    payload: Dict[str, Any],
) -> Optional[SchemaChange]:
    """
    Compare payload key structure against stored fingerprint.
    Returns a SchemaChange if the structure changed, otherwise None.
    Upserts the fingerprint on every call.
    """
    keys = _extract_keys(payload)
    if not keys:
        return None

    fp = _fingerprint(keys)

    result = await db.execute(
        select(SchemaFingerprint).where(
            SchemaFingerprint.tenant_id == tenant_id,
            SchemaFingerprint.source_key == source_key,
        )
    )
    existing: Optional[SchemaFingerprint] = result.scalar_one_or_none()

    if existing is None:
        # First time seeing this source — store baseline fingerprint
        db.add(SchemaFingerprint(
            tenant_id=tenant_id,
            source_key=source_key,
            fingerprint=fp,
            key_structure=keys,
            sample_payload=_redact(payload),
            first_seen_at=datetime.now(timezone.utc),
            last_seen_at=datetime.now(timezone.utc),
            event_count=1,
        ))
        return None

    # Bump last_seen and count regardless
    existing.last_seen_at = datetime.now(timezone.utc)
    existing.event_count = (existing.event_count or 0) + 1

    if existing.fingerprint == fp:
        return None  # no change

    # Fingerprint changed — compute diff
    old_keys: List[str] = existing.key_structure or []
    added = sorted(set(keys) - set(old_keys))
    removed = sorted(set(old_keys) - set(keys))

    change = SchemaChange(
        tenant_id=tenant_id,
        source_key=source_key,
        old_fingerprint=existing.fingerprint,
        new_fingerprint=fp,
        added_keys=added,
        removed_keys=removed,
        detected_at=datetime.now(timezone.utc),
    )
    db.add(change)

    # Update stored fingerprint to the new baseline
    existing.fingerprint = fp
    existing.key_structure = keys
    existing.sample_payload = _redact(payload)

    logger.warning(
        "Schema drift detected: source=%s tenant=%s added=%s removed=%s",
        source_key, tenant_id, added, removed,
        extra={"event": "schema_drift.detected", "source_key": source_key},
    )
    return change
