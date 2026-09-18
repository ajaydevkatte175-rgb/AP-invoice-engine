import pytest

from app.core.db import engine, engine_ro


@pytest.fixture(scope="session", autouse=True)
async def cleanup_db_connections():
    yield
    await engine.dispose()
    await engine_ro.dispose()
