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
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth import require_admin
from ...config import settings
from ...database import get_db
from ...dependencies import get_current_space
from ...integrations.github.auth import (
    GitHubNotConfigured,
    fetch_installation_metadata,
    invalidate_cache,
    list_installation_repos,
)
from ...integrations.github.publish import run_publish
from ...integrations.github.sync import sync_repo
from ...models.connected_account import ConnectedAccount
from ...models.integration_publish_target import IntegrationPublishTarget
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
    code_enabled: bool
    code_extraction_profile_id: int | None
    code_include_exts: list[str]
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
    code_enabled: bool = False
    code_extraction_profile_id: int | None = None
    code_include_exts: list[str] = []


class RepoBulkUpsert(BaseModel):
    repos: list[RepoSelection]


class VaultRepoOut(RepoOut):
    account_id: int
    account_label: str


class ResourcePatch(BaseModel):
    """Single-resource edit. Feature flags and profile/exts only — space_id
    and account_id are immutable here; admin must delete + re-track to move
    a repo between vaults.

    Pydantic 2 `model_fields_set` distinguishes "not provided" from "provided
    as null" — that's how we clear the profile vs leaving it alone.
    """

    enabled: bool | None = None
    issues_enabled: bool | None = None
    prs_enabled: bool | None = None
    wiki_enabled: bool | None = None
    code_enabled: bool | None = None
    code_extraction_profile_id: int | None = None
    code_include_exts: list[str] | None = None


class RepoTrackRequest(BaseModel):
    """Start tracking one repo into the current vault."""

    account_id: int
    external_id: str
    full_name: str
    default_branch: str | None = None
    private: bool = False
    enabled: bool = True
    issues_enabled: bool = True
    prs_enabled: bool = True
    wiki_enabled: bool = False
    code_enabled: bool = False
    code_extraction_profile_id: int | None = None
    code_include_exts: list[str] = []


class PublishTargetIn(BaseModel):
    account_id: int
    repo_full_name: str
    wiki_branch: str = "master"


class PublishTargetOut(BaseModel):
    id: int
    account_id: int
    space_id: int
    resource_id: int | None
    repo_full_name: str
    wiki_branch: str
    last_published_sha: str | None
    last_published_at: str | None
    created_at: str


class PublishRequest(BaseModel):
    space_id: int
    dry_run: bool = False
    force_overwrite: bool = False


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
                code_enabled=bool(cfg.get("code_enabled", False)),
                code_extraction_profile_id=cfg.get("code_extraction_profile_id"),
                code_include_exts=list(cfg.get("code_include_exts") or []),
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
        # Preserve any existing hash map so we don't force a full re-sync
        # every time the admin edits something unrelated.
        prior_cfg: dict = {}
        if (prior := existing.get(sel.external_id)) and prior.config_json:
            try:
                prior_cfg = json.loads(prior.config_json)
            except ValueError:
                prior_cfg = {}
        cfg = {
            "issues_enabled": bool(sel.issues_enabled),
            "prs_enabled": bool(sel.prs_enabled),
            "wiki_enabled": bool(sel.wiki_enabled),
            "code_enabled": bool(sel.code_enabled),
            "code_extraction_profile_id": sel.code_extraction_profile_id,
            "code_include_exts": [
                e.lstrip(".").lower() for e in sel.code_include_exts if e and e.strip()
            ],
            "default_branch": sel.default_branch,
            "private": bool(sel.private),
        }
        if "code_hashes" in prior_cfg:
            cfg["code_hashes"] = prior_cfg["code_hashes"]
        row = prior
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
                code_enabled=bool(cfg.get("code_enabled", False)),
                code_extraction_profile_id=cfg.get("code_extraction_profile_id"),
                code_include_exts=list(cfg.get("code_include_exts") or []),
                last_synced_at=r.last_synced_at,
            )
        )
    return out


# ── Vault-scoped repo management ─────────────────────────────────────


def _resource_to_vault_out(
    row: IntegrationResource, account_label: str
) -> VaultRepoOut:
    cfg: dict = {}
    if row.config_json:
        try:
            cfg = json.loads(row.config_json)
        except ValueError:
            cfg = {}
    owner, _, name = (row.name or "").partition("/")
    return VaultRepoOut(
        external_id=row.external_id,
        full_name=row.name,
        owner=owner,
        name=name,
        default_branch=cfg.get("default_branch"),
        private=bool(cfg.get("private", False)),
        resource_id=row.id,
        enabled=bool(row.enabled),
        space_id=row.space_id,
        issues_enabled=bool(cfg.get("issues_enabled", False)),
        prs_enabled=bool(cfg.get("prs_enabled", False)),
        wiki_enabled=bool(cfg.get("wiki_enabled", False)),
        code_enabled=bool(cfg.get("code_enabled", False)),
        code_extraction_profile_id=cfg.get("code_extraction_profile_id"),
        code_include_exts=list(cfg.get("code_include_exts") or []),
        last_synced_at=row.last_synced_at,
        account_id=row.account_id,
        account_label=account_label,
    )


@router.get("/repos", dependencies=[Depends(require_admin)])
async def list_vault_repos(
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[VaultRepoOut]:
    """Tracked repos targeting the current vault, across all connected accounts."""
    rows = (
        await db.execute(
            select(IntegrationResource, ConnectedAccount.label)
            .join(
                ConnectedAccount, ConnectedAccount.id == IntegrationResource.account_id
            )
            .where(
                IntegrationResource.resource_type == "repo",
                IntegrationResource.space_id == current_space.id,
            )
        )
    ).all()
    return [_resource_to_vault_out(row, label) for row, label in rows]


@router.post("/repos/track", status_code=201, dependencies=[Depends(require_admin)])
async def track_repo_in_vault(
    body: RepoTrackRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> VaultRepoOut:
    """Start tracking one repo into the current vault.

    The unique index on (account_id, resource_type, external_id) means one
    install can track a given repo against at most one vault at a time. If a
    prior row exists for another vault, reject — admin must remove it there
    first. A prior row for the current vault is treated as a config reset.
    """
    acct = await _load_account(db, body.account_id)

    prior_result = await db.execute(
        select(IntegrationResource).where(
            IntegrationResource.account_id == acct.id,
            IntegrationResource.resource_type == "repo",
            IntegrationResource.external_id == body.external_id,
        )
    )
    prior = prior_result.scalar_one_or_none()

    if prior is not None and prior.space_id != current_space.id:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{body.full_name} is already tracked by another vault on this "
                "install — remove it there first."
            ),
        )

    prior_cfg: dict = {}
    if prior and prior.config_json:
        try:
            prior_cfg = json.loads(prior.config_json)
        except ValueError:
            prior_cfg = {}

    cfg = {
        "issues_enabled": bool(body.issues_enabled),
        "prs_enabled": bool(body.prs_enabled),
        "wiki_enabled": bool(body.wiki_enabled),
        "code_enabled": bool(body.code_enabled),
        "code_extraction_profile_id": body.code_extraction_profile_id,
        "code_include_exts": [
            e.lstrip(".").lower() for e in body.code_include_exts if e and e.strip()
        ],
        "default_branch": body.default_branch,
        "private": bool(body.private),
    }
    if prior is not None and "code_hashes" in prior_cfg:
        cfg["code_hashes"] = prior_cfg["code_hashes"]

    now_iso = datetime.now(timezone.utc).isoformat()
    if prior is None:
        row = IntegrationResource(
            account_id=acct.id,
            resource_type="repo",
            external_id=body.external_id,
            name=body.full_name,
            enabled=1 if body.enabled else 0,
            config_json=json.dumps(cfg),
            space_id=current_space.id,
            created_at=now_iso,
        )
        db.add(row)
    else:
        row = prior
        row.name = body.full_name or row.name
        row.enabled = 1 if body.enabled else 0
        row.config_json = json.dumps(cfg)
        row.space_id = current_space.id

    await db.commit()
    await db.refresh(row)
    return _resource_to_vault_out(row, acct.label)


@router.patch(
    "/resources/{resource_id}", dependencies=[Depends(require_admin)]
)
async def patch_resource(
    resource_id: int,
    body: ResourcePatch,
    db: AsyncSession = Depends(get_db),
) -> VaultRepoOut:
    """Edit feature flags / profile / exts on a single tracked repo. Does not
    change account_id or space_id — to move a repo to a different vault,
    delete it and re-track from the destination vault."""
    result = await db.execute(
        select(IntegrationResource, ConnectedAccount.label)
        .join(ConnectedAccount, ConnectedAccount.id == IntegrationResource.account_id)
        .where(IntegrationResource.id == resource_id)
    )
    hit = result.first()
    if hit is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    row, acct_label = hit

    cfg: dict = {}
    if row.config_json:
        try:
            cfg = json.loads(row.config_json)
        except ValueError:
            cfg = {}

    provided = body.model_fields_set
    if "enabled" in provided and body.enabled is not None:
        row.enabled = 1 if body.enabled else 0
    if "issues_enabled" in provided and body.issues_enabled is not None:
        cfg["issues_enabled"] = bool(body.issues_enabled)
    if "prs_enabled" in provided and body.prs_enabled is not None:
        cfg["prs_enabled"] = bool(body.prs_enabled)
    if "wiki_enabled" in provided and body.wiki_enabled is not None:
        cfg["wiki_enabled"] = bool(body.wiki_enabled)
    if "code_enabled" in provided and body.code_enabled is not None:
        cfg["code_enabled"] = bool(body.code_enabled)
    if "code_extraction_profile_id" in provided:
        cfg["code_extraction_profile_id"] = body.code_extraction_profile_id
    if "code_include_exts" in provided and body.code_include_exts is not None:
        cfg["code_include_exts"] = [
            e.lstrip(".").lower() for e in body.code_include_exts if e and e.strip()
        ]

    row.config_json = json.dumps(cfg)
    await db.commit()
    await db.refresh(row)
    return _resource_to_vault_out(row, acct_label)


@router.post("/resources/{resource_id}/sync", dependencies=[Depends(require_admin)])
async def sync_resource(
    resource_id: int,
    background_tasks: BackgroundTasks,
    dry_run: bool = Query(default=False),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run incremental sync for a tracked repo. Admin-only.

    Returns counts synchronously for issues/PRs/wiki. Code-file processing
    runs in the background via FastAPI's BackgroundTasks — the counts here
    are files *queued*; the note-generation pipeline completes afterward.

    With `dry_run=true`, returns the count of items that *would* be ingested
    without writing to the DB — useful for previewing before the first sync.
    """
    _require_configured()
    result = await db.execute(
        select(IntegrationResource).where(IntegrationResource.id == resource_id)
    )
    resource = result.scalar_one_or_none()
    if not resource:
        raise HTTPException(status_code=404, detail="Resource not found")

    acct = await _load_account(db, resource.account_id)
    installation_id = _installation_id_for(acct)

    sync_result = await sync_repo(
        db, resource, installation_id,
        dry_run=dry_run,
        background_tasks=background_tasks,
    )
    return sync_result.as_dict()


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


# ── Publish targets + publish run ────────────────────────────────────


def _target_to_out(t: IntegrationPublishTarget) -> PublishTargetOut:
    return PublishTargetOut(
        id=t.id,
        account_id=t.account_id,
        space_id=t.space_id,
        resource_id=t.resource_id,
        repo_full_name=t.repo_full_name,
        wiki_branch=t.wiki_branch,
        last_published_sha=t.last_published_sha,
        last_published_at=t.last_published_at,
        created_at=t.created_at,
    )


@router.get(
    "/publish-targets/space/{space_id}", dependencies=[Depends(require_admin)]
)
async def get_publish_target(
    space_id: int,
    db: AsyncSession = Depends(get_db),
) -> PublishTargetOut | None:
    result = await db.execute(
        select(IntegrationPublishTarget).where(
            IntegrationPublishTarget.space_id == space_id
        )
    )
    t = result.scalar_one_or_none()
    return _target_to_out(t) if t else None


@router.put(
    "/publish-targets/space/{space_id}", dependencies=[Depends(require_admin)]
)
async def put_publish_target(
    space_id: int,
    body: PublishTargetIn,
    db: AsyncSession = Depends(get_db),
) -> PublishTargetOut:
    # Validate space exists
    sp_result = await db.execute(select(Space).where(Space.id == space_id))
    if not sp_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Space not found")
    # Validate account + type
    acct = await _load_account(db, body.account_id)

    # Attempt to find the tracking resource (optional convenience link)
    resource_id: int | None = None
    r = await db.execute(
        select(IntegrationResource).where(
            IntegrationResource.account_id == acct.id,
            IntegrationResource.resource_type == "repo",
            IntegrationResource.name == body.repo_full_name,
        )
    )
    existing_resource = r.scalar_one_or_none()
    if existing_resource is not None:
        resource_id = existing_resource.id

    # Upsert
    result = await db.execute(
        select(IntegrationPublishTarget).where(
            IntegrationPublishTarget.space_id == space_id
        )
    )
    row = result.scalar_one_or_none()
    now_iso = datetime.now(timezone.utc).isoformat()
    if row is None:
        row = IntegrationPublishTarget(
            account_id=acct.id,
            space_id=space_id,
            resource_id=resource_id,
            repo_full_name=body.repo_full_name,
            wiki_branch=body.wiki_branch,
            created_at=now_iso,
        )
        db.add(row)
    else:
        row.account_id = acct.id
        row.resource_id = resource_id
        row.repo_full_name = body.repo_full_name
        row.wiki_branch = body.wiki_branch
    await db.commit()
    await db.refresh(row)
    return _target_to_out(row)


@router.delete(
    "/publish-targets/space/{space_id}", dependencies=[Depends(require_admin)]
)
async def delete_publish_target(
    space_id: int,
    db: AsyncSession = Depends(get_db),
) -> dict:
    result = await db.execute(
        select(IntegrationPublishTarget).where(
            IntegrationPublishTarget.space_id == space_id
        )
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="No publish target for that space")
    await db.delete(row)
    await db.commit()
    return {"deleted": space_id}


@router.post("/publish", dependencies=[Depends(require_admin)])
async def publish_space(
    body: PublishRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Run a publish for the given space's target."""
    _require_configured()
    result = await db.execute(
        select(IntegrationPublishTarget).where(
            IntegrationPublishTarget.space_id == body.space_id
        )
    )
    target = result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="No publish target configured for this space")

    acct = await _load_account(db, target.account_id)
    installation_id = _installation_id_for(acct)

    out = await run_publish(
        db,
        target,
        installation_id,
        dry_run=body.dry_run,
        force_overwrite=body.force_overwrite,
    )
    return out.as_dict()


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
