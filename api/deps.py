"""FastAPI dependency injection: session, auth, current user."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, UTC

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from mimir.config import get_settings
from storage.database import get_session  # re-export for routes

DEV_USER_ID = "dev"
DEV_USER_EMAIL = "dev@local"
DEV_USER_NAME = "Dev User"


@dataclass
class UserContext:
    id: str
    email: str
    display_name: str
    is_dev: bool = False


_DEV_USER = UserContext(id=DEV_USER_ID, email=DEV_USER_EMAIL, display_name=DEV_USER_NAME, is_dev=True)


async def get_current_user(
    authorization: str = Header(default=""),
    x_api_key: str = Header(default=""),
    session: AsyncSession = Depends(get_session),
) -> UserContext:
    """Resolve the current user from Authorization Bearer or X-API-Key header.

    Accepts (in order):
      1. Dev mode → synthetic dev user, no key needed
      2. Authorization: Bearer <oauth_token> → OAuth access token lookup
      3. Authorization: Bearer <api_key> or X-API-Key: <key> → API key lookup
      4. Legacy MIMIR_API_KEY fast path
    """
    settings = get_settings()
    if settings.is_dev_auth:
        return _DEV_USER

    # Extract the raw credential from Authorization or X-API-Key
    bearer_value = ""
    if authorization.startswith("Bearer "):
        bearer_value = authorization[7:].strip()

    key_to_check = bearer_value or x_api_key.strip()

    if not key_to_check:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API key required")

    # Try OAuth access token first (token_hash lookup)
    from api.routes.oauth import resolve_oauth_token
    oauth_user_id = await resolve_oauth_token(key_to_check)
    if oauth_user_id:
        user = await session.get(__import__("storage.models", fromlist=["User"]).User, oauth_user_id)
        if not user or not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")
        return UserContext(id=user.id, email=user.email, display_name=user.display_name, is_dev=False)

    # multi_user mode: reject the default dev key (local-dev-key)
    if settings.is_multi_user and key_to_check == settings.dev_api_key and settings.dev_api_key in ("local-dev-key",):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Dev key not accepted in multi_user mode")

    # Legacy single-key auth: if key matches configured MIMIR_API_KEY, bypass DB lookup
    if key_to_check == settings.api_key:
        return UserContext(id="admin", email="admin@local", display_name="Admin", is_dev=False)

    from storage.models import APIKey
    key_hash = hashlib.sha256(key_to_check.encode()).hexdigest()
    result = await session.execute(
        select(APIKey)
        .where(APIKey.key_hash == key_hash, APIKey.is_active == True)  # noqa: E712
        .options(selectinload(APIKey.user))
    )
    api_key_row = result.scalar_one_or_none()
    if not api_key_row:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    if not api_key_row.user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User account is inactive")

    # Update last_used timestamp (fire-and-forget; don't fail the request if it errors)
    try:
        api_key_row.last_used_at = datetime.now(UTC)
        await session.commit()
    except Exception:
        pass

    return UserContext(
        id=api_key_row.user.id,
        email=api_key_row.user.email,
        display_name=api_key_row.user.display_name,
        is_dev=False,
    )


async def require_api_key(x_api_key: str = Header(default="")) -> str:
    """Legacy dependency kept for backward compat (Slack route etc.)."""
    settings = get_settings()
    if settings.is_dev_auth:
        return x_api_key
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return x_api_key
