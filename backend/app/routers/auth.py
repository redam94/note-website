from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from ..auth import (
    AuthUser,
    create_token,
    get_current_user,
    verify_admin_password,
)

router = APIRouter(prefix="/api/auth")


class LoginRequest(BaseModel):
    password: str | None = None
    api_key: str | None = None
    role: str = "user"  # "admin" or "user"


class AuthResponse(BaseModel):
    role: str
    has_api_key: bool


@router.post("/login")
async def login(body: LoginRequest, response: Response):
    """Login as admin (with password) or as user (optionally with API key)."""
    if body.role == "admin":
        if not body.password or not verify_admin_password(body.password):
            return {"error": "Invalid password"}, 401

        token = create_token("admin")
        response.set_cookie(
            key="session",
            value=token,
            httponly=True,
            samesite="lax",
            max_age=72 * 3600,
            path="/",
        )
        return {"role": "admin", "has_api_key": True}

    # User login — optionally with their own API key
    token = create_token("user", user_api_key=body.api_key or None)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=72 * 3600,
        path="/",
    )
    return {"role": "user", "has_api_key": bool(body.api_key)}


@router.post("/logout")
async def logout(response: Response):
    response.delete_cookie("session", path="/")
    return {"role": "guest"}


@router.get("/me")
async def get_me(user: AuthUser = Depends(get_current_user)) -> AuthResponse:
    return AuthResponse(role=user.role, has_api_key=user.has_api_key)


@router.put("/api-key")
async def update_user_api_key(
    body: LoginRequest,
    user: AuthUser = Depends(get_current_user),
    response: Response = None,
):
    """Update the user's personal API key (re-issues session token)."""
    role = user.role if user.role != "guest" else "user"
    token = create_token(role, user_api_key=body.api_key or None)
    response.set_cookie(
        key="session",
        value=token,
        httponly=True,
        samesite="lax",
        max_age=72 * 3600,
        path="/",
    )
    return {"role": role, "has_api_key": bool(body.api_key)}
