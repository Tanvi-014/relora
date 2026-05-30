"""
Live integration tests against a running Hermes instance.
Run with: HERMES_TEST_BASE_URL=http://localhost:8000 pytest tests/test_api_integration.py

Inside Docker Compose these use http://downstream:9000/ok and /fail.
"""
import os
import uuid
import pytest
import httpx

BASE_URL = os.getenv("HERMES_TEST_BASE_URL", "http://localhost:8000")
DOWNSTREAM_OK = os.getenv("HERMES_TEST_DOWNSTREAM_OK", "http://downstream:9000/ok")
DOWNSTREAM_FAIL = os.getenv("HERMES_TEST_DOWNSTREAM_FAIL", "http://downstream:9000/fail")

_session_token = None


def get_headers():
    if _session_token:
        return {"Authorization": f"Bearer {_session_token}", "Content-Type": "application/json"}
    return {"Content-Type": "application/json"}


@pytest.fixture(scope="session", autouse=True)
def setup_session():
    global _session_token
    email = f"itest_{uuid.uuid4().hex[:8]}@hermes.test"
    password = "IntegrationTest123!"

    r = httpx.post(f"{BASE_URL}/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, f"Register failed: {r.text}"

    r = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200
    _session_token = r.json()["access_token"]


def test_health():
    r = httpx.get(f"{BASE_URL}/health")
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"


def test_detailed_health():
    r = httpx.get(f"{BASE_URL}/health/detailed", headers=get_headers())
    assert r.status_code == 200
    assert "database" in r.json()["checks"]


def test_ingest_and_list():
    ikey = f"itest-{uuid.uuid4().hex}"
    r = httpx.post(
        f"{BASE_URL}/api/v1/ingest",
        headers={**get_headers(), "Idempotency-Key": ikey},
        params={"url": DOWNSTREAM_OK},
        json={"event": "integration.test", "value": 42},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    wid = body["webhook_ids"][0]

    r2 = httpx.get(f"{BASE_URL}/api/v1/webhooks", headers=get_headers())
    assert r2.status_code == 200
    ids = [w["id"] for w in r2.json()["webhooks"]]
    assert wid in ids


def test_idempotency():
    ikey = f"idem-{uuid.uuid4().hex}"
    headers = {**get_headers(), "Idempotency-Key": ikey}
    params = {"url": DOWNSTREAM_OK}
    payload = {"event": "idem.test"}

    r1 = httpx.post(f"{BASE_URL}/api/v1/ingest", headers=headers, params=params, json=payload)
    r2 = httpx.post(f"{BASE_URL}/api/v1/ingest", headers=headers, params=params, json=payload)
    assert r1.json()["webhook_ids"][0] == r2.json()["webhook_ids"][0]


def test_fan_out():
    r = httpx.post(
        f"{BASE_URL}/api/v1/ingest",
        headers=get_headers(),
        params={"url": DOWNSTREAM_OK, "urls": [DOWNSTREAM_OK + "?copy=1"]},
        json={"event": "fanout.test"},
    )
    assert r.status_code == 200
    assert len(r.json()["webhook_ids"]) == 2


def test_filter_rejects():
    r = httpx.post(
        f"{BASE_URL}/api/v1/ingest",
        headers=get_headers(),
        params={"url": DOWNSTREAM_OK, "filter": "event.type == 'payment.succeeded'"},
        json={"event": {"type": "payment.failed"}},
    )
    assert r.status_code == 200
    assert r.json()["filtered"] is True


def test_stats():
    r = httpx.get(f"{BASE_URL}/api/v1/stats", headers=get_headers())
    assert r.status_code == 200
    for k in ("total_webhooks", "pending_count", "completed_count", "failed_count", "success_rate"):
        assert k in r.json()


def test_metrics():
    r = httpx.get(f"{BASE_URL}/metrics", headers=get_headers())
    assert r.status_code == 200
    assert "hermes_webhooks_total" in r.text


def test_simulate_providers():
    r = httpx.get(f"{BASE_URL}/api/v1/simulate/providers", headers=get_headers())
    assert r.status_code == 200
    assert "stripe" in r.json()


def test_destination_crud():
    r = httpx.post(
        f"{BASE_URL}/api/v1/destinations",
        headers=get_headers(),
        json={"name": f"itest-{uuid.uuid4().hex[:6]}", "url": DOWNSTREAM_OK},
    )
    assert r.status_code == 201, r.text
    dest_id = r.json()["id"]

    r = httpx.get(f"{BASE_URL}/api/v1/destinations/{dest_id}", headers=get_headers())
    assert r.status_code == 200

    r = httpx.delete(f"{BASE_URL}/api/v1/destinations/{dest_id}", headers=get_headers())
    assert r.status_code == 204


def test_event_type_crud():
    r = httpx.post(
        f"{BASE_URL}/api/v1/event-types",
        headers=get_headers(),
        json={"name": f"itest.event.{uuid.uuid4().hex[:6]}", "description": "test"},
    )
    assert r.status_code == 201, r.text
    et_id = r.json()["id"]

    r = httpx.delete(f"{BASE_URL}/api/v1/event-types/{et_id}", headers=get_headers())
    assert r.status_code == 204


def test_usage():
    r = httpx.get(f"{BASE_URL}/api/v1/usage", headers=get_headers())
    assert r.status_code == 200
    assert "usage" in r.json()
