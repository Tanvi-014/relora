import json
import re
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, status


FILTER_RE = re.compile(
    r"^\s*([A-Za-z0-9_.@-]+)\s*(?:==|!=|>=|<=|>|<)\s*['\"]?([^'\"]+)['\"]?\s*$"
)

OPERATORS = {
    "==": lambda a, b: str(a) == b,
    "!=": lambda a, b: str(a) != b,
    ">": lambda a, b: _numeric_compare(a, b, lambda x, y: x > y),
    "<": lambda a, b: _numeric_compare(a, b, lambda x, y: x < y),
    ">=": lambda a, b: _numeric_compare(a, b, lambda x, y: x >= y),
    "<=": lambda a, b: _numeric_compare(a, b, lambda x, y: x <= y),
}


def _numeric_compare(a: Any, b: str, op) -> bool:
    try:
        return op(float(a), float(b))
    except (TypeError, ValueError):
        return False


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

    # Try the regex matcher
    match = FILTER_RE.match(filter_expression)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Filter must be like: event.type == 'payment.succeeded'",
        )

    path, expected = match.groups()
    actual = get_path(payload, path)

    # Detect which operator was used
    for op_str, op_fn in OPERATORS.items():
        if op_str in filter_expression:
            return op_fn(actual, expected.strip())

    return str(actual) == expected.strip()


def apply_json_map(payload: Any, mapping: Dict[str, str]) -> Dict[str, Any]:
    transformed: Dict[str, Any] = {}
    for output_field, source_path in mapping.items():
        if not isinstance(output_field, str) or not isinstance(source_path, str):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Transform keys and values must be strings",
            )
        transformed[output_field] = get_path(payload, source_path)
    return transformed


def apply_transform(payload: Any, transform_spec: Optional[str]) -> Any:
    """Legacy query-param based JSON transform (json_map only)."""
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
    return apply_json_map(payload, mapping)
