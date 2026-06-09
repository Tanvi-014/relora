"""
Postgres-backed token bucket rate limiter.
Works correctly across multiple API processes — no in-memory state.
"""
import logging
from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings

logger = logging.getLogger("relora.rate_limit")


async def check_rate_limit(
    request: Request,
    tenant_id: str,
    db: AsyncSession,
    max_per_minute: int = 0,  # 0 = use settings.RATE_LIMIT_PER_MINUTE
) -> None:
    max_per_minute = max_per_minute or settings.RATE_LIMIT_PER_MINUTE
    max_tokens = float(max_per_minute)
    refill_rate = max_tokens / 60.0  # tokens per second

    # Key: prefer tenant_id (if real tenant), else fall back to IP
    if tenant_id and tenant_id != "anonymous":
        key = f"tenant:{tenant_id}"
    else:
        forwarded = request.headers.get("X-Forwarded-For", "")
        ip = forwarded.split(",")[0].strip() if forwarded else (
            request.client.host if request.client else "unknown"
        )
        key = f"ip:{ip}"

    bucket_key = f"rl:{key}"

    try:
        result = await db.execute(
            text("""
            INSERT INTO rate_limit_buckets (key, tokens, last_refill, max_tokens, refill_rate)
            VALUES (:key, :max_tokens - 1, NOW(), :max_tokens, :refill_rate)
            ON CONFLICT (key) DO UPDATE SET
              tokens = LEAST(
                rate_limit_buckets.max_tokens,
                rate_limit_buckets.tokens +
                  EXTRACT(EPOCH FROM (NOW() - rate_limit_buckets.last_refill)) * rate_limit_buckets.refill_rate
              ) - 1,
              last_refill = NOW()
            WHERE (
              rate_limit_buckets.tokens +
              EXTRACT(EPOCH FROM (NOW() - rate_limit_buckets.last_refill)) * rate_limit_buckets.refill_rate
            ) >= 1
            RETURNING tokens
            """),
            {"key": bucket_key, "max_tokens": max_tokens, "refill_rate": refill_rate},
        )
        # This commit is intentional and load-bearing: it persists the token spend
        # before ingest_webhook writes webhook rows, so a rolled-back webhook insert
        # cannot also roll back the rate-limit deduction and bypass the limiter.
        # Do NOT merge this into the caller's transaction or reorder it after the webhook insert.
        await db.commit()
        row = result.fetchone()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Max {max_per_minute} requests/minute.",
                headers={"Retry-After": "60"},
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Rate limiter failure must not block requests
        logger.warning("Rate limiter DB error, allowing request through: %s", exc)
