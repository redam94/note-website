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
    list_installation_repos,
)
from ...models.connected_account import ConnectedAccount
from ...models.integration_resource import IntegrationResource
from ...models.space import Space

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


class RepoOut(BaseModel):
    external_id: str
    full_name: str
    owner: str
    name: str
    default_branch: str | None
    private: bool
    # Tracking state (null if not tracked)
    resource_id: int | None
    enabled: bool
    space_id: int | None
    issues_enabled: bool
    prs_enabled: bool
    wiki_enabled: bool
    last_synced_at: str | None


class RepoSelection(BaseModel):
    external_id: str
    full_name: str
    default_branch: str | None = None
    private: bool = False
    enabled: bool = True
    space_id: int | None = None
    issues_enabled: bool = True
    prs_enabled: bool = True
    wiki_enabled: bool = False


class RepoBulkUpsert(BaseModel):
    repos: list[RepoSelection]


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


@router.get("/callback", dependencies=[Depends(require_admin)])
async def github_callback(
    installation_id: int | None = Query(default=None),
    setup_action: str | None = Query(default=None),
    state: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Handle the post-install redirect from GitHub.

    Two gates: admin session cookie (so an intercepted state can't be replayed
    from a different browser) AND signed state (standard CSRF protection).
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


async def _load_account(db: AsyncSession, account_id: int) -> ConnectedAccount:
    result = await db.execute(
        select(ConnectedAccount).where(
            ConnectedAccount.id == account_id,
            ConnectedAccount.integration_type == "github",
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Account not found")
    return row


def _installation_id_for(acct: ConnectedAccount) -> int:
    try:
        md = json.loads(acct.metadata_json or "{}")
    except ValueError:
        md = {}
    inst_id = md.get("installation_id")
    if not inst_id:
        raise HTTPException(
            status_code=409,
            detail="Account has no installation_id — reconnect it.",
        )
    return int(inst_id)


@router.get("/accounts/{account_id}/repos", dependencies=[Depends(require_admin)])
async def list_account_repos(
    account_id: int,
    db: AsyncSession = Depends(get_db),
) -> list[RepoOut]:
    """Merge the live GitHub repo list with any integration_resources rows
    so the UI can render the enable-state for each one."""
    _require_configured()
    acct = await _load_account(db, account_id)
    installation_id = _installation_id_for(acct)

    try:
        repos = await list_installation_repos(installation_id)
    except (GitHubNotConfigured, httpx.HTTPError) as e:
        logger.exception("Failed to list installation repos")
        raise HTTPException(status_code=502, detail=f"GitHub API error: {e}") from e

    # Fetch existing tracking rows for this account/resource_type
    tracked_result = await db.execute(
        select(IntegrationResource).where(
            IntegrationResource.account_id == account_id,
            IntegrationResource.resource_type == "repo",
        )
    )
    by_ext_id = {r.external_id: r for r in tracked_result.scalars().all()}

    out: list[RepoOut] = []
    for r in repos:
        ext_id = str(r["id"])
        tracked = by_ext_id.get(ext_id)
        cfg: dict = {}
        if tracked and tracked.config_json:
            try:
                cfg = json.loads(tracked.config_json)
            except ValueError:
                cfg = {}
        owner_obj = r.get("owner") or {}
        out.append(
            RepoOut(
                external_id=ext_id,
                full_name=r.get("full_name", ""),
                owner=owner_obj.get("login", ""),
                name=r.get("name", ""),
                default_branch=r.get("default_branch"),
                private=bool(r.get("private", False)),
                resource_id=tracked.id if tracked else None,
                enabled=bool(tracked.enabled) if tracked else False,
                space_id=tracked.space_id if tracked else None,
                issues_enabled=bool(cfg.get("issues_enabled", False)),
                prs_enabled=bool(cfg.get("prs_enabled", False)),
                wiki_enabled=bool(cfg.get("wiki_enabled", False)),
                last_synced_at=tracked.last_synced_at if tracked else None,
            )
        )
    return out


@router.post("/accounts/{account_id}/resources", dependencies=[Depends(require_admin)])
async def upsert_account_resources(
    account_id: int,
    body: RepoBulkUpsert,
    db: AsyncSession = Depends(get_db),
) -> list[RepoOut]:
    """Bulk upsert repo tracking state. Rows absent from the payload are left alone."""
    acct = await _load_account(db, account_id)

    # Pre-validate every referenced space
    space_ids = {s.space_id for s in body.repos if s.space_id is not None}
    if space_ids:
        result = await db.execute(select(Space.id).where(Space.id.in_(space_ids)))
        valid_ids = {row[0] for row in result.all()}
        missing = space_ids - valid_ids
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown space_id(s): {sorted(missing)}",
            )

    existing_result = await db.execute(
        select(IntegrationResource).where(
            IntegrationResource.account_id == account_id,
            IntegrationResource.resource_type == "repo",
        )
    )
    existing = {r.external_id: r for r in existing_result.scalars().all()}

    now_iso = datetime.now(timezone.utc).isoformat()
    touched: list[IntegrationResource] = []

    for sel in body.repos:
        cfg = {
            "issues_enabled": bool(sel.issues_enabled),
            "prs_enabled": bool(sel.prs_enabled),
            "wiki_enabled": bool(sel.wiki_enabled),
            "default_branch": sel.default_branch,
            "private": bool(sel.private),
        }
        row = existing.get(sel.external_id)
        if row is None:
            row = IntegrationResource(
                account_id=account_id,
                resource_type="repo",
                external_id=sel.external_id,
                name=sel.full_name,
                enabled=1 if sel.enabled else 0,
                config_json=json.dumps(cfg),
                space_id=sel.space_id,
                created_at=now_iso,
            )
            db.add(row)
        else:
            row.name = sel.full_name or row.name
            row.enabled = 1 if sel.enabled else 0
            row.config_json = json.dumps(cfg)
            row.space_id = sel.space_id
        touched.append(row)

    await db.commit()

    # Re-read with ids populated and return in the same shape as the GET endpoint
    acct_id = acct.id
    refreshed = await db.execute(
        select(IntegrationResource).where(
            IntegrationResource.account_id == acct_id,
            IntegrationResource.resource_type == "repo",
        )
    )
    rows = refreshed.scalars().all()
    out: list[RepoOut] = []
    for r in rows:
        cfg = {}
        if r.config_json:
            try:
                cfg = json.loads(r.config_json)
            except ValueError:
                cfg = {}
        owner, _, name = (r.name or "").partition("/")
        out.append(
            RepoOut(
                external_id=r.external_id,
                full_name=r.name,
                owner=owner,
                name=name,
                default_branch=cfg.get("default_branch"),
                private=bool(cfg.get("private", False)),
                resource_id=r.id,
                enabled=bool(r.enabled),
                space_id=r.space_id,
                issues_enabled=bool(cfg.get("issues_enabled", False)),
                prs_enabled=bool(cfg.get("prs_enabled", False)),
                wiki_enabled=bool(cfg.get("wiki_enabled", False)),
                last_synced_at=r.last_synced_at,
            )
        )
    return out


@router.delete("/resources/{resource_id}", dependencies=[Depends(require_admin)])
async def delete_resource(
    resource_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(IntegrationResource).where(IntegrationResource.id == resource_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Resource not found")
    await db.delete(row)
    await db.commit()
    return {"deleted": resource_id}


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
