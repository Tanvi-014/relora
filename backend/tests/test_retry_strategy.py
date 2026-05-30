"""Unit tests for adaptive retry strategy."""
import pytest
from datetime import timezone
from app.retry_strategy import compute_next_attempt, RetryStrategy


def test_network_error_fast_retry():
    next_at, strategy = compute_next_attempt(1, None, {}, "TimeoutError", base_seconds=30)
    assert strategy == RetryStrategy.FAST
    diff = (next_at - _now()).total_seconds()
    assert 0 < diff <= 120


def test_4xx_no_retry():
    next_at, strategy = compute_next_attempt(1, 400, {}, None, base_seconds=30)
    assert strategy == RetryStrategy.NO_RETRY

    next_at, strategy = compute_next_attempt(1, 404, {}, None, base_seconds=30)
    assert strategy == RetryStrategy.NO_RETRY

    # 429 should NOT be no-retry
    next_at, strategy = compute_next_attempt(1, 429, {}, None, base_seconds=30)
    assert strategy != RetryStrategy.NO_RETRY


def test_429_respects_retry_after_header():
    next_at, strategy = compute_next_attempt(1, 429, {"retry-after": "120"}, None, base_seconds=30)
    assert strategy == RetryStrategy.RESPECT_RETRY_AFTER
    diff = (next_at - _now()).total_seconds()
    assert 100 < diff <= 130


def test_429_default_backoff_without_header():
    next_at, strategy = compute_next_attempt(1, 429, {}, None, base_seconds=30)
    assert strategy == RetryStrategy.RESPECT_RETRY_AFTER
    diff = (next_at - _now()).total_seconds()
    assert diff > 0


def test_503_long_backoff():
    next_at, strategy = compute_next_attempt(1, 503, {}, None, base_seconds=30)
    assert strategy == RetryStrategy.LONG
    # 503 uses cubic: 30 * 3^0 = 30s for attempt 1
    diff = (next_at - _now()).total_seconds()
    assert diff > 0


def test_500_exponential():
    next_at, strategy = compute_next_attempt(1, 500, {}, None, base_seconds=30)
    assert strategy == RetryStrategy.EXPONENTIAL

    next_at2, _ = compute_next_attempt(2, 500, {}, None, base_seconds=30)
    # Second attempt should be further in future
    assert next_at2 > next_at


def test_network_error_types():
    for err_type in ("ConnectionError", "DNSError", "NetworkError"):
        _, strategy = compute_next_attempt(1, None, {}, err_type, base_seconds=30)
        assert strategy == RetryStrategy.FAST


def test_exponential_capped_at_3600():
    # Very high attempt number should cap at 3600s
    next_at, _ = compute_next_attempt(20, 500, {}, None, base_seconds=30)
    diff = (next_at - _now()).total_seconds()
    assert diff <= 3601


def _now():
    from datetime import datetime
    return datetime.now(timezone.utc)
