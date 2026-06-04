"""Tests for ReloraClient (synchronous, stdlib-only)."""
from __future__ import annotations

import json
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, call, patch

import pytest

from relora import ReloraClient, ReloraError
from relora.async_client import AsyncReloraClient

BASE = "http://localhost:8000"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _ok_response(data: dict):
    """Build a mock urlopen context manager that returns `data` as JSON."""
    body = json.dumps(data).encode()
    mock = MagicMock()
    mock.read.return_value = body
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


def _http_error(data: dict, status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="http://test",
        code=status,
        msg="Error",
        hdrs=None,
        fp=BytesIO(json.dumps(data).encode()),
    )


# ── ReloraError ────────────────────────────────────────────────────────────────

def test_relora_error_attributes():
    err = ReloraError(429, "Rate limit exceeded")
    assert err.status_code == 429
    assert err.detail == "Rate limit exceeded"
    assert str(err) == "HTTP 429: Rate limit exceeded"


# ── send() ────────────────────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_send_returns_ingest_response(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc", "status": "pending"})
    client = ReloraClient(BASE, api_key="hk_test")
    result = client.send("https://example.com/hook", {"event": "test"})
    assert result["webhook_id"] == "abc"
    assert result["status"] == "pending"


@patch("urllib.request.urlopen")
def test_send_posts_to_ingest_url(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc"})
    client = ReloraClient(BASE, api_key="hk_test")
    client.send("https://example.com/hook", {"event": "test"})
    req = mock_open.call_args[0][0]
    assert "/api/v1/ingest" in req.full_url
    assert req.get_method() == "POST"


@patch("urllib.request.urlopen")
def test_send_sets_api_key_header(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc"})
    client = ReloraClient(BASE, api_key="hk_live_key")
    client.send("https://example.com/hook", {})
    req = mock_open.call_args[0][0]
    assert req.get_header("X-relora-api-key") == "hk_live_key"


@patch("urllib.request.urlopen")
def test_send_sets_idempotency_key_header(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc"})
    client = ReloraClient(BASE, api_key="hk_test")
    client.send("https://example.com/hook", {}, idempotency_key="idem-1")
    req = mock_open.call_args[0][0]
    assert req.get_header("Idempotency-key") == "idem-1"


@patch("urllib.request.urlopen")
def test_send_sets_project_id_header(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc"})
    client = ReloraClient(BASE, api_key="hk_test", project_id="proj-uuid")
    client.send("https://example.com/hook", {})
    req = mock_open.call_args[0][0]
    assert req.get_header("X-project-id") == "proj-uuid"


@patch("urllib.request.urlopen")
def test_send_extra_headers_forwarded(mock_open):
    mock_open.return_value = _ok_response({"webhook_id": "abc"})
    client = ReloraClient(BASE, api_key="hk_test")
    client.send("https://example.com/hook", {}, extra_headers={"X-Source": "billing"})
    req = mock_open.call_args[0][0]
    assert req.get_header("X-source") == "billing"


# ── ReloraError on HTTP failures ──────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_raises_relora_error_on_404(mock_open):
    mock_open.side_effect = _http_error({"detail": "Webhook not found"}, 404)
    client = ReloraClient(BASE, api_key="hk_test")
    with pytest.raises(ReloraError) as exc_info:
        client.get_webhook("bad-id")
    assert exc_info.value.status_code == 404
    assert "Webhook not found" in exc_info.value.detail


@patch("urllib.request.urlopen")
def test_raises_relora_error_on_401(mock_open):
    mock_open.side_effect = _http_error({"detail": "Invalid API key"}, 401)
    client = ReloraClient(BASE, api_key="wrong")
    with pytest.raises(ReloraError) as exc_info:
        client.get_stats()
    assert exc_info.value.status_code == 401


# ── fan_out() ─────────────────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_fan_out_all_success(mock_open):
    mock_open.side_effect = [
        _ok_response({"webhook_id": "id-1", "status": "pending"}),
        _ok_response({"webhook_id": "id-2", "status": "pending"}),
    ]
    client = ReloraClient(BASE, api_key="hk_test")
    results = client.fan_out(["https://a.com/h", "https://b.com/h"], {"event": "x"})
    assert len(results) == 2
    assert results[0]["webhook_id"] == "id-1"
    assert results[1]["webhook_id"] == "id-2"


@patch("urllib.request.urlopen")
def test_fan_out_partial_failure_does_not_abort(mock_open):
    mock_open.side_effect = [
        _http_error({"detail": "Server error"}, 500),
        _ok_response({"webhook_id": "id-2", "status": "pending"}),
    ]
    client = ReloraClient(BASE, api_key="hk_test")
    results = client.fan_out(["https://a.com/h", "https://b.com/h"], {"event": "x"})
    assert len(results) == 2
    assert results[0]["id"] is None
    assert "HTTP 500" in results[0]["error"]
    assert results[1]["webhook_id"] == "id-2"


# ── destinations ──────────────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_update_destination(mock_open):
    mock_open.return_value = _ok_response({"id": "dest-1", "max_retries": 3})
    client = ReloraClient(BASE, api_key="hk_test")
    result = client.update_destination("dest-1", max_retries=3, is_enabled=False)
    req = mock_open.call_args[0][0]
    assert "/api/v1/destinations/dest-1" in req.full_url
    assert req.get_method() == "PUT"
    assert result["id"] == "dest-1"


# ── event types ───────────────────────────────────────────────────────────────

@patch("urllib.request.urlopen")
def test_create_event_type(mock_open):
    mock_open.return_value = _ok_response({"id": "et-1", "name": "order.created"})
    client = ReloraClient(BASE, api_key="hk_test")
    result = client.create_event_type("order.created", description="New order")
    req = mock_open.call_args[0][0]
    assert "/api/v1/event-types" in req.full_url
    body = json.loads(req.data.decode())
    assert body["name"] == "order.created"
    assert body["description"] == "New order"


# ── imports ───────────────────────────────────────────────────────────────────

def test_async_client_importable_from_root():
    from relora import AsyncReloraClient  # noqa: F401
    assert AsyncReloraClient is not None


def test_relora_error_importable_from_root():
    from relora import ReloraError  # noqa: F401
    assert ReloraError is not None


def test_version_defined():
    import relora
    assert hasattr(relora, "__version__")
    assert isinstance(relora.__version__, str)


# ── async client ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_async_client_requires_context_manager():
    client = AsyncReloraClient(BASE, api_key="hk_test")
    with pytest.raises(RuntimeError, match="context manager"):
        await client.get_stats()
