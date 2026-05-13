"""Auth endpoints: user registration, login, API key management."""

from __future__ import annotations

import hashlib
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import UserContext, get_current_user
from storage.database import get_session
from storage.models import APIKey, User

router = APIRouter(prefix="/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: str
    display_name: str
    key_name: str = "default"


class LoginIn(BaseModel):
    email: str
    api_key: str


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class APIKeyOut(BaseModel):
    id: str
    name: str | None
    created_at: str
    last_used_at: str | None

    model_config = {"from_attributes": True}


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterIn, session: AsyncSession = Depends(get_session)):
    """Create a new user and return a raw API key (shown only once)."""
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered")

    user = User(
        id=uuid.uuid4().hex,
        email=body.email,
        display_name=body.display_name,
    )
    session.add(user)

    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = APIKey(
        id=uuid.uuid4().hex,
        user_id=user.id,
        key_hash=key_hash,
        name=body.key_name,
    )
    session.add(api_key)
    await session.commit()

    return {
        "user": UserOut.model_validate(user),
        "api_key": raw_key,
        "note": "Store this key securely — it will not be shown again.",
    }


@router.post("/keys")
async def create_key(
    name: str = "new-key",
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Create an additional API key for the current user."""
    if current_user.is_dev:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Cannot create keys in dev mode")

    raw_key = secrets.token_urlsafe(32)
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    api_key = APIKey(
        id=uuid.uuid4().hex,
        user_id=current_user.id,
        key_hash=key_hash,
        name=name,
    )
    session.add(api_key)
    await session.commit()
    return {"api_key": raw_key, "id": api_key.id}


@router.get("/keys")
async def list_keys(
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """List API keys for the current user (hashes not returned)."""
    if current_user.is_dev:
        return {"keys": []}
    result = await session.execute(
        select(APIKey).where(APIKey.user_id == current_user.id, APIKey.is_active == True)  # noqa: E712
    )
    keys = result.scalars().all()
    return {
        "keys": [
            {
                "id": k.id,
                "name": k.name,
                "created_at": k.created_at.isoformat() if k.created_at else None,
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            }
            for k in keys
        ]
    }


@router.delete("/keys/{key_id}")
async def revoke_key(
    key_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: UserContext = Depends(get_current_user),
):
    """Revoke one of the current user's API keys."""
    api_key = await session.get(APIKey, key_id)
    if not api_key or api_key.user_id != current_user.id:
        raise HTTPException(404, "Key not found")
    api_key.is_active = False
    await session.commit()
    return {"ok": True}


@router.get("/me")
async def me(current_user: UserContext = Depends(get_current_user)):
    """Return the authenticated user's identity."""
    return {
        "id": current_user.id,
        "email": current_user.email,
        "display_name": current_user.display_name,
        "is_dev": current_user.is_dev,
    }
