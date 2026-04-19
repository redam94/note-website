"""GitHub App install + account-management endpoints.

Flow:
  1. Admin hits GET /install → we sign a short-lived CSRF state and redirect
     to the GitHub install URL.
  2. GitHub redirects back to GET /callback with ?installation_id=… &state=…
     &setup_action=install|update. We verify the state, fetch install metadata
     from GitHub, and upsert a connected_accounts row.
  3. Admin can list via GET /accounts and remove via DELETE /accounts/{id}.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_admin
from ...config import settings
from ...database import get_db
from ...integrations.github.auth import (
    GitHubNotConfigured,
    fetch_installation_metadata,
    invalidate_cache,
)
from ...models.connected_account import ConnectedAccount

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/integrations/github")

_STATE_PURPOSE = "gh_install"
_STATE_TTL_SECONDS = 600


# ── Helpers ──────────────────────────────────────────────────────────


def _require_configured():
    missing = [
        name
        for name, val in [
            ("GITHUB_APP_ID", settings.github_app_id),
            ("GITHUB_APP_SLUG", settings.github_app_slug),
            ("GITHUB_APP_PRIVATE_KEY", settings.github_app_private_key),
        ]
        if not val
    ]
    if missing:
        raise HTTPException(
            status_code=503,
            detail=f"GitHub App not configured. Missing env vars: {', '.join(missing)}.",
        )


def _sign_state(nonce: str) -> str:
    payload = {
        "nonce": nonce,
        "purpose": _STATE_PURPOSE,
        "exp": datetime.now(timezone.utc) + timedelta(seconds=_STATE_TTL_SECONDS),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _verify_state(state: str) -> None:
    try:
        payload = jwt.decode(state, settings.jwt_secret, algorithms=["HS256"])
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=400, detail=f"Invalid state: {e}") from e
    if payload.get("purpose") != _STATE_PURPOSE:
        raise HTTPException(status_code=400, detail="State purpose mismatch")


# ── Schemas ──────────────────────────────────────────────────────────


class ConnectedAccountOut(BaseModel):
    id: int
    integration_type: str
    label: str
    external_account_id: str | None
    created_at: str
    metadata: dict


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/install", dependencies=[Depends(require_admin)])
async def github_install():
    """Redirect the admin to GitHub's install flow."""
    _require_configured()
    state = _sign_state(secrets.token_urlsafe(24))
    url = (
        f"https://github.com/apps/{settings.github_app_slug}/installations/new"
        f"?{urlencode({'state': state})}"
    )
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def github_callback(
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Handle the post-install redirect from GitHub.

    Note: this endpoint is NOT gated by require_admin because GitHub redirects
    the user's browser here without our cookies in some setups. Security rests
    on the signed `state` token we issued from the (admin-gated) /install
    endpoint — an attacker cannot forge a valid state.
    """
    _require_configured()
    if not state:
        raise HTTPException(status_code=400, detail="Missing state")
    _verify_state(state)

    if installation_id is None:
        raise HTTPException(status_code=400, detail="Missing installation_id")

    try:
        meta = await fetch_installation_metadata(installation_id)
    except (GitHubNotConfigured, httpx.HTTPError) as e:
        logger.exception("Failed to fetch install metadata")
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e

    account = meta.get("account") or {}
    account_login = account.get("login") or "(unknown)"
    account_id = str(account.get("id")) if account.get("id") is not None else None
    account_type = account.get("type") or "User"

    # Upsert: same installation_id (stored in metadata) updates in place.
    existing_q = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.integration_type == "github",
            ConnectedAccount.external_account_id == account_id,
        )
    )
    row = existing_q.scalar_one_or_none()

    metadata_payload = {
        "installation_id": installation_id,
        "account_type": account_type,
        "permissions": meta.get("permissions") or {},
        "repository_selection": meta.get("repository_selection"),
    }
    now_iso = datetime.now(timezone.utc).isoformat()

    if row is None:
        row = ConnectedAccount(
            integration_type="github",
            label=f"{account_login} ({account_type.lower()})",
            external_account_id=account_id,
            scopes=json.dumps(sorted((meta.get("permissions") or {}).keys())),
            metadata_json=json.dumps(metadata_payload),
            created_at=now_iso,
        )
        db.add(row)
    else:
        row.label = f"{account_login} ({account_type.lower()})"
        row.scopes = json.dumps(sorted((meta.get("permissions") or {}).keys()))
        row.metadata_json = json.dumps(metadata_payload)
        invalidate_cache(installation_id)

    await db.commit()

    # Redirect back to the settings page with a success flag.
    return RedirectResponse(
        url=f"/settings/integrations?connected=github&action={setup_action or 'install'}",
        status_code=302,
    )


@router.get("/accounts", dependencies=[Depends(require_admin)])
async def list_github_accounts(
    db: AsyncSession = Depends(get_db),
) -> list[ConnectedAccountOut]:
    result = await db.execute(
        select(ConnectedAccount).where(ConnectedAccount.integration_type == "github")
    )
    rows = result.scalars().all()
    return [
        ConnectedAccountOut(
            id=r.id,
            integration_type=r.integration_type,
            label=r.label,
            external_account_id=r.external_account_id,
            created_at=r.created_at,
            metadata=json.loads(r.metadata_json or "{}"),
        )
        for r in rows
    ]


@router.delete("/accounts/{account_id}", dependencies=[Depends(require_admin)])
async def disconnect_github_account(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id,
            ConnectedAccount.integration_type == "github",
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")

    # Drop any cached install token for this account before the row disappears.
    try:
        md = json.loads(row.metadata_json or "{}")
        if inst_id := md.get("installation_id"):
            invalidate_cache(int(inst_id))
    except (ValueError, TypeError):
        pass

    await db.delete(row)
    await db.commit()
    return {"deleted": account_id}
