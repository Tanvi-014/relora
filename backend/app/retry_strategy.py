"""
Adaptive retry strategy — picks delay and strategy based on failure signal.
Reads HTTP status codes and Retry-After headers to make intelligent decisions.
"""
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Dict, Optional, Tuple


class RetryStrategy(str, Enum):
    EXPONENTIAL = "exponential"
    RESPECT_RETRY_AFTER = "retry_after"
    FAST = "fast"
    LONG = "long"
    NO_RETRY = "no_retry"


def compute_next_attempt(
    attempt_number: int,
    http_status: Optional[int],
    response_headers: Optional[Dict[str, str]],
    error_type: Optional[str],
    base_seconds: int = 30,
) -> Tuple[datetime, RetryStrategy]:
    """
    Returns (next_attempt_at UTC, strategy_used).
    Caller must check strategy == NO_RETRY and move to DLQ immediately.
    """
    now = datetime.now(timezone.utc)
    headers = response_headers or {}

    # Network-level errors: retry fast (blip, DNS hiccup)
    if error_type in ("TimeoutError", "ConnectionError", "DNSError", "NetworkError"):
        delay = min(5 * (2 ** max(attempt_number - 1, 0)), 120)
        return now + timedelta(seconds=delay), RetryStrategy.FAST

    if http_status is None:
        delay = min(base_seconds * (2 ** max(attempt_number - 1, 0)), 3600)
        return now + timedelta(seconds=delay), RetryStrategy.EXPONENTIAL

    # 4xx (except 429) — client error, won't fix itself, no retry
    if 400 <= http_status < 500 and http_status != 429:
        return now, RetryStrategy.NO_RETRY

    # 429 Too Many Requests — respect Retry-After
    if http_status == 429:
        retry_after_raw = headers.get("retry-after") or headers.get("Retry-After")
        delay = 60  # default
        if retry_after_raw:
            try:
                delay = int(retry_after_raw)
            except ValueError:
                delay = 60
        delay = min(delay, 3600)
        return now + timedelta(seconds=delay), RetryStrategy.RESPECT_RETRY_AFTER

    # 503 Service Unavailable — longer cubic backoff
    if http_status == 503:
        delay = min(base_seconds * (3 ** max(attempt_number - 1, 0)), 7200)
        return now + timedelta(seconds=delay), RetryStrategy.LONG

    # Any other 5xx — standard exponential
    if http_status >= 500:
        delay = min(base_seconds * (2 ** max(attempt_number - 1, 0)), 3600)
        return now + timedelta(seconds=delay), RetryStrategy.EXPONENTIAL

    return now + timedelta(seconds=base_seconds), RetryStrategy.EXPONENTIAL
