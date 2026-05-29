import json
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, status


FILTER_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*(?:==|=)\s*['\"]?([^'\"]+)['\"]?\s*$")


def get_path(payload: Any, path: str) -> Any:
    current = payload
    for part in path.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def extract_event_id(payload: Any, explicit_event_id: Optional[str] = None) -> str:
    if explicit_event_id:
        return explicit_event_id

    if isinstance(payload, dict):
        for path in ("event_id", "event.id", "id"):
            value = get_path(payload, path)
            if value:
                return str(value)

    return str(uuid.uuid4())


def event_matches_filter(payload: Any, filter_expression: Optional[str]) -> bool:
    if not filter_expression:
        return True

    match = FILTER_RE.match(filter_expression)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filter must look like event.type == 'payment.succeeded'",
        )

    path, expected = match.groups()
    return str(get_path(payload, path)) == expected


def apply_transform(payload: Any, transform_spec: Optional[str]) -> Any:
    if not transform_spec:
        return payload

    try:
        mapping = json.loads(transform_spec)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transform must be a JSON object mapping output fields to source paths",
        ) from exc

    if not isinstance(mapping, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Transform must be a JSON object",
        )

    transformed: Dict[str, Any] = {}
    for output_field, source_path in mapping.items():
        if not isinstance(output_field, str) or not isinstance(source_path, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transform keys and values must be strings",
            )
        transformed[output_field] = get_path(payload, source_path)

    return transformed
