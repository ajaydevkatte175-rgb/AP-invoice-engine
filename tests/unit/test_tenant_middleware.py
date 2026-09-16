import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.middleware import DEFAULT_TENANT_ID
from app.main import app


@pytest.mark.asyncio
async def test_tenant_middleware_default_fallback():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("X-Tenant-ID") == str(DEFAULT_TENANT_ID)


@pytest.mark.asyncio
async def test_tenant_middleware_custom_tenant():
    custom_tenant = uuid.uuid4()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": str(custom_tenant)},
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.headers.get("X-Tenant-ID") == str(custom_tenant)


@pytest.mark.asyncio
async def test_tenant_middleware_invalid_uuid():
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"X-Tenant-ID": "invalid-uuid-string"},
    ) as client:
        response = await client.get("/health")
        assert response.status_code == 400
        assert "Invalid X-Tenant-ID header" in response.json()["detail"]
