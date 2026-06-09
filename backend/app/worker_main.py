"""
Standalone worker process entrypoint.
Run with: python -m app.worker_main
Runs the WorkerPool forever — no HTTP server, no API.
"""
import asyncio
import logging
import signal

from app.config import settings
from app.db import init_db
from app.logging_config import configure_logging
from app.worker import WorkerPool, _wake_workers

configure_logging()
logger = logging.getLogger("relora.worker_main")


async def _recover_stuck_webhooks() -> None:
    """
    Reset webhooks stuck in 'processing' to 'pending'.
    Covers workers that crashed mid-delivery and consumer-mode webhooks whose
    client never sent an ack. poll_ack_token is cleared so a stale ack cannot
    accidentally complete a re-queued webhook.
    """
    from app.db import async_session as _s
    from sqlalchemy import text as _text
    async with _s() as db:
        result = await db.execute(
            _text("""
            UPDATE webhooks
            SET status = 'pending',
                poll_ack_token = NULL,
                updated_at = NOW()
            WHERE status = 'processing'
              AND updated_at < NOW() - INTERVAL '10 minutes'
            """)
        )
        await db.commit()
        if result.rowcount:
            logger.warning(
                "Recovered %d stuck webhooks (processing → pending)",
                result.rowcount,
                extra={"event": "webhook.stuck.recovered", "count": result.rowcount},
            )


async def _periodic_recovery_loop() -> None:
    """Run stuck-webhook recovery every 5 minutes while the worker is alive."""
    while True:
        await asyncio.sleep(300)
        try:
            await _recover_stuck_webhooks()
        except Exception as exc:
            logger.warning("Periodic stuck-webhook recovery failed: %s", exc)


async def _prune_expired_tokens() -> None:
    """
    Delete expired or used email-verification and password-reset tokens.
    Rows accumulate indefinitely without this; prune anything older than 24 h
    past its expiry so investigation is still possible for recent failures.
    """
    from app.db import async_session as _s
    from sqlalchemy import text as _text
    async with _s() as db:
        ev = await db.execute(
            _text("""
            DELETE FROM email_verification_tokens
            WHERE expires_at < NOW() - INTERVAL '24 hours'
               OR used_at IS NOT NULL
            """)
        )
        pr = await db.execute(
            _text("""
            DELETE FROM password_reset_tokens
            WHERE expires_at < NOW() - INTERVAL '24 hours'
               OR used_at IS NOT NULL
            """)
        )
        await db.commit()
        total = (ev.rowcount or 0) + (pr.rowcount or 0)
        if total:
            logger.info(
                "Pruned %d expired auth tokens (ev=%d pr=%d)",
                total, ev.rowcount or 0, pr.rowcount or 0,
                extra={"event": "auth_tokens.pruned", "count": total},
            )


async def _periodic_token_prune_loop() -> None:
    """Prune expired auth tokens once every 6 hours."""
    while True:
        await asyncio.sleep(6 * 3600)
        try:
            await _prune_expired_tokens()
        except Exception as exc:
            logger.warning("Periodic token pruning failed: %s", exc)


async def _listen_for_new_webhooks() -> None:
    """Dedicated asyncpg LISTEN connection. Sets _wake_workers on every NOTIFY new_webhook."""
    import asyncpg

    dsn = settings.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

    def _on_notify(conn, pid, channel, payload):
        _wake_workers.set()

    while True:
        try:
            conn = await asyncpg.connect(dsn)
            try:
                await conn.add_listener("new_webhook", _on_notify)
                logger.info("LISTEN new_webhook active.", extra={"event": "worker_main.listen_active"})
                while True:
                    await asyncio.sleep(3600)
            finally:
                try:
                    await conn.remove_listener("new_webhook", _on_notify)
                except Exception:
                    pass
                await conn.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.warning(
                "LISTEN connection lost: %s — reconnecting in 5s", exc,
                extra={"event": "worker_main.listen_reconnect"},
            )
            await asyncio.sleep(5)


async def main():
    if settings.AUTO_CREATE_TABLES:
        logger.info("Initializing database tables...")
        await init_db()

    await _recover_stuck_webhooks()

    await _prune_expired_tokens()

    pool = WorkerPool(concurrency=settings.WORKER_CONCURRENCY)
    pool.start()
    recovery_task = asyncio.create_task(_periodic_recovery_loop())
    prune_task = asyncio.create_task(_periodic_token_prune_loop())
    listen_task = asyncio.create_task(_listen_for_new_webhooks())
    logger.info("Worker pool running.", extra={
        "event": "worker_main.started",
        "concurrency": settings.WORKER_CONCURRENCY,
    })

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _shutdown(sig_num: int) -> None:
        logger.info(
            "Shutdown signal received (%s), draining workers…",
            signal.Signals(sig_num).name,
            extra={"event": "worker_main.shutdown_requested"},
        )
        stop_event.set()

    # loop.add_signal_handler integrates correctly with asyncio on Linux/macOS.
    # Fallback to signal.signal for Windows local-dev only.
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown, sig)
        except (NotImplementedError, OSError):
            signal.signal(sig, lambda s, f: _shutdown(s))

    await stop_event.wait()
    recovery_task.cancel()
    prune_task.cancel()
    listen_task.cancel()
    for t in (recovery_task, prune_task, listen_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    logger.info("Draining in-flight deliveries (up to 35s per worker)…")
    await pool.stop()
    logger.info("Worker process exited cleanly.", extra={"event": "worker_main.stopped"})


if __name__ == "__main__":
    asyncio.run(main())
