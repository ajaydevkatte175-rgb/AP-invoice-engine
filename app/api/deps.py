import secrets

from fastapi import Header, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

security_bearer = HTTPBearer(auto_error=False)


async def verify_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    bearer_auth: HTTPAuthorizationCredentials | None = Security(security_bearer),
) -> str:
    """Verify incoming request contains a valid API key via X-API-Key header or Bearer token.

    Uses constant-time comparison (secrets.compare_digest) to prevent timing attacks.
    """
    provided_key: str | None = None
    if x_api_key:
        provided_key = x_api_key
    elif bearer_auth and bearer_auth.credentials:
        provided_key = bearer_auth.credentials

    if not provided_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing API key. Provide via 'X-API-Key' header or Bearer token.",
        )

    expected_key = settings.API_KEY
    if not secrets.compare_digest(provided_key, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )

    return provided_key

