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
logger = logging.getLogger("hermes.worker_main")


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

    def _shutdown(sig, frame):
        logger.info("Shutdown signal received (%s), stopping workers...", sig)
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    await stop_event.wait()
    await pool.stop()
    logger.info("Worker process exited cleanly.")


if __name__ == "__main__":
    asyncio.run(main())
