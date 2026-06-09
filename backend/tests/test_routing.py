"""Unit tests for routing — filters, transforms, event ID extraction."""
import pytest
from fastapi import HTTPException

from app.routing import (
    apply_json_map,
    apply_transform,
    event_matches_filter,
    extract_event_id,
    get_path,
)

PAYLOAD = {
    "event": {
        "id": "evt_abc123",
        "type": "payment.succeeded",
        "amount": 2999,
        "currency": "usd",
    },
    "data": {
        "customer_id": "cus_xyz",
        "nested": {"deep": "value"},
    },
}


# ── get_path ──────────────────────────────────────────────────────────────

def test_get_path_simple():
    assert get_path(PAYLOAD, "event.type") == "payment.succeeded"


def test_get_path_nested():
    assert get_path(PAYLOAD, "data.nested.deep") == "value"


def test_get_path_missing():
    assert get_path(PAYLOAD, "does.not.exist") is None


# ── extract_event_id ──────────────────────────────────────────────────────

def test_extract_explicit():
    assert extract_event_id({}, "explicit_id") == "explicit_id"


def test_extract_from_event_id_field():
    assert extract_event_id({"event_id": "evid_1"}) == "evid_1"


def test_extract_from_event_dot_id():
    assert extract_event_id(PAYLOAD) == "evt_abc123"


def test_extract_fallback_uuid():
    eid = extract_event_id({})
    assert len(eid) == 36  # UUID format


# ── event_matches_filter ──────────────────────────────────────────────────

def test_filter_equals_match():
    assert event_matches_filter(PAYLOAD, "event.type == 'payment.succeeded'") is True


def test_filter_equals_no_match():
    assert event_matches_filter(PAYLOAD, "event.type == 'payment.failed'") is False


def test_filter_not_equals():
    assert event_matches_filter(PAYLOAD, "event.type != 'payment.failed'") is True


def test_filter_greater_than():
    assert event_matches_filter(PAYLOAD, "event.amount > 1000") is True
    assert event_matches_filter(PAYLOAD, "event.amount > 9999") is False


def test_filter_none_matches_all():
    assert event_matches_filter(PAYLOAD, None) is True
    assert event_matches_filter(PAYLOAD, "") is True


def test_filter_invalid_expression_raises():
    with pytest.raises(ValueError):
        event_matches_filter(PAYLOAD, "invalid expression here")


# ── apply_json_map ────────────────────────────────────────────────────────

def test_json_map_basic():
    mapping = {"id": "event.id", "type": "event.type", "amount": "event.amount"}
    result = apply_json_map(PAYLOAD, mapping)
    assert result == {"id": "evt_abc123", "type": "payment.succeeded", "amount": 2999}


def test_json_map_missing_path():
    result = apply_json_map(PAYLOAD, {"x": "does.not.exist"})
    assert result == {"x": None}


# ── apply_transform ───────────────────────────────────────────────────────

def test_apply_transform_none():
    result = apply_transform(PAYLOAD, None)
    assert result is PAYLOAD


def test_apply_transform_valid_json():
    import json
    mapping = json.dumps({"event_type": "event.type"})
    result = apply_transform(PAYLOAD, mapping)
    assert result == {"event_type": "payment.succeeded"}


def test_apply_transform_invalid_json():
    with pytest.raises(HTTPException) as exc:
        apply_transform(PAYLOAD, "not json")
    assert exc.value.status_code == 400
