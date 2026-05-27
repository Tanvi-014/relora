from typing import AsyncGenerator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import settings
from app.models import Base

# Setup async database engine
# echo=True is helpful for logging SQL during development, but settings.DEBUG controls it
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    pool_pre_ping=True
)

# Async session factory
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that yields an async database session
    and handles transaction rollback on exception or automatic closing.
    """
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

async def init_db() -> None:
    """
    Asynchronously creates all tables defined in models.py
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("ALTER TABLE webhooks ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR"))
        await conn.execute(text("""
            CREATE UNIQUE INDEX IF NOT EXISTS ix_webhooks_destination_idempotency_key
            ON webhooks (destination_url, idempotency_key)
            WHERE idempotency_key IS NOT NULL
        """))
