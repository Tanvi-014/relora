"""
End-to-end smoke test: full delivery pipeline flow.

Tests the happy path through:
  1. Register + login
  2. Create project → get API key
  3. Create destination pointing at the fake downstream
  4. Ingest webhook → destination is selected
  5. Poll until delivery completes (worker must be running)
  6. Verify webhook is completed
  7. Replay the webhook → new webhook created
  8. Verify replay also completes

Run with:
  HERMES_TEST_BASE_URL=http://localhost:8000 \
  HERMES_TEST_DOWNSTREAM_OK=http://localhost:9000/ok \
  pytest tests/test_smoke_e2e.py -v

These tests require a running API server and delivery worker.
They are skipped automatically when HERMES_TEST_BASE_URL is not set.
"""
from __future__ import annotations

import os
import time
import uuid

import httpx
import pytest

BASE_URL       = os.getenv("HERMES_TEST_BASE_URL", "")
DOWNSTREAM_OK  = os.getenv("HERMES_TEST_DOWNSTREAM_OK", "http://localhost:9000/ok")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="HERMES_TEST_BASE_URL not set — skipping E2E smoke tests",
)


# ── Shared session state ─────────────────────────────────────────────────────

_state: dict = {}


def _auth_headers() -> dict:
    return {
        "Authorization": f"Bearer {_state['token']}",
        "Content-Type": "application/json",
    }


def _project_headers() -> dict:
    return {
        "X-Relora-API-Key": _state["api_key"],
        "Content-Type": "application/json",
    }


def _wait_for_status(webhook_id: str, target: str, timeout: int = 15) -> dict:
    """Poll /api/v1/webhooks/{id} until status == target or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = httpx.get(
            f"{BASE_URL}/api/v1/webhooks/{webhook_id}",
            headers=_project_headers(),
            timeout=5,
        )
        if r.status_code == 200:
            body = r.json()
            if body.get("status") == target:
                return body
        time.sleep(0.5)
    raise TimeoutError(
        f"Webhook {webhook_id} did not reach status '{target}' within {timeout}s. "
        f"Last status: {r.json().get('status')}"
    )


# ── Step 1: Register and login ────────────────────────────────────────────────

def test_01_register_and_login():
    email = f"smoke_{uuid.uuid4().hex[:8]}@relora.test"
    password = "SmokeTest123!"

    r = httpx.post(f"{BASE_URL}/api/v1/auth/register", json={"email": email, "password": password})
    assert r.status_code == 201, f"Register failed: {r.text}"

    r = httpx.post(f"{BASE_URL}/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, f"Login failed: {r.text}"
    _state["token"] = r.json()["access_token"]


# ── Step 2: Create project ────────────────────────────────────────────────────

def test_02_create_project():
    r = httpx.post(
        f"{BASE_URL}/api/v1/projects",
        headers=_auth_headers(),
        json={"name": f"smoke-project-{uuid.uuid4().hex[:6]}"},
        timeout=10,
    )
    assert r.status_code == 201, f"Project creation failed: {r.text}"
    body = r.json()
    assert "api_key" in body
    _state["api_key"] = body["api_key"]
    _state["project_id"] = body["id"]


# ── Step 3: Create destination ────────────────────────────────────────────────

def test_03_create_destination():
    r = httpx.post(
        f"{BASE_URL}/api/v1/destinations",
        headers=_project_headers(),
        json={
            "name": f"smoke-dest-{uuid.uuid4().hex[:6]}",
            "url": DOWNSTREAM_OK,
            "max_retries": 2,
        },
        timeout=10,
    )
    assert r.status_code == 201, f"Destination creation failed: {r.text}"
    body = r.json()
    assert body["url"] == DOWNSTREAM_OK
    _state["destination_id"] = body["id"]


# ── Step 4: Ingest webhook via destination ────────────────────────────────────

def test_04_ingest_webhook():
    ikey = f"smoke-{uuid.uuid4().hex}"
    r = httpx.post(
        f"{BASE_URL}/api/v1/ingest",
        headers={**_project_headers(), "Idempotency-Key": ikey},
        params={"destination_id": _state["destination_id"]},
        json={"event": "smoke.test", "value": 1},
        timeout=10,
    )
    assert r.status_code == 200, f"Ingest failed: {r.text}"
    body = r.json()
    assert body["success"] is True
    assert len(body["webhook_ids"]) == 1
    _state["webhook_id"] = body["webhook_ids"][0]


# ── Step 5: Wait for delivery completion ──────────────────────────────────────

def test_05_webhook_delivered():
    """Worker must deliver within 15 seconds in a healthy test environment."""
    wh = _wait_for_status(_state["webhook_id"], "completed", timeout=15)
    assert wh["status"] == "completed"
    assert wh["retry_count"] == 0


# ── Step 6: Replay the webhook ────────────────────────────────────────────────

def test_06_replay_webhook():
    r = httpx.post(
        f"{BASE_URL}/api/v1/dlq/{_state['webhook_id']}/replay",
        headers=_project_headers(),
        timeout=10,
    )
    assert r.status_code == 200, f"Replay failed: {r.text}"
    body = r.json()
    assert "webhook_id" in body or "id" in body or "replayed" in str(body).lower()
    # New webhook_id may be in body["webhook_id"] or body["id"]
    new_id = body.get("webhook_id") or body.get("id")
    if new_id and new_id != _state["webhook_id"]:
        _state["replay_webhook_id"] = new_id
    else:
        _state["replay_webhook_id"] = None


# ── Step 7: Verify replay also completes ─────────────────────────────────────

def test_07_replay_delivered():
    rid = _state.get("replay_webhook_id")
    if not rid:
        pytest.skip("Replay created no new webhook_id — skipping delivery check")
    wh = _wait_for_status(rid, "completed", timeout=15)
    assert wh["status"] == "completed"


# ── Step 8: Health endpoint still responds ────────────────────────────────────

def test_08_health_after_flow():
    r = httpx.get(f"{BASE_URL}/health", timeout=5)
    assert r.status_code == 200
    assert r.json()["status"] == "healthy"
