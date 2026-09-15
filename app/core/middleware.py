import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from fastapi import Header, HTTPException, status

# Default tenant UUID for local development or unauthenticated testing
DEFAULT_TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")

# Context variable holding the current tenant UUID for the active async task/request
current_tenant_var: ContextVar[uuid.UUID | None] = ContextVar("current_tenant_var", default=None)


def get_current_tenant_id() -> uuid.UUID:
    """Retrieve the current tenant ID from context, falling back to default in dev."""
    tenant_id = current_tenant_var.get()
    if tenant_id is None:
        return DEFAULT_TENANT_ID
    return tenant_id


async def tenant_dependency(
    x_tenant_id: str | None = Header(default=None, alias="X-Tenant-ID"),
) -> uuid.UUID:
    """FastAPI dependency to extract and validate the X-Tenant-ID header."""
    if x_tenant_id:
        try:
            tenant_uuid = uuid.UUID(x_tenant_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid X-Tenant-ID header: must be a valid UUID",
            )
        current_tenant_var.set(tenant_uuid)
        return tenant_uuid

    # Fallback to current context or default
    return get_current_tenant_id()


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware that intercepts requests to extract X-Tenant-ID and populate context."""

    async def dispatch(self, request: Request, call_next) -> Response:
        raw_tenant = request.headers.get("X-Tenant-ID")
        tenant_uuid: uuid.UUID = DEFAULT_TENANT_ID

        if raw_tenant:
            try:
                tenant_uuid = uuid.UUID(raw_tenant)
            except ValueError:
                # If invalid UUID format is provided in header
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid X-Tenant-ID header: must be a valid UUID",
                )

        token = current_tenant_var.set(tenant_uuid)
        request.state.tenant_id = tenant_uuid

        try:
            response = await call_next(request)
            response.headers["X-Tenant-ID"] = str(tenant_uuid)
            return response
        finally:
            current_tenant_var.reset(token)

