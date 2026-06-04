#!/usr/bin/env python3
"""
Hermes load test — measures ingest throughput and latency.

Usage:
    # Basic (requires a running Hermes + a destination that returns 200):
    python benchmark/loadtest.py --url http://localhost:8000 \
        --destination http://localhost:9000/ok \
        --api-key hk_... \
        --concurrency 20 --duration 30

    # Against the fake downstream bundled in docker-compose:
    docker-compose up -d
    python benchmark/loadtest.py --url http://localhost:8000 \
        --destination http://localhost:9000/ok \
        --duration 30

Output (example):
    ── Hermes Load Test ─────────────────────────────────────────
    Target:       http://localhost:8000/api/v1/ingest
    Destination:  http://localhost:9000/ok
    Concurrency:  20 workers
    Duration:     30 s
    ─────────────────────────────────────────────────────────────
    Requests:     18 432
    Errors:       0  (0.0%)
    Throughput:   614.4 req/s
    Latency P50:   28 ms
    Latency P95:   61 ms
    Latency P99:  104 ms
    Latency max:  312 ms
    ─────────────────────────────────────────────────────────────
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import List

try:
    import httpx
except ImportError:
    print("Install httpx first:  pip install httpx")
    raise SystemExit(1)

PAYLOAD = {
    "event": "benchmark.test",
    "source": "hermes-loadtest",
    "data": {"amount": 1000, "currency": "USD", "customer_id": "cus_benchmark"},
}


async def _worker(
    client: httpx.AsyncClient,
    ingest_url: str,
    destination_url: str,
    stop: asyncio.Event,
    latencies: List[float],
    errors: List[int],
) -> None:
    params = {"url": destination_url}
    while not stop.is_set():
        t0 = time.perf_counter()
        try:
            r = await client.post(ingest_url, json=PAYLOAD, params=params)
            latencies.append((time.perf_counter() - t0) * 1000)
            if r.status_code >= 400:
                errors.append(r.status_code)
        except Exception:
            errors.append(0)


async def run(
    base_url: str,
    destination_url: str,
    api_key: str,
    concurrency: int,
    duration: int,
) -> None:
    ingest_url = f"{base_url.rstrip('/')}/api/v1/ingest"
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Hermes-API-Key"] = api_key

    latencies: List[float] = []
    errors: List[int] = []
    stop = asyncio.Event()

    print(f"\n── Hermes Load Test ─────────────────────────────────────────")
    print(f"Target:       {ingest_url}")
    print(f"Destination:  {destination_url}")
    print(f"Concurrency:  {concurrency} workers")
    print(f"Duration:     {duration} s")
    print(f"─────────────────────────────────────────────────────────────")

    async with httpx.AsyncClient(headers=headers, timeout=15) as client:
        tasks = [
            asyncio.create_task(
                _worker(client, ingest_url, destination_url, stop, latencies, errors)
            )
            for _ in range(concurrency)
        ]
        await asyncio.sleep(duration)
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)

    total = len(latencies) + len(errors)
    err_count = len(errors)
    err_pct = err_count / total * 100 if total else 0
    throughput = total / duration

    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        p_max = latencies[-1]
    else:
        p50 = p95 = p99 = p_max = 0

    print(f"Requests:     {total:,}")
    print(f"Errors:       {err_count:,}  ({err_pct:.1f}%)")
    print(f"Throughput:   {throughput:.1f} req/s")
    print(f"Latency P50:  {p50:>5.0f} ms")
    print(f"Latency P95:  {p95:>5.0f} ms")
    print(f"Latency P99:  {p99:>5.0f} ms")
    print(f"Latency max:  {p_max:>5.0f} ms")
    print(f"─────────────────────────────────────────────────────────────\n")

    # Machine-readable output for CI / README badge generation
    result = {
        "total_requests": total,
        "errors": err_count,
        "error_rate_pct": round(err_pct, 2),
        "throughput_rps": round(throughput, 1),
        "latency_p50_ms": round(p50, 1),
        "latency_p95_ms": round(p95, 1),
        "latency_p99_ms": round(p99, 1),
        "latency_max_ms": round(p_max, 1),
        "concurrency": concurrency,
        "duration_s": duration,
    }
    with open("benchmark/last_result.json", "w") as f:
        json.dump(result, f, indent=2)
    print("Results saved to benchmark/last_result.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Hermes ingest load test")
    ap.add_argument("--url", default="http://localhost:8000", help="Hermes base URL")
    ap.add_argument("--destination", default="http://localhost:9000/ok", help="Destination URL")
    ap.add_argument("--api-key", default="", help="X-Hermes-API-Key value")
    ap.add_argument("--concurrency", type=int, default=20, help="Concurrent workers")
    ap.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    args = ap.parse_args()
    asyncio.run(run(args.url, args.destination, args.api_key, args.concurrency, args.duration))


if __name__ == "__main__":
    main()
