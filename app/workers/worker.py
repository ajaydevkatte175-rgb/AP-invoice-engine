from arq.connections import RedisSettings

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


async def startup(ctx: dict) -> None:
    logger.info("Worker startup initiated")


async def shutdown(ctx: dict) -> None:
    logger.info("Worker shutdown completed")


from typing import ClassVar


class WorkerSettings:
    functions: ClassVar[list] = []
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
