"""Checking the Supabase login token on each request.

Supabase signs tokens with a private key (ES256). We verify them with the matching public
keys, published at /auth/v1/.well-known/jwks.json, so this server never holds a secret
that could mint tokens. The role comes from the user_role claim added by the token hook.
"""

from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class CurrentUser:
    id: UUID
    role: str


@lru_cache
def _jwks_client() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json")


def current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not logged in")
    token = credentials.credentials
    try:
        key = _jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key, algorithms=["ES256", "RS256"], audience="authenticated")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired login")

    role = claims.get("user_role")
    if role is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This login has no role")
    return CurrentUser(id=UUID(claims["sub"]), role=role)


def require_teacher(user: Annotated[CurrentUser, Depends(current_user)]) -> CurrentUser:
    if user.role != "teacher":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Teachers only")
    return user
