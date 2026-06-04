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
from app.worker import WorkerPool

configure_logging()
logger = logging.getLogger("relora.worker_main")


async def main():
    if settings.AUTO_CREATE_TABLES:
        logger.info("Initializing database tables...")
        await init_db()

    pool = WorkerPool(concurrency=settings.WORKER_CONCURRENCY)
    pool.start()
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
    logger.info("Draining in-flight deliveries (up to 35s per worker)…")
    await pool.stop()
    logger.info("Worker process exited cleanly.", extra={"event": "worker_main.stopped"})


if __name__ == "__main__":
    asyncio.run(main())
