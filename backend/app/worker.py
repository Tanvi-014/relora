import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import async_session
from app.models import Webhook, WebhookStatus, DeliveryAttempt

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("hermes.worker")

class WebhookWorker:
    def __init__(self, worker_id: int):
        self.worker_id = worker_id
        self.is_running = False
        self.task: Optional[asyncio.Task] = None

    def start(self):
        self.is_running = True
        self.task = asyncio.create_task(self._run_loop())
        logger.info(f"Worker {self.worker_id} started.")

    async def stop(self):
        self.is_running = False
        if self.task:
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info(f"Worker {self.worker_id} stopped.")

    async def _run_loop(self):
        async with httpx.AsyncClient(timeout=settings.HTTP_CLIENT_TIMEOUT_SECONDS) as client:
            while self.is_running:
                try:
                    job_processed = await self._process_next_job(client)
                    if not job_processed:
                        # No pending jobs, wait for the poll interval
                        await asyncio.sleep(settings.WORKER_POLL_INTERVAL_SECONDS)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Worker {self.worker_id} encountered an error: {e}", exc_info=True)
                    await asyncio.sleep(2)

    async def _process_next_job(self, client: httpx.AsyncClient) -> bool:
        """
        Locks and retrieves the next available pending webhook job using SELECT FOR UPDATE SKIP LOCKED.
        Returns True if a job was found and processed, False otherwise.
        """
        async with async_session() as session:
            # Atomic SELECT FOR UPDATE SKIP LOCKED
            query = text("""
                UPDATE webhooks
                SET status = 'processing', updated_at = NOW()
                WHERE id = (
                    SELECT id
                    FROM webhooks
                    WHERE status = 'pending' AND next_attempt_at <= NOW()
                    ORDER BY next_attempt_at ASC
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                RETURNING id, destination_url, payload, headers, retry_count, max_retries;
            """)
            
            result = await session.execute(query)
            row = result.fetchone()
            
            if not row:
                return False

            webhook_id, destination_url, payload, headers, retry_count, max_retries = row
            
            logger.info(f"Worker {self.worker_id} claimed webhook {webhook_id} targeting {destination_url}")
            
            # Perform delivery in an isolated try-except block so failure doesn't crash the worker
            await self._deliver_webhook(session, client, webhook_id, destination_url, payload, headers, retry_count, max_retries)
            return True

    async def _deliver_webhook(
        self, 
        session: AsyncSession, 
        client: httpx.AsyncClient, 
        webhook_id, 
        destination_url: str, 
        payload, 
        headers, 
        retry_count: int, 
        max_retries: int
    ):
        attempt_number = retry_count + 1
        start_time = time.perf_counter()
        
        status_code = None
        response_body = None
        error_message = None
        
        # Inject Hermes headers for tracing and idempotency
        delivery_headers = {**headers}
        delivery_headers["X-Hermes-Delivery-Id"] = str(uuid_to_str(webhook_id))
        delivery_headers["X-Hermes-Attempt"] = str(attempt_number)
        
        try:
            response = await client.post(
                destination_url,
                json=payload,
                headers=delivery_headers
            )
            status_code = response.status_code
            response_body = response.text[:2000] # Truncate large error pages
            
            if 200 <= response.status_code < 300:
                success = True
            else:
                success = False
                error_message = f"HTTP Error Status {response.status_code}"
        except httpx.TimeoutException:
            success = False
            error_message = "Connection timeout"
        except httpx.NetworkError as ne:
            success = False
            error_message = f"Network connection failed: {ne}"
        except Exception as e:
            success = False
            error_message = f"Unexpected error during delivery: {e}"

        duration_ms = int((time.perf_counter() - start_time) * 1000)
        
        # Create attempt record
        attempt = DeliveryAttempt(
            webhook_id=webhook_id,
            attempt_number=attempt_number,
            status_code=status_code,
            response_body=response_body,
            duration_ms=duration_ms,
            error_message=error_message,
            attempted_at=datetime.now(timezone.utc)
        )
        session.add(attempt)

        now = datetime.now(timezone.utc)
        
        if success:
            logger.info(f"Successfully delivered webhook {webhook_id} on attempt {attempt_number}")
            # Complete webhook
            await session.execute(
                text("""
                    UPDATE webhooks
                    SET status = 'completed', last_attempt_at = :last_attempt, updated_at = :now
                    WHERE id = :id
                """),
                {"last_attempt": now, "now": now, "id": webhook_id}
            )
        else:
            new_retry_count = retry_count + 1
            logger.warning(f"Failed to deliver webhook {webhook_id} (Attempt {attempt_number}). Error: {error_message}")
            
            if new_retry_count < max_retries:
                # Calculate exponential backoff
                backoff_seconds = settings.BACKOFF_BASE_SECONDS * (2 ** retry_count)
                next_attempt = now + timedelta(seconds=backoff_seconds)
                logger.info(f"Re-queueing webhook {webhook_id} for retry at {next_attempt} (in {backoff_seconds}s)")
                
                await session.execute(
                    text("""
                        UPDATE webhooks
                        SET status = 'pending', retry_count = :retry_count, next_attempt_at = :next_attempt, last_attempt_at = :last_attempt, updated_at = :now
                        WHERE id = :id
                    """),
                    {
                        "retry_count": new_retry_count,
                        "next_attempt": next_attempt,
                        "last_attempt": now,
                        "now": now,
                        "id": webhook_id
                    }
                )
            else:
                # Mark as permanently failed (Dead Letter Queue)
                logger.error(f"Webhook {webhook_id} exceeded max retries. Moving to Dead Letter Queue.")
                await session.execute(
                    text("""
                        UPDATE webhooks
                        SET status = 'failed', retry_count = :retry_count, last_attempt_at = :last_attempt, updated_at = :now
                        WHERE id = :id
                    """),
                    {
                        "retry_count": new_retry_count,
                        "last_attempt": now,
                        "now": now,
                        "id": webhook_id
                    }
                )
        
        await session.commit()

def uuid_to_str(val):
    if hasattr(val, "hex"):
        return val.hex
    return str(val)


class WorkerPool:
    def __init__(self, concurrency: int = settings.WORKER_CONCURRENCY):
        self.concurrency = concurrency
        self.workers = [WebhookWorker(i) for i in range(concurrency)]

    def start(self):
        logger.info(f"Starting worker pool with {self.concurrency} concurrent workers...")
        for worker in self.workers:
            worker.start()

    async def stop(self):
        logger.info("Stopping worker pool...")
        await asyncio.gather(*(worker.stop() for worker in self.workers))
        logger.info("Worker pool stopped successfully.")
