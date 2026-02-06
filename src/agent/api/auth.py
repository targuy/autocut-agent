"""Authentication — JWT tokens and API key support."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, HTTPException, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

# Defaults — override via config
SECRET_KEY = "change-me"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def create_access_token(
    data: dict[str, Any],
    secret_key: str = SECRET_KEY,
    expires_delta: timedelta | None = None,
) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    to_encode["exp"] = expire
    return jwt.encode(to_encode, secret_key, algorithm=ALGORITHM)


def verify_token(
    token: str,
    secret_key: str = SECRET_KEY,
) -> dict[str, Any]:
    """Verify and decode a JWT token."""
    try:
        payload = jwt.decode(token, secret_key, algorithms=[ALGORITHM])
        return payload
    except JWTError as e:
        raise HTTPException(status_code=401, detail="Invalid token") from e


async def get_current_user(
    bearer: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
    api_key: str | None = Security(api_key_header),
) -> dict[str, Any]:
    """Dependency that extracts the current user from JWT or API key.

    For now, this is a permissive implementation that allows
    unauthenticated access when no credentials are provided.
    Tighten this when auth is fully configured.
    """
    if bearer and bearer.credentials:
        return verify_token(bearer.credentials)

    if api_key:
        # API key validation would go here (check against DB/config)
        return {"sub": "api_key_user", "api_key": api_key}

    # Permissive: allow unauthenticated access during development
    return {"sub": "anonymous"}
