"""
Delivery worker — claims webhooks atomically via SELECT FOR UPDATE SKIP LOCKED,
delivers with Standard Webhooks signing, adaptive retry, and circuit breaker.
"""
import asyncio
import base64
import json
import logging
import time
import zlib
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts import dispatch_dlq_alert
from app.circuit_breaker import should_deliver, record_outcome, HALF_OPEN_TIMEOUT
from app.telemetry import record_delivered, record_delivery_failed, record_dlq
from app.config import settings
from app.db import async_session
from app.failure_classifier import FailureClassifier
from app.incident_engine import IncidentEngine
from app.logging_config import configure_logging
from app.models import Webhook, WebhookStatus, DeliveryAttempt, Destination
from app.retry_strategy import compute_next_attempt, RetryStrategy
from app.sse_hub import sse_hub
from app.standard_webhooks import sign_outbound_webhook
from app.websocket_hub import ws_manager

configure_logging()
logger = logging.getLogger("relora.worker")

# Set by the LISTEN/NOTIFY watcher in worker_main to wake idle workers immediately.
_wake_workers: asyncio.Event = asyncio.Event()

# FIFO-aware claim query:
# - Ordered webhooks (ordering_key set) are delivered strictly by creation order.
# - If another webhook with the same ordering_key is PROCESSING, skip the whole group.
# - Unordered webhooks are claimed freely.
# - Added acquisition timeout to prevent priority inversion
CLAIM_QUERY = text("""
WITH eligible AS (
  SELECT w.id
  FROM webhooks w
  WHERE w.status = 'pending'
    AND w.next_attempt_at <= NOW()
    AND (
      w.ordering_key IS NULL
      OR NOT EXISTS (
        SELECT 1 FROM webhooks w2
        WHERE w2.ordering_key = w.ordering_key
          AND w2.status = 'processing'
          AND w2.updated_at >= NOW() - INTERVAL '15 minutes'
      )
    )
  ORDER BY
    CASE WHEN w.ordering_key IS NOT NULL THEN 0 ELSE 1 END,
    w.created_at ASC
  LIMIT 1
  FOR UPDATE SKIP LOCKED
)
UPDATE webhooks
SET status = 'processing', updated_at = NOW()
WHERE id = (SELECT id FROM eligible)
RETURNING
  id, tenant_id, event_id, destination_url, destination_id,
  payload, headers, retry_count, max_retries, ordering_key
""")

# Set lock acquisition timeout at session level before claiming;
# prevents workers from blocking each other when contention is high.
SET_LOCK_TIMEOUT = text("SET LOCAL lock_timeout = '5000ms'")


class WebhookWorker:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.is_running = False
        self.task: Optional[asyncio.Task] = None

    def start(self):
        self.is_running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info("Worker started.", extra={"event": "worker.started", "worker_id": self.worker_id})

    async def stop(self):
        self.is_running = False
        if self.task:
            # Give the in-flight delivery up to 35s to finish naturally
            # (HTTP_CLIENT_TIMEOUT_SECONDS default is 10s + DB overhead).
            # Only force-cancel if it is stuck beyond that.
            try:
                await asyncio.wait_for(asyncio.shield(self.task), timeout=35.0)
            except asyncio.TimeoutError:
                logger.warning(
                    "Worker %d did not drain within 35s — force cancelling.",
                    self.worker_id,
                    extra={"event": "worker.stop_timeout", "worker_id": self.worker_id},
                )
                self.task.cancel()
                try:
                    await self.task
                except (asyncio.CancelledError, Exception):
                    pass
            except (asyncio.CancelledError, Exception):
                pass
        logger.info("Worker stopped.", extra={"event": "worker.stopped", "worker_id": self.worker_id})

    async def _run_loop(self):
        async with httpx.AsyncClient(
            timeout=settings.HTTP_CLIENT_TIMEOUT_SECONDS,
            follow_redirects=False,
        ) as client:
            while self.is_running:
                try:
                    processed = await self._process_next_job(client)
                    if not processed:
                        _wake_workers.clear()
                        try:
                            await asyncio.wait_for(
                                _wake_workers.wait(),
                                timeout=settings.WORKER_POLL_INTERVAL_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            pass
                except asyncio.CancelledError:
                    break
                except Exception:
                    logger.error("Worker loop error.", exc_info=True,
                                 extra={"event": "worker.loop_error", "worker_id": self.worker_id})
                    await asyncio.sleep(2)

    async def _process_next_job(self, client: httpx.AsyncClient) -> bool:
        async with async_session() as session:
            await session.execute(SET_LOCK_TIMEOUT)
            result = await session.execute(CLAIM_QUERY)
            row = result.fetchone()
            if not row:
                return False

            (
                webhook_id, tenant_id, event_id, destination_url, destination_id,
                payload, headers, retry_count, max_retries, ordering_key,
            ) = row

            logger.info("Worker claimed webhook.", extra={
                "event": "webhook.claimed",
                "worker_id": self.worker_id,
                "webhook_id": str(webhook_id),
                "tenant_id": tenant_id,
                "event_id": event_id,
                "destination_url": destination_url,
                "ordering_key": ordering_key,
                "retry_count": retry_count,
            })

            # Check circuit breaker if destination is registered
            if destination_id:
                try:
                    open_circuit = not await should_deliver(session, UUID(str(destination_id)))
                    if open_circuit:
                        logger.warning("Circuit OPEN, skipping delivery.", extra={
                            "event": "webhook.circuit_open_skip",
                            "webhook_id": str(webhook_id),
                            "destination_id": str(destination_id),
                        })
                        # Revert to pending and back-off until the circuit's cooldown elapses,
                        # preventing a tight re-claim loop while the circuit is open.
                        from datetime import timedelta
                        circuit_cooldown = datetime.now(timezone.utc) + timedelta(seconds=HALF_OPEN_TIMEOUT * 60)
                        await session.execute(
                            text("UPDATE webhooks SET status='pending', next_attempt_at=:na, updated_at=NOW() WHERE id=:id"),
                            {"na": circuit_cooldown, "id": webhook_id},
                        )
                        await session.commit()
                        return True
                except Exception as exc:
                    logger.warning("Circuit breaker check error: %s", exc)

            # Load destination config for webhook_secret, custom_headers, base_seconds
            dest_config: Optional[dict] = None
            dest_obj = None
            base_seconds = settings.BACKOFF_BASE_SECONDS
            if destination_id:
                dest_result = await session.execute(
                    select(Destination).where(Destination.id == UUID(str(destination_id)))
                )
                dest_obj = dest_result.scalar_one_or_none()
                if dest_obj:
                    dest_config = {
                        "webhook_secret": dest_obj.webhook_secret,
                        "custom_headers": dest_obj.custom_headers or {},
                        "backoff_base_seconds": dest_obj.backoff_base_seconds,
                        "max_retries": dest_obj.max_retries,
                    }
                    base_seconds = dest_obj.backoff_base_seconds
                    max_retries = dest_obj.max_retries

            is_sandbox = bool(dest_obj and dest_obj.is_sandbox)
            await self._deliver_webhook(
                session, client,
                webhook_id, tenant_id, event_id,
                destination_url, destination_id,
                payload, headers,
                retry_count, max_retries,
                base_seconds, dest_config,
                is_sandbox=is_sandbox,
            )
            return True

    async def _deliver_webhook(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient,
        webhook_id,
        tenant_id: str,
        event_id: str,
        destination_url: str,
        destination_id,
        payload,
        headers,
        retry_count: int,
        max_retries: int,
        base_seconds: int,
        dest_config: Optional[dict],
        is_sandbox: bool = False,
    ):
        attempt_number = retry_count + 1
        start_time = time.perf_counter()
        now = datetime.now(timezone.utc)

        status_code = None
        response_body = None
        response_headers_dict: dict = {}
        error_message = None
        error_type = None

        # Build delivery headers
        delivery_headers = {k: v for k, v in (headers or {}).items()}
        if dest_config and dest_config.get("custom_headers"):
            delivery_headers.update(dest_config["custom_headers"])

        # Relora tracing headers
        delivery_headers["X-Relora-Delivery-Id"] = _uuid_str(webhook_id)
        delivery_headers["X-Relora-Event-Id"] = event_id
        delivery_headers["X-Relora-Attempt"] = str(attempt_number)

        # Standard Webhooks signing
        webhook_secret = (dest_config or {}).get("webhook_secret") or settings.STANDARD_WEBHOOKS_SECRET
        if webhook_secret:
            payload_str = json.dumps(payload)
            signing_headers = sign_outbound_webhook(
                webhook_id=_uuid_str(webhook_id),
                payload=payload_str,
                secret=webhook_secret,
            )
            delivery_headers.update(signing_headers)

        try:
            response = await client.post(
                destination_url,
                json=payload,
                headers=delivery_headers,
            )
            status_code = response.status_code
            response_body = response.text[:2000]
            response_headers_dict = dict(response.headers)
            success = 200 <= status_code < 300
            if not success:
                error_message = f"HTTP {status_code}: {response_body[:200]}"
        except httpx.TimeoutException as exc:
            success = False
            error_type = "TimeoutError"
            error_message = f"Timeout: {exc}"
        except httpx.NetworkError as exc:
            success = False
            error_type = "NetworkError"
            error_message = f"Network error: {exc}"
        except Exception as exc:
            success = False
            error_type = type(exc).__name__
            error_message = f"Unexpected: {exc}"

        duration_ms = int((time.perf_counter() - start_time) * 1000)

        # Record the attempt
        next_retry_at, strategy = compute_next_attempt(
            attempt_number=attempt_number,
            http_status=status_code,
            response_headers=response_headers_dict,
            error_type=error_type,
            base_seconds=base_seconds,
        )

        # Classify the failure
        failure_category, failure_subcategory, failure_severity, failure_recoverability, error_signature = FailureClassifier.classify(
            status_code=status_code,
            error_message=error_message,
            response_body=response_body,
        )

        # Compress response body if it's large before storing
        compressed_response_body = None
        if response_body and len(response_body) > 1024:  # Only compress if > 1KB
            try:
                compressed_response_body = base64.b64encode(
                    zlib.compress(response_body.encode('utf-8'), level=6)
                ).decode('ascii')
            except Exception as e:
                logger.warning("Failed to compress response body: %s", e)

        attempt = DeliveryAttempt(
            webhook_id=webhook_id,
            attempt_number=attempt_number,
            status_code=status_code,
            response_body=compressed_response_body if compressed_response_body else response_body,
            response_headers=response_headers_dict if response_headers_dict else None,
            duration_ms=duration_ms,
            error_message=error_message,
            retry_strategy_used=strategy.value,
            attempted_at=now,
            failure_category=failure_category,
            failure_subcategory=failure_subcategory,
            failure_severity=failure_severity,
            failure_recoverability=failure_recoverability,
            error_signature=error_signature,
            response_body_compressed=compressed_response_body is not None,
        )
        session.add(attempt)

        new_retry_count = retry_count + 1
        can_retry = False  # overridden in failure branch

        if success:
            logger.info("Webhook delivered.", extra={
                "event": "webhook.delivery.succeeded",
                "worker_id": self.worker_id,
                "webhook_id": _uuid_str(webhook_id),
                "tenant_id": tenant_id,
                "event_id": event_id,
                "destination_url": destination_url,
                "attempt_number": attempt_number,
                "response_status": status_code,
                "duration_ms": duration_ms,
            })
            await session.execute(
                text("UPDATE webhooks SET status='completed', last_attempt_at=:t, updated_at=:t WHERE id=:id"),
                {"t": now, "id": webhook_id},
            )
            record_delivered(tenant_id, destination_url, duration_ms)
            if destination_id:
                try:
                    await record_outcome(session, UUID(str(destination_id)), success=True)
                except Exception:
                    pass
        else:
            record_delivery_failed(tenant_id, destination_url, duration_ms)
            can_retry = (
                new_retry_count < max_retries
                and strategy != RetryStrategy.NO_RETRY
            )

            logger.warning("Webhook delivery failed.", extra={
                "event": "webhook.delivery.failed",
                "worker_id": self.worker_id,
                "webhook_id": _uuid_str(webhook_id),
                "tenant_id": tenant_id,
                "event_id": event_id,
                "destination_url": destination_url,
                "attempt_number": attempt_number,
                "response_status": status_code,
                "duration_ms": duration_ms,
                "error_message": error_message,
                "strategy": strategy.value,
                "can_retry": can_retry,
            })

            if destination_id:
                try:
                    await record_outcome(session, UUID(str(destination_id)), success=False)
                except Exception:
                    pass

            if can_retry:
                logger.info("Scheduled retry.", extra={
                    "event": "webhook.retry.scheduled",
                    "webhook_id": _uuid_str(webhook_id),
                    "retry_count": new_retry_count,
                    "next_attempt_at": next_retry_at.isoformat(),
                    "strategy": strategy.value,
                })
                await session.execute(
                    text("""
                    UPDATE webhooks
                    SET status='pending', retry_count=:rc, next_attempt_at=:na,
                        last_attempt_at=:t, updated_at=:t
                    WHERE id=:id
                    """),
                    {"rc": new_retry_count, "na": next_retry_at, "t": now, "id": webhook_id},
                )
            else:
                logger.error("Webhook moved to DLQ.", extra={
                    "event": "webhook.dlq.created",
                    "webhook_id": _uuid_str(webhook_id),
                    "tenant_id": tenant_id,
                    "event_id": event_id,
                    "destination_url": destination_url,
                    "retry_count": new_retry_count,
                })
                await session.execute(
                    text("UPDATE webhooks SET status='failed', retry_count=:rc, last_attempt_at=:t, updated_at=:t WHERE id=:id"),
                    {"rc": new_retry_count, "t": now, "id": webhook_id},
                )
                
                # Create or update incident for this failure
                try:
                    # Get project_id for the webhook
                    project_result = await session.execute(
                        select(Destination.project_id).where(Destination.id == destination_id)
                    )
                    project_id = project_result.scalar_one_or_none()
                    
                    if project_id:
                        await IncidentEngine.get_or_create_incident(
                            db=session,
                            project_id=str(project_id),
                            destination_id=destination_id,
                            error_signature=error_signature,
                            failure_category=failure_category,
                            failure_subcategory=failure_subcategory,
                        )
                except Exception as ie:
                    logger.error("Incident creation failed: %s", ie)
                
                record_dlq(tenant_id)
                try:
                    await dispatch_dlq_alert(
                        session=session,
                        tenant_id=tenant_id,
                        webhook_id=_uuid_str(webhook_id),
                        event_id=event_id,
                        destination_url=destination_url,
                        retry_count=new_retry_count,
                        last_error=error_message,
                    )
                except Exception as ae:
                    logger.error("DLQ alert dispatch failed: %s", ae)

        await session.commit()

        # Broadcast to connected dashboard clients
        try:
            if success:
                _final_status = "completed"
            elif can_retry:
                _final_status = "pending"
            else:
                _final_status = "failed"

            webhook_data = {
                "id": _uuid_str(webhook_id),
                "status": _final_status,
                "retry_count": retry_count + 1,
                "destination_url": destination_url,
                "duration_ms": duration_ms,
                "attempt_number": attempt_number,
                "is_sandbox": is_sandbox,
            }
            
            # Broadcast via WebSocket
            await ws_manager.broadcast(
                project_key=tenant_id,
                event_type="webhook.updated",
                data=webhook_data,
            )
            
            # Broadcast via SSE
            await sse_hub.broadcast_webhook_update(tenant_id, webhook_data)
            
            # Also broadcast delivery attempt details
            attempt_data = {
                "webhook_id": _uuid_str(webhook_id),
                "attempt_number": attempt_number,
                "status_code": status_code,
                "duration_ms": duration_ms,
                "success": success,
                "error_message": error_message,
            }
            await sse_hub.broadcast_delivery_attempt(tenant_id, attempt_data)
        except Exception:
            pass


def _uuid_str(val) -> str:
    if hasattr(val, "hex"):
        return str(val)
    return str(val)


class WorkerPool:
    def __init__(self, concurrency: int = settings.WORKER_CONCURRENCY):
        self.concurrency = concurrency
        self.workers = [WebhookWorker(i) for i in range(concurrency)]

    def start(self):
        logger.info("Starting worker pool.", extra={
            "event": "worker_pool.starting",
            "concurrency": self.concurrency,
        })
        for worker in self.workers:
            worker.start()

    async def stop(self):
        logger.info("Stopping worker pool.", extra={"event": "worker_pool.stopping"})
        await asyncio.gather(*(w.stop() for w in self.workers), return_exceptions=True)
        logger.info("Worker pool stopped.", extra={"event": "worker_pool.stopped"})
