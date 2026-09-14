from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# Primary async engine for application read-write operations
engine = create_async_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    echo=False,
)

# Session factory for primary database
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Read-only async engine for AI agent queries
engine_ro = create_async_engine(
    settings.DATABASE_URL_RO,
    pool_pre_ping=True,
    echo=False,
)

# Session factory for read-only agent queries
AsyncSessionLocalRO = async_sessionmaker(
    bind=engine_ro,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_db_ro() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocalRO() as session:
        try:
            yield session
        finally:
            await session.close()


from sqlalchemy.exc import SQLAlchemyError


async def check_db_health() -> bool:
    """Execute a lightweight SELECT 1 to verify database connectivity."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except (SQLAlchemyError, OSError):
        return False
