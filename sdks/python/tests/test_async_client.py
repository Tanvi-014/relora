"""Tests for AsyncReloraClient."""
from __future__ import annotations

import pytest
import respx
import httpx

from relora import AsyncReloraClient, ReloraError

BASE = "http://localhost:8000"
pytestmark = pytest.mark.asyncio


# ── helpers ────────────────────────────────────────────────────────────────────

def _ok(data: dict) -> httpx.Response:
    return httpx.Response(200, json=data)


def _err(data: dict, status: int) -> httpx.Response:
    return httpx.Response(status, json=data)


# ── context manager guard ──────────────────────────────────────────────────────

async def test_requires_context_manager():
    client = AsyncReloraClient(BASE, api_key="hk_test")
    with pytest.raises(RuntimeError, match="context manager"):
        await client.get_stats()


# ── send() ────────────────────────────────────────────────────────────────────

@respx.mock
async def test_send_returns_ingest_response():
    respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc", "status": "pending"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.send("https://example.com/hook", {"event": "test"})
    assert result["webhook_id"] == "abc"
    assert result["status"] == "pending"


@respx.mock
async def test_send_posts_correct_url():
    route = respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.send("https://example.com/hook", {"event": "test"})
    assert route.called
    assert "url=https%3A%2F%2Fexample.com%2Fhook" in str(route.calls[0].request.url)


@respx.mock
async def test_send_sets_api_key_header():
    route = respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc"}))
    async with AsyncReloraClient(BASE, api_key="hk_live_key") as client:
        await client.send("https://example.com/hook", {})
    assert route.calls[0].request.headers["x-relora-api-key"] == "hk_live_key"


@respx.mock
async def test_send_sets_project_id_header():
    route = respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc"}))
    async with AsyncReloraClient(BASE, api_key="hk_test", project_id="proj-uuid") as client:
        await client.send("https://example.com/hook", {})
    assert route.calls[0].request.headers["x-project-id"] == "proj-uuid"


@respx.mock
async def test_send_sets_idempotency_key_header():
    route = respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.send("https://example.com/hook", {}, idempotency_key="idem-42")
    assert route.calls[0].request.headers["idempotency-key"] == "idem-42"


@respx.mock
async def test_send_extra_headers_forwarded():
    route = respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_ok({"webhook_id": "abc"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.send("https://example.com/hook", {}, extra_headers={"X-Source": "billing"})
    assert route.calls[0].request.headers["x-source"] == "billing"


# ── HTTP error → ReloraError ──────────────────────────────────────────────────

@respx.mock
async def test_raises_relora_error_on_404():
    respx.get(f"{BASE}/api/v1/webhooks/bad-id").mock(return_value=_err({"detail": "Webhook not found"}, 404))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        with pytest.raises(ReloraError) as exc_info:
            await client.get_webhook("bad-id")
    assert exc_info.value.status_code == 404
    assert "Webhook not found" in exc_info.value.detail


@respx.mock
async def test_raises_relora_error_on_401():
    respx.get(f"{BASE}/api/v1/stats").mock(return_value=_err({"detail": "Invalid API key"}, 401))
    async with AsyncReloraClient(BASE, api_key="wrong") as client:
        with pytest.raises(ReloraError) as exc_info:
            await client.get_stats()
    assert exc_info.value.status_code == 401


@respx.mock
async def test_raises_relora_error_on_429():
    respx.post(f"{BASE}/api/v1/ingest").mock(return_value=_err({"detail": "Rate limit exceeded"}, 429))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        with pytest.raises(ReloraError) as exc_info:
            await client.send("https://example.com/hook", {})
    assert exc_info.value.status_code == 429


# ── fan_out() ─────────────────────────────────────────────────────────────────

@respx.mock
async def test_fan_out_all_success():
    respx.post(f"{BASE}/api/v1/ingest").mock(side_effect=[
        _ok({"webhook_id": "id-1", "status": "pending"}),
        _ok({"webhook_id": "id-2", "status": "pending"}),
    ])
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        results = await client.fan_out(["https://a.com/h", "https://b.com/h"], {"event": "x"})
    assert len(results) == 2
    ids = {r.get("webhook_id") for r in results}
    assert ids == {"id-1", "id-2"}


@respx.mock
async def test_fan_out_partial_failure_does_not_abort():
    respx.post(f"{BASE}/api/v1/ingest").mock(side_effect=[
        _err({"detail": "Server error"}, 500),
        _ok({"webhook_id": "id-2", "status": "pending"}),
    ])
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        results = await client.fan_out(["https://a.com/h", "https://b.com/h"], {"event": "x"})
    assert len(results) == 2
    errors = [r for r in results if r.get("id") is None]
    successes = [r for r in results if r.get("webhook_id") == "id-2"]
    assert len(errors) == 1
    assert len(successes) == 1
    assert "500" in errors[0]["error"]


# ── webhooks ──────────────────────────────────────────────────────────────────

@respx.mock
async def test_get_webhook():
    respx.get(f"{BASE}/api/v1/webhooks/wh-1").mock(return_value=_ok({"id": "wh-1", "status": "delivered"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.get_webhook("wh-1")
    assert result["id"] == "wh-1"


@respx.mock
async def test_list_webhooks_passes_params():
    route = respx.get(f"{BASE}/api/v1/webhooks").mock(return_value=_ok({"items": [], "total": 0}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.list_webhooks(status="failed", limit=10, offset=20)
    url = str(route.calls[0].request.url)
    assert "status=failed" in url
    assert "limit=10" in url
    assert "offset=20" in url


@respx.mock
async def test_replay_webhook():
    route = respx.post(f"{BASE}/api/v1/webhooks/wh-1/replay").mock(return_value=_ok({"queued": True}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.replay_webhook("wh-1")
    assert route.called
    assert result["queued"] is True


# ── DLQ ───────────────────────────────────────────────────────────────────────

@respx.mock
async def test_list_dlq():
    respx.get(f"{BASE}/api/v1/dlq").mock(return_value=_ok({"items": [], "total": 0}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.list_dlq()
    assert result["total"] == 0


@respx.mock
async def test_replay_all_dlq():
    route = respx.post(f"{BASE}/api/v1/dlq/replay-all").mock(return_value=_ok({"replayed": 5}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.replay_all_dlq()
    assert route.called
    assert result["replayed"] == 5


@respx.mock
async def test_dlq_health():
    respx.get(f"{BASE}/api/v1/dlq/health").mock(return_value=_ok({"score": 92, "status": "healthy"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.dlq_health()
    assert result["score"] == 92


# ── stats & audit ─────────────────────────────────────────────────────────────

@respx.mock
async def test_get_stats():
    respx.get(f"{BASE}/api/v1/stats").mock(return_value=_ok({"total": 100, "delivered": 98}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.get_stats()
    assert result["total"] == 100


@respx.mock
async def test_get_audit_log_params():
    route = respx.get(f"{BASE}/api/v1/audit-log").mock(return_value=_ok({"items": []}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.get_audit_log(resource_type="destination", action="create", limit=5)
    url = str(route.calls[0].request.url)
    assert "resource_type=destination" in url
    assert "action=create" in url
    assert "limit=5" in url


# ── destinations ──────────────────────────────────────────────────────────────

@respx.mock
async def test_list_destinations():
    respx.get(f"{BASE}/api/v1/destinations").mock(return_value=_ok([{"id": "d-1"}]))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.list_destinations()
    assert result[0]["id"] == "d-1"


@respx.mock
async def test_create_destination():
    route = respx.post(f"{BASE}/api/v1/destinations").mock(return_value=_ok({"id": "d-new", "name": "billing"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.create_destination("billing", "https://billing.example.com/hook")
    assert route.called
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "billing"
    assert body["url"] == "https://billing.example.com/hook"
    assert result["id"] == "d-new"


@respx.mock
async def test_update_destination():
    route = respx.put(f"{BASE}/api/v1/destinations/d-1").mock(return_value=_ok({"id": "d-1", "max_retries": 3}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.update_destination("d-1", max_retries=3, is_enabled=False)
    assert route.called
    assert result["max_retries"] == 3


@respx.mock
async def test_delete_destination():
    route = respx.delete(f"{BASE}/api/v1/destinations/d-1").mock(return_value=httpx.Response(204))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.delete_destination("d-1")
    assert route.called


# ── event types ───────────────────────────────────────────────────────────────

@respx.mock
async def test_create_event_type():
    route = respx.post(f"{BASE}/api/v1/event-types").mock(return_value=_ok({"id": "et-1", "name": "order.created"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.create_event_type("order.created", description="New order placed")
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["name"] == "order.created"
    assert body["description"] == "New order placed"
    assert result["id"] == "et-1"


@respx.mock
async def test_list_event_types():
    respx.get(f"{BASE}/api/v1/event-types").mock(return_value=_ok([{"id": "et-1", "name": "order.created"}]))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.list_event_types()
    assert result[0]["name"] == "order.created"


@respx.mock
async def test_delete_event_type():
    route = respx.delete(f"{BASE}/api/v1/event-types/et-1").mock(return_value=httpx.Response(204))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.delete_event_type("et-1")
    assert route.called


# ── alerts ────────────────────────────────────────────────────────────────────

@respx.mock
async def test_list_alerts():
    respx.get(f"{BASE}/api/v1/alerts").mock(return_value=_ok([{"id": "al-1", "name": "slack-ops"}]))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.list_alerts()
    assert result[0]["id"] == "al-1"


@respx.mock
async def test_create_alert():
    route = respx.post(f"{BASE}/api/v1/alerts").mock(return_value=_ok({"id": "al-new", "name": "slack-ops"}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        result = await client.create_alert(
            "slack-ops", "slack", {"webhook_url": "https://hooks.slack.com/test"}
        )
    import json
    body = json.loads(route.calls[0].request.content)
    assert body["channel_type"] == "slack"
    assert result["id"] == "al-new"


@respx.mock
async def test_delete_alert():
    route = respx.delete(f"{BASE}/api/v1/alerts/al-1").mock(return_value=httpx.Response(204))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.delete_alert("al-1")
    assert route.called


# ── context manager cleanup ───────────────────────────────────────────────────

@respx.mock
async def test_client_closes_on_exit():
    respx.get(f"{BASE}/api/v1/stats").mock(return_value=_ok({"total": 0}))
    async with AsyncReloraClient(BASE, api_key="hk_test") as client:
        await client.get_stats()
        inner_client = client._client
    assert inner_client.is_closed
