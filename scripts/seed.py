"""Database seed script stub."""

import asyncio
from app.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def main():
    setup_logging()
    logger.info("Seed script placeholder - ready for seed data in upcoming phases")


if __name__ == "__main__":
    asyncio.run(main())

