"""GitHub App authentication helpers.

A GitHub App identifies itself to GitHub two ways:

1. **App JWT** — an RS256-signed token claiming `iss = app_id`, valid for up to
   10 minutes. Required to call install-level endpoints (e.g. list installs,
   create an installation token).
2. **Installation access token** — a short-lived (1h) token minted by GitHub
   from an App JWT + installation id. Used for every actual API call against
   a repo/org that the install can reach.

This module builds both. Installation tokens are cached in-process with a
50-minute TTL (under the 60-minute server-side lifetime) so one sync run
doesn't mint a new token on every request.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
import jwt

from ...config import settings

_APP_JWT_TTL = 540  # seconds; GitHub caps at 600
_INSTALL_TOKEN_TTL = 50 * 60  # seconds; GitHub caps at 60m


class GitHubNotConfigured(RuntimeError):
    """Raised when GitHub App env vars haven't been set."""


@dataclass
class _CachedToken:
    token: str
    expires_at: float


_install_token_cache: dict[int, _CachedToken] = {}


def _require_app_config() -> tuple[str, str]:
    if not settings.github_app_id or not settings.github_app_private_key:
        raise GitHubNotConfigured(
            "GITHUB_APP_ID and GITHUB_APP_PRIVATE_KEY must be set before "
            "calling any /api/integrations/github/* endpoint."
        )
    # Normalize private key that came in as a single-line env var with literal \n
    pk = settings.github_app_private_key.replace("\\n", "\n")
    return settings.github_app_id, pk


def build_app_jwt() -> str:
    """Build a short-lived App JWT to call install-level GitHub endpoints."""
    app_id, private_key = _require_app_config()
    now = int(time.time())
    payload = {
        "iat": now - 60,  # small clock-skew cushion
        "exp": now + _APP_JWT_TTL,
        "iss": app_id,
    }
    return jwt.encode(payload, private_key, algorithm="RS256")


async def get_installation_token(installation_id: int) -> str:
    """Return a valid installation access token, minting one if the cache is stale."""
    cached = _install_token_cache.get(installation_id)
    now = time.time()
    if cached and cached.expires_at > now + 30:
        return cached.token

    app_jwt = build_app_jwt()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"https://api.github.com/app/installations/{installation_id}/access_tokens",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    resp.raise_for_status()
    data = resp.json()
    token = data["token"]
    _install_token_cache[installation_id] = _CachedToken(
        token=token,
        expires_at=now + _INSTALL_TOKEN_TTL,
    )
    return token


async def fetch_installation_metadata(installation_id: int) -> dict:
    """Return GitHub's record for an installation: account login, type, permissions."""
    app_jwt = build_app_jwt()
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(
            f"https://api.github.com/app/installations/{installation_id}",
            headers={
                "Authorization": f"Bearer {app_jwt}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
    resp.raise_for_status()
    return resp.json()


def invalidate_cache(installation_id: int | None = None) -> None:
    if installation_id is None:
        _install_token_cache.clear()
    else:
        _install_token_cache.pop(installation_id, None)


async def list_installation_repos(installation_id: int) -> list[dict]:
    """Return all repos this installation can reach.

    Pages through GET /installation/repositories (max 100/page; stops when the
    server returns fewer than the page size).
    """
    token = await get_installation_token(installation_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    per_page = 100
    repos: list[dict] = []
    async with httpx.AsyncClient(timeout=30.0) as client:
        page = 1
        while True:
            resp = await client.get(
                "https://api.github.com/installation/repositories",
                headers=headers,
                params={"per_page": per_page, "page": page},
            )
            resp.raise_for_status()
            data = resp.json()
            batch = data.get("repositories", [])
            repos.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
    return repos
