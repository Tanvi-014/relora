import os
import time
import uuid

import httpx
import pytest


BASE_URL = os.getenv("HERMES_TEST_BASE_URL")
SUCCESS_URL = os.getenv("HERMES_TEST_SUCCESS_URL", "http://downstream:9000/ok")
FAILURE_URL = os.getenv("HERMES_TEST_FAILURE_URL", "http://downstream:9000/fail")

pytestmark = pytest.mark.skipif(
    not BASE_URL,
    reason="Set HERMES_TEST_BASE_URL to run live API integration tests.",
)


def api_client() -> httpx.Client:
    headers = {}
    if os.getenv("HERMES_TEST_API_KEY"):
        headers["X-Hermes-API-Key"] = os.getenv("HERMES_TEST_API_KEY", "")
    return httpx.Client(base_url=BASE_URL, headers=headers, timeout=10.0)


def test_ingest_detail_stats_and_replay_flow():
    destination = SUCCESS_URL
    idempotency_key = f"pytest-{uuid.uuid4()}"

    with api_client() as client:
        ingest_response = client.post(
            f"/api/v1/ingest?url={destination}",
            headers={"Idempotency-Key": idempotency_key},
            json={"event": "pytest.integration.success"},
        )
        assert ingest_response.status_code == 200
        ingest_data = ingest_response.json()
        assert ingest_data["success"] is True
        assert ingest_data["duplicate"] is False

        duplicate_response = client.post(
            f"/api/v1/ingest?url={destination}",
            headers={"Idempotency-Key": idempotency_key},
            json={"event": "pytest.integration.success"},
        )
        assert duplicate_response.status_code == 200
        duplicate_data = duplicate_response.json()
        assert duplicate_data["webhook_id"] == ingest_data["webhook_id"]
        assert duplicate_data["duplicate"] is True

        webhook_id = ingest_data["webhook_id"]
        deadline = time.time() + 20
        detail_data = {}
        while time.time() < deadline:
            detail_response = client.get(f"/api/v1/webhooks/{webhook_id}")
            assert detail_response.status_code == 200
            detail_data = detail_response.json()
            if detail_data["status"] == "completed":
                break
            time.sleep(1)

        assert detail_data["status"] == "completed"
        assert detail_data["idempotency_key"] == idempotency_key
        assert len(detail_data["attempts"]) >= 1

        stats_response = client.get("/api/v1/stats")
        assert stats_response.status_code == 200
        assert stats_response.json()["total_webhooks"] >= 1

        replay_response = client.post(f"/api/v1/webhooks/{webhook_id}/replay")
        assert replay_response.status_code == 200
        assert replay_response.json()["success"] is True


def test_metrics_endpoint_returns_prometheus_text():
    with api_client() as client:
        response = client.get("/metrics")

    assert response.status_code == 200
    assert "hermes_webhooks_total" in response.text
    assert "hermes_delivery_attempts_total" in response.text


def test_failed_delivery_records_attempt_and_schedules_retry():
    with api_client() as client:
        ingest_response = client.post(
            f"/api/v1/ingest?url={FAILURE_URL}",
            json={"event": "pytest.integration.failure"},
        )
        assert ingest_response.status_code == 200
        webhook_id = ingest_response.json()["webhook_id"]

        deadline = time.time() + 20
        detail_data = {}
        while time.time() < deadline:
            detail_response = client.get(f"/api/v1/webhooks/{webhook_id}")
            assert detail_response.status_code == 200
            detail_data = detail_response.json()
            if detail_data["attempts"]:
                break
            time.sleep(1)

        assert detail_data["status"] in {"pending", "failed"}
        assert detail_data["retry_count"] >= 1
        assert detail_data["attempts"][0]["status_code"] == 500
        assert detail_data["attempts"][0]["error_message"] == "HTTP Error Status 500"
