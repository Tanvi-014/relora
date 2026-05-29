from unittest.mock import Mock
import asyncio
import hmac
import time
from hashlib import sha256

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routing import apply_transform, event_matches_filter, extract_event_id
from app.security import require_api_key, validate_destination_url
from app.signatures import verify_webhook_signature


def test_validate_destination_url_accepts_public_url(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", False)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    assert validate_destination_url("https://example.com/webhook") == "https://example.com/webhook"


def test_validate_destination_url_rejects_invalid_scheme():
    with pytest.raises(HTTPException) as exc:
        validate_destination_url("ftp://example.com/webhook")

    assert exc.value.status_code == 400


def test_validate_destination_url_rejects_private_ip_when_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", False)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    with pytest.raises(HTTPException) as exc:
        validate_destination_url("http://127.0.0.1:3000/webhook")

    assert exc.value.status_code == 400


def test_validate_destination_url_allows_private_host_for_local_demo(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", True)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")

    assert validate_destination_url("http://localhost:3000/webhook") == "http://localhost:3000/webhook"


def test_validate_destination_url_enforces_allowlist(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", True)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "hooks.example.com")

    with pytest.raises(HTTPException) as exc:
        validate_destination_url("https://other.example.com/webhook")

    assert exc.value.status_code == 400


def test_destination_host_allowlist_parses_csv(monkeypatch):
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", " hooks.example.com, api.example.com ")

    assert settings.destination_host_allowlist == ["hooks.example.com", "api.example.com"]


def test_require_api_key_noops_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "")
    monkeypatch.setattr(settings, "HERMES_API_KEYS", "")
    request = Mock()
    request.headers = {}

    assert asyncio.run(require_api_key(request)) == "anonymous"


def test_require_api_key_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "secret")
    monkeypatch.setattr(settings, "HERMES_API_KEYS", "")
    request = Mock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_api_key(request))

    assert exc.value.status_code == 401


def test_require_api_key_accepts_matching_key(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "secret")
    monkeypatch.setattr(settings, "HERMES_API_KEYS", "")
    request = Mock()
    request.headers = {"X-Hermes-API-Key": "secret"}

    assert asyncio.run(require_api_key(request)) == "default"


def test_require_api_key_maps_named_tenant(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_API_KEY", "")
    monkeypatch.setattr(settings, "HERMES_API_KEYS", "acme:key-acme")
    request = Mock()
    request.headers = {"X-Hermes-API-Key": "key-acme"}

    assert asyncio.run(require_api_key(request)) == "acme"


def test_event_filter_matches_nested_field():
    payload = {"event": {"type": "payment.succeeded"}}

    assert event_matches_filter(payload, "event.type == 'payment.succeeded'") is True
    assert event_matches_filter(payload, "event.type == 'payment.failed'") is False


def test_apply_transform_maps_fields():
    payload = {"event": {"id": "evt_123", "type": "payment.succeeded"}}

    assert apply_transform(payload, '{"id":"event.id","type":"event.type"}') == {
        "id": "evt_123",
        "type": "payment.succeeded",
    }


def test_extract_event_id_prefers_payload_id():
    assert extract_event_id({"event": {"id": "evt_123"}}) == "evt_123"


def test_verify_hermes_signature(monkeypatch):
    monkeypatch.setattr(settings, "HERMES_WEBHOOK_SECRET", "topsecret")
    body = b'{"event":"signed"}'
    signature = hmac.new(b"topsecret", body, sha256).hexdigest()
    request = Mock()
    request.headers = {
        "X-Hermes-Signature": signature,
        "X-Hermes-Signature-Algorithm": "sha256",
    }

    assert verify_webhook_signature("hermes", request, body) is None


def test_verify_stripe_signature(monkeypatch):
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", "whsec_test")
    monkeypatch.setattr(settings, "SIGNATURE_TOLERANCE_SECONDS", 300)
    body = b'{"id":"evt_123"}'
    timestamp = int(time.time())
    signed_payload = f"{timestamp}.".encode("utf-8") + body
    signature = hmac.new(b"whsec_test", signed_payload, sha256).hexdigest()
    request = Mock()
    request.headers = {"Stripe-Signature": f"t={timestamp},v1={signature}"}

    assert verify_webhook_signature("stripe", request, body) is None
