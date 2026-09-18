from typing import ClassVar
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import settings
from app.core.logging import get_logger
from app.workers.tasks import process_document_pipeline

logger = get_logger(__name__)

_redis_pool: ArqRedis | None = None


async def get_arq_redis() -> ArqRedis:
    """Retrieve or create the shared ARQ Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
    return _redis_pool


async def close_arq_redis() -> None:
    """Close the ARQ Redis connection pool on application shutdown."""
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.close()
        _redis_pool = None


async def startup(ctx: dict) -> None:
    logger.info("Worker startup initiated")


async def shutdown(ctx: dict) -> None:
    logger.info("Worker shutdown completed")


class WorkerSettings:
    """ARQ worker configuration class."""

    functions: ClassVar[list] = [process_document_pipeline]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
