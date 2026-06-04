import argparse
import sys
import time
import uuid

import httpx


def request_headers(api_key: str | None = None, idempotency_key: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if api_key:
        headers["X-Relora-API-Key"] = api_key
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return headers


def wait_for_status(
    client: httpx.Client,
    webhook_id: str,
    statuses: set[str],
    timeout_seconds: int,
    min_attempts: int = 0,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_detail: dict = {}

    while time.time() < deadline:
        response = client.get(f"/api/v1/webhooks/{webhook_id}")
        response.raise_for_status()
        last_detail = response.json()
        if last_detail["status"] in statuses and len(last_detail.get("attempts", [])) >= min_attempts:
            return last_detail
        time.sleep(1)

    return last_detail


def print_result(label: str, detail: dict) -> None:
    attempts = detail.get("attempts", [])
    print(f"{label}: {detail['status']} after {len(attempts)} attempt(s)")
    if attempts:
        latest = attempts[-1]
        print(f"  latest status_code={latest.get('status_code')} error={latest.get('error_message')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a Relora webhook delivery demo.")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Relora API base URL.")
    parser.add_argument("--api-key", default=None, help="Optional Relora API key.")
    parser.add_argument("--success-url", default="http://downstream:9000/ok", help="Destination that returns 2xx.")
    parser.add_argument("--failure-url", default="http://downstream:9000/fail", help="Destination that returns non-2xx.")
    parser.add_argument("--timeout", type=int, default=45, help="Seconds to wait for demo status changes.")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=10.0, headers=request_headers(args.api_key)) as client:
        health = client.get("/health")
        health.raise_for_status()
        print(f"Relora health: {health.json()['status']}")

        idempotency_key = f"demo-{uuid.uuid4()}"
        success = client.post(
            f"/api/v1/ingest?url={args.success_url}",
            headers=request_headers(args.api_key, idempotency_key),
            json={"event": "demo.success", "idempotency_key": idempotency_key},
        )
        success.raise_for_status()
        success_data = success.json()
        print(f"Success webhook queued: {success_data['webhook_id']}")

        duplicate = client.post(
            f"/api/v1/ingest?url={args.success_url}",
            headers=request_headers(args.api_key, idempotency_key),
            json={"event": "demo.success", "idempotency_key": idempotency_key},
        )
        duplicate.raise_for_status()
        duplicate_data = duplicate.json()
        print(f"Duplicate ingest reused webhook: {duplicate_data['webhook_id']} duplicate={duplicate_data['duplicate']}")

        success_detail = wait_for_status(client, success_data["webhook_id"], {"completed"}, args.timeout)
        print_result("Successful delivery", success_detail)

        failure = client.post(
            f"/api/v1/ingest?url={args.failure_url}",
            json={"event": "demo.failure"},
        )
        failure.raise_for_status()
        failure_data = failure.json()
        print(f"Failure webhook queued: {failure_data['webhook_id']}")

        retry_detail = wait_for_status(client, failure_data["webhook_id"], {"pending", "failed"}, args.timeout, min_attempts=1)
        print_result("Failure/retry path", retry_detail)

        fanout_key = f"demo-fanout-{uuid.uuid4()}"
        transform = '{"id":"event.id","type":"event.type","amount":"event.amount"}'
        fanout = client.post(
            "/api/v1/ingest",
            params=[
                ("url", args.success_url),
                ("urls", f"{args.success_url}?copy=analytics"),
                ("filter", "event.type == 'payment.succeeded'"),
                ("transform", transform),
            ],
            headers=request_headers(args.api_key, fanout_key),
            json={"event": {"id": fanout_key, "type": "payment.succeeded", "amount": 2999}},
        )
        fanout.raise_for_status()
        fanout_data = fanout.json()
        print(f"Fan-out queued {len(fanout_data['webhook_ids'])} destination(s): {fanout_data['webhook_ids']}")

        filtered = client.post(
            "/api/v1/ingest",
            params={"url": args.success_url, "filter": "event.type == 'payment.succeeded'"},
            json={"event": {"id": f"demo-filter-{uuid.uuid4()}", "type": "payment.failed"}},
        )
        filtered.raise_for_status()
        print(f"Filtered non-matching event: {filtered.json()['filtered']}")

        replay = client.post(f"/api/v1/webhooks/{failure_data['webhook_id']}/replay")
        replay.raise_for_status()
        print(f"Replay requested: {replay.json()['success']}")

        stats = client.get("/api/v1/stats")
        stats.raise_for_status()
        print(f"Stats: {stats.json()}")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPError as exc:
        print(f"Demo failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
