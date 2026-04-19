"""Authentication and authorization.

Two roles:
- admin: full access, authenticates with password
- user: read-only + AI features with own API key

Sessions are JWT tokens stored in cookies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

import bcrypt
import jwt
import sqlalchemy as sa
from fastapi import Cookie, Depends, HTTPException, Request

from .config import settings
from .models.note import Note

_ALGORITHM = "HS256"
_TOKEN_EXPIRY_HOURS = 72


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())


def verify_admin_password(password: str) -> bool:
    """Check password against ADMIN_PASSWORD env var."""
    return password == settings.admin_password


def create_token(role: str, user_api_key: str | None = None) -> str:
    payload = {
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=_TOKEN_EXPIRY_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    if user_api_key:
        payload["api_key"] = user_api_key
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[_ALGORITHM])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return None


# ── Dependency functions ──────────────────────────────────────────────


class AuthUser:
    """Represents the current authenticated user."""

    def __init__(self, role: str, api_key: str | None = None):
        self.role: Literal["admin", "user", "guest"] = role
        self.api_key = api_key

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)


def _get_user_from_request(request: Request) -> AuthUser:
    """Extract user from session cookie or return guest."""
    token = request.cookies.get("session")
    if not token:
        return AuthUser(role="guest")

    payload = decode_token(token)
    if not payload:
        return AuthUser(role="guest")

    return AuthUser(
        role=payload.get("role", "guest"),
        api_key=payload.get("api_key"),
    )


async def get_current_user(request: Request) -> AuthUser:
    """Get current user — always succeeds (returns guest if unauthenticated)."""
    return _get_user_from_request(request)


async def require_admin(request: Request) -> AuthUser:
    """Require admin role. Raises 401/403 if not."""
    user = _get_user_from_request(request)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def note_visibility_filter(is_admin: bool):
    """SQL fragment for filtering notes by visibility.

    Admin sees all notes; every other caller sees only notes with
    visibility='public'. Apply to every select(Note) on reader-reachable paths.
    """
    if is_admin:
        return sa.true()
    return Note.visibility == "public"


async def require_user_with_key(request: Request) -> AuthUser:
    """Require either admin or a user who has provided their own API key."""
    user = _get_user_from_request(request)
    if user.is_admin:
        return user
    if user.has_api_key:
        return user

    # Check for API key in header (allows one-off usage without session)
    header_key = request.headers.get("X-User-API-Key")
    if header_key:
        return AuthUser(role="user", api_key=header_key)

    raise HTTPException(
        status_code=403,
        detail="AI features require an API key. Provide your own key in settings.",
    )
