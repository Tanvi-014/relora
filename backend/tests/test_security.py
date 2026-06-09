from unittest.mock import Mock, patch, call
import asyncio
import hmac
import socket
import time
from hashlib import sha256

import pytest
from fastapi import HTTPException

from app.config import settings
from app.routing import apply_transform, event_matches_filter, extract_event_id
from app.security import require_api_key, validate_destination_url
from app.signatures import verify_webhook_signature

# Fake public DNS response: 93.184.216.34 is example.com's real IP (public, non-private)
_PUBLIC_ADDRINFO = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def test_validate_destination_url_accepts_public_url(monkeypatch):
    monkeypatch.setattr(settings, "ALLOW_PRIVATE_DESTINATIONS", False)
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", "")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _PUBLIC_ADDRINFO)

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
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **kw: _PUBLIC_ADDRINFO)

    with pytest.raises(HTTPException) as exc:
        validate_destination_url("https://other.example.com/webhook")

    assert exc.value.status_code == 400


def test_destination_host_allowlist_parses_csv(monkeypatch):
    monkeypatch.setattr(settings, "DESTINATION_HOST_ALLOWLIST", " hooks.example.com, api.example.com ")

    assert settings.destination_host_allowlist == ["hooks.example.com", "api.example.com"]


def test_require_api_key_noops_when_unset(monkeypatch):
    monkeypatch.setattr(settings, "RELORA_API_KEY", "")
    monkeypatch.setattr(settings, "RELORA_API_KEYS", "")
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    request = Mock()
    request.headers = {}

    assert asyncio.run(require_api_key(request)) == "anonymous"


def test_require_api_key_rejects_missing_key(monkeypatch):
    monkeypatch.setattr(settings, "RELORA_API_KEY", "secret")
    monkeypatch.setattr(settings, "RELORA_API_KEYS", "")
    request = Mock()
    request.headers = {}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(require_api_key(request))

    assert exc.value.status_code == 401


def test_require_api_key_accepts_matching_key(monkeypatch):
    monkeypatch.setattr(settings, "RELORA_API_KEY", "secret")
    monkeypatch.setattr(settings, "RELORA_API_KEYS", "")
    request = Mock()
    request.headers = {"X-Relora-API-Key": "secret"}

    assert asyncio.run(require_api_key(request)) == "default"


def test_require_api_key_maps_named_tenant(monkeypatch):
    monkeypatch.setattr(settings, "RELORA_API_KEY", "")
    monkeypatch.setattr(settings, "RELORA_API_KEYS", "acme:key-acme")
    request = Mock()
    request.headers = {"X-Relora-API-Key": "key-acme"}

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


def test_verify_relora_signature(monkeypatch):
    monkeypatch.setattr(settings, "RELORA_WEBHOOK_SECRET", "topsecret")
    body = b'{"event":"signed"}'
    signature = hmac.new(b"topsecret", body, sha256).hexdigest()
    request = Mock()
    request.headers = {
        "X-Relora-Signature": signature,
        "X-Relora-Signature-Algorithm": "sha256",
    }

    assert verify_webhook_signature("relora", request, body) is None


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


# ---------------------------------------------------------------------------
# WebSocket auth bypass
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_websocket_closes_4001_without_token_or_valid_project_key():
    """
    A WebSocket connection with an unknown project_key and no JWT token must
    be closed with code 4001 (Unauthorized).
    """
    from unittest.mock import AsyncMock, MagicMock

    # DB returns no project for the given key
    mock_db = AsyncMock()
    no_project = MagicMock()
    no_project.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_project)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()

    # Patch at source — the function imports async_session from app.db at call time
    from app.routers.system import websocket_endpoint
    with patch("app.db.async_session", return_value=ctx):
        await websocket_endpoint(websocket=mock_ws, project_key="unknown-key", token=None)

    mock_ws.close.assert_called_once_with(code=4001)


@pytest.mark.anyio
async def test_websocket_closes_4001_with_invalid_jwt():
    """
    A WebSocket connection with an invalid/expired JWT and an unknown project key
    must be rejected with code 4001.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_db = AsyncMock()
    no_project = MagicMock()
    no_project.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=no_project)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()

    from app.routers.system import websocket_endpoint
    with patch("app.db.async_session", return_value=ctx), \
         patch("app.auth.decode_access_token", return_value=None):
        await websocket_endpoint(websocket=mock_ws, project_key="unknown-key", token="bad.jwt.token")

    mock_ws.close.assert_called_once_with(code=4001)


@pytest.mark.anyio
async def test_websocket_accepts_valid_project_api_key():
    """
    A WebSocket connection where project_key matches a real project api_key
    (SDK / programmatic use) must be accepted and NOT close with 4001.
    """
    from unittest.mock import AsyncMock, MagicMock

    mock_project = MagicMock()

    mock_db = AsyncMock()
    found_project = MagicMock()
    found_project.scalar_one_or_none.return_value = mock_project
    mock_db.execute = AsyncMock(return_value=found_project)

    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_db)
    ctx.__aexit__ = AsyncMock(return_value=False)

    # send_text raises to break the keep-alive loop immediately
    mock_ws = AsyncMock()
    mock_ws.close = AsyncMock()
    mock_ws.send_text = AsyncMock(side_effect=Exception("disconnect"))

    from app.routers.system import websocket_endpoint
    with patch("app.db.async_session", return_value=ctx), \
         patch("app.routers.system.ws_manager") as mock_manager:
        mock_manager.connect = AsyncMock()
        mock_manager.disconnect = AsyncMock()
        await websocket_endpoint(websocket=mock_ws, project_key="hk_live_abc123", token=None)

    # close(4001) must NOT have been called — connection was accepted
    for c in mock_ws.close.call_args_list:
        assert c != call(code=4001), "WS was closed with 4001 for a valid project key"
