#!/usr/bin/env python3
"""
Relora load test — measures ingest throughput and latency.

Quick start (handles everything automatically):
    benchmark/run.sh          # Linux / macOS
    benchmark\\run.bat         # Windows

Manual start (if you manage docker-compose yourself):
    docker-compose -f docker-compose.yml -f docker-compose.benchmark.yml up -d
    python benchmark/loadtest.py

The pre-flight check (runs before every test) verifies the rate limit is not
a bottleneck. If it detects 429s it prints the exact fix command and exits
rather than wasting 30 seconds producing invalid results.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
import uuid
from collections import Counter
from typing import List

try:
    import httpx
except ImportError:
    print("Install httpx first:  pip install httpx")
    raise SystemExit(1)

PAYLOAD = {
    "event": "benchmark.test",
    "source": "relora-loadtest",
    "data": {"amount": 1000, "currency": "USD", "customer_id": "cus_benchmark"},
}

_BENCHMARK_COMPOSE_CMD = (
    "docker-compose -f docker-compose.yml -f docker-compose.benchmark.yml up -d"
)


# ── Auto-provisioning ─────────────────────────────────────────────────────────

async def _provision_bench_api_key(base_url: str) -> str:
    """Register a throwaway user and return its project API key.

    Creates a fresh random identity each call so benchmark runs are isolated.
    """
    suffix = uuid.uuid4().hex[:10]
    email = f"bench_{suffix}@relora.bench"
    password = "BenchmarkR1!"

    async with httpx.AsyncClient(base_url=base_url, timeout=15) as client:
        r = await client.post(
            "/api/v1/auth/register",
            json={"email": email, "password": password},
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Auto-provision failed at register (HTTP {r.status_code}): {r.text}\n"
                f"  Is the stack running?  {_BENCHMARK_COMPOSE_CMD}"
            )

        r = await client.post(
            "/api/v1/auth/login",
            json={"email": email, "password": password},
        )
        if r.status_code != 200:
            raise RuntimeError(
                f"Auto-provision failed at login (HTTP {r.status_code}): {r.text}"
            )
        token = r.json()["access_token"]

        r = await client.post(
            "/api/v1/projects",
            json={"name": f"bench-{suffix[:6]}"},
            headers={"Authorization": f"Bearer {token}"},
        )
        if r.status_code not in (200, 201):
            raise RuntimeError(
                f"Auto-provision failed at project creation (HTTP {r.status_code}): {r.text}"
            )
        return r.json()["api_key"]


# ── Pre-flight rate-limit check ───────────────────────────────────────────────

async def _pre_flight_check(base_url: str, destination_url: str, api_key: str) -> None:
    """Fire a burst of concurrent requests and abort if the rate limiter fires.

    Sends 70 simultaneous requests. With RATE_LIMIT_PER_MINUTE=60 (default),
    the token bucket holds 60 tokens: the first 60 requests will succeed and
    the remaining 10+ will return 429. With the benchmark override (100000/min)
    all 70 succeed.

    Exits with a clear error message and the exact docker-compose command if
    rate limiting is detected, rather than running a 30-second test that will
    produce entirely invalid numbers.
    """
    ingest_url = f"{base_url.rstrip('/')}/api/v1/ingest"
    headers = {"Content-Type": "application/json", "X-Relora-API-Key": api_key}
    params = {"url": destination_url}
    probe_n = 70

    print(f"Pre-flight: firing {probe_n} concurrent probe requests...")

    codes: List[int] = []
    async with httpx.AsyncClient(headers=headers, timeout=10) as client:
        responses = await asyncio.gather(
            *[client.post(ingest_url, json=PAYLOAD, params=params) for _ in range(probe_n)],
            return_exceptions=True,
        )

    for r in responses:
        if isinstance(r, Exception):
            codes.append(0)
        else:
            codes.append(r.status_code)

    rate_limited = sum(1 for c in codes if c == 429)
    success = sum(1 for c in codes if 200 <= c < 300)

    if rate_limited > 5:
        estimated_limit = probe_n - rate_limited
        print(
            f"\nFAIL  Pre-flight: rate limiter is active.\n"
            f"      {rate_limited}/{probe_n} probe requests returned HTTP 429.\n"
            f"      Estimated active RATE_LIMIT_PER_MINUTE: ~{estimated_limit * 2}  (should be 100000)\n\n"
            f"      Restart the stack with:\n\n"
            f"          {_BENCHMARK_COMPOSE_CMD}\n\n"
            f"      Or use the runner script:\n"
            f"          benchmark/run.sh       (Linux/macOS)\n"
            f"          benchmark\\run.bat      (Windows)\n"
        )
        raise SystemExit(1)

    print(f"OK    Pre-flight: {success}/{probe_n} succeeded, {rate_limited} 429s.\n")


# ── Worker -------------------------------------------------------------───────

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
            elapsed = (time.perf_counter() - t0) * 1000
            if r.status_code >= 400:
                errors.append(r.status_code)
            else:
                latencies.append(elapsed)
        except Exception:
            errors.append(0)


# ── Main run -------------------------------------------------------------─────

async def run(
    base_url: str,
    destination_url: str,
    api_key: str,
    concurrency: int,
    duration: int,
    skip_pre_flight: bool = False,
) -> None:
    auto_provisioned = False
    if not api_key:
        print("No --api-key provided, auto-provisioning a benchmark project...")
        try:
            api_key = await _provision_bench_api_key(base_url)
            auto_provisioned = True
        except RuntimeError as exc:
            print(f"\nERROR: {exc}\n")
            raise SystemExit(1)

    if not skip_pre_flight:
        await _pre_flight_check(base_url, destination_url, api_key)

    ingest_url = f"{base_url.rstrip('/')}/api/v1/ingest"
    headers = {"Content-Type": "application/json", "X-Relora-API-Key": api_key}

    latencies: List[float] = []
    errors: List[int] = []
    stop = asyncio.Event()

    key_label = f"{api_key[:16]}... ({'auto-provisioned' if auto_provisioned else 'provided'})"
    print(f"== Relora Load Test =========================================")
    print(f"Target:       {ingest_url}")
    print(f"Destination:  {destination_url}")
    print(f"Concurrency:  {concurrency} workers / Duration: {duration} s")
    print(f"API key:      {key_label}")
    print(f"-------------------------------------------------------------")

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
    err_pct = err_count / total * 100 if total else 0.0
    throughput = total / duration

    if latencies:
        latencies.sort()
        p50 = statistics.median(latencies)
        p95 = latencies[int(len(latencies) * 0.95)]
        p99 = latencies[int(len(latencies) * 0.99)]
        p_max = latencies[-1]
    else:
        p50 = p95 = p99 = p_max = 0.0

    print(f"Requests:     {total:,}    Errors: {err_count:,} ({err_pct:.1f}%)")
    print(f"Throughput:   {throughput:.1f} req/s")
    if latencies:
        print(
            f"Latency P50:  {p50:>5.0f} ms"
            f"  P95: {p95:>5.0f} ms"
            f"  P99: {p99:>5.0f} ms"
            f"  max: {p_max:>5.0f} ms"
        )
    else:
        print("Latency:      no successful requests — check stack config")

    if errors:
        breakdown = Counter(errors)
        parts = [f"HTTP {c}: {breakdown[c]:,}" for c in sorted(breakdown) if c != 0]
        if breakdown.get(0):
            parts.append(f"network/timeout: {breakdown[0]:,}")
        print(f"Error codes:  {' | '.join(parts)}")
        if breakdown.get(429):
            print(
                f"  ↳ 429s detected. This should have been caught by the pre-flight.\n"
                f"    Run:  {_BENCHMARK_COMPOSE_CMD}"
            )

    print(f"-------------------------------------------------------------\n")

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


# ── Entry point -------------------------------------------------------------──

def main() -> None:
    ap = argparse.ArgumentParser(description="Relora ingest load test")
    ap.add_argument("--url", default="http://localhost:8000", help="Relora base URL")
    ap.add_argument(
        "--destination",
        default="http://downstream:9000/ok",
        help="Destination URL visible from the Relora API container",
    )
    ap.add_argument(
        "--api-key",
        default="",
        help="X-Relora-API-Key value (auto-provisioned if omitted)",
    )
    ap.add_argument("--concurrency", type=int, default=20, help="Concurrent workers")
    ap.add_argument("--duration", type=int, default=30, help="Test duration in seconds")
    ap.add_argument(
        "--skip-pre-flight",
        action="store_true",
        help="Skip the rate-limit pre-flight check (not recommended)",
    )
    args = ap.parse_args()
    asyncio.run(
        run(
            args.url,
            args.destination,
            args.api_key,
            args.concurrency,
            args.duration,
            args.skip_pre_flight,
        )
    )


if __name__ == "__main__":
    main()
