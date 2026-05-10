"""Sync GitHub issues + PRs for a tracked repo into the notes table.

Scope (Phase 3):
- Ingest issue/PR **body** only. Comments are deferred to a later phase.
- Dedup by `notes.slug = "gh-issue-{external_id}"` / `"gh-pr-{external_id}"`.
- Notes default to `visibility='admin'`. Admin publishes individually.
- Cursor: `integration_resources.sync_cursor` holds the max `updated_at` we've
  processed; next run passes it to GitHub's `since` parameter.

Not implemented here:
- Webhooks (need public URL; they call the same upsert helpers when added).
- LLM-driven cross-link detection (Phase 5 wires GitHub refs into graph_builder).
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fastapi import BackgroundTasks

from ...models.integration_resource import IntegrationResource
from ...models.note import Note
from .auth import get_installation_token
from .code import sync_code
from .wiki import sync_wiki

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"
_PAGE_SIZE = 100
_MAX_PAGES = 50  # safety stop — 5000 items per sync is plenty for v1
_LOW_REMAINING_THRESHOLD = 100  # pause if fewer than this many calls left


@dataclass
class SyncResult:
    resource_id: int
    issues_ingested: int = 0
    issues_updated: int = 0
    prs_ingested: int = 0
    prs_updated: int = 0
    wiki_ingested: int = 0
    wiki_updated: int = 0
    wiki_skipped: int = 0
    wiki_deleted: int = 0
    code_ingested: int = 0
    code_updated: int = 0
    code_skipped: int = 0
    code_skipped_too_large: int = 0
    code_deleted: int = 0
    errors: list[str] = field(default_factory=list)
    cursor: str | None = None
    synced_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "resource_id": self.resource_id,
            "issues_ingested": self.issues_ingested,
            "issues_updated": self.issues_updated,
            "prs_ingested": self.prs_ingested,
            "prs_updated": self.prs_updated,
            "wiki_ingested": self.wiki_ingested,
            "wiki_updated": self.wiki_updated,
            "wiki_skipped": self.wiki_skipped,
            "wiki_deleted": self.wiki_deleted,
            "code_ingested": self.code_ingested,
            "code_updated": self.code_updated,
            "code_skipped": self.code_skipped,
            "code_skipped_too_large": self.code_skipped_too_large,
            "code_deleted": self.code_deleted,
            "errors": self.errors,
            "cursor": self.cursor,
            "synced_at": self.synced_at,
        }


# ── HTTP layer ───────────────────────────────────────────────────────


async def _respect_rate_limit(resp: httpx.Response) -> None:
    """If we're low on calls, sleep until the reset time."""
    try:
        remaining = int(resp.headers.get("X-RateLimit-Remaining", "100"))
    except ValueError:
        return
    if remaining >= _LOW_REMAINING_THRESHOLD:
        return
    reset_ts = resp.headers.get("X-RateLimit-Reset")
    if not reset_ts:
        return
    try:
        reset_at = int(reset_ts)
    except ValueError:
        return
    now = int(datetime.now(timezone.utc).timestamp())
    wait = max(0, min(60, reset_at - now))  # cap 60s — anything longer, abort
    if wait > 0:
        logger.warning("GitHub rate-limit low (%d left); sleeping %ds", remaining, wait)
        await asyncio.sleep(wait)


async def _paginate(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
) -> list[dict]:
    """GET `url` paginated via page=N. Returns all items, bounded by _MAX_PAGES."""
    out: list[dict] = []
    page = 1
    params = {**params, "per_page": _PAGE_SIZE}
    while page <= _MAX_PAGES:
        params["page"] = page
        for attempt in range(3):
            resp = await client.get(url, params=params)
            if resp.status_code in (403, 429):
                wait = 2 ** attempt
                logger.warning("GitHub %d on %s; retry in %ds", resp.status_code, url, wait)
                await asyncio.sleep(wait)
                continue
            break
        resp.raise_for_status()
        await _respect_rate_limit(resp)
        batch = resp.json()
        if not isinstance(batch, list):
            raise ValueError(f"Expected list from {url}, got {type(batch).__name__}")
        out.extend(batch)
        if len(batch) < _PAGE_SIZE:
            break
        page += 1
    return out


# ── Transforms ───────────────────────────────────────────────────────


def _iso(ts: str | None) -> str:
    return ts or datetime.now(timezone.utc).isoformat()


def _slug_for(kind: Literal["issue", "pr"], external_id: int | str) -> str:
    return f"gh-{kind}-{external_id}"


def _tag_repo(full_name: str) -> str:
    """Build the `github/{owner}-{repo}` tag, keeping slug-safe chars only."""
    safe = re.sub(r"[^A-Za-z0-9._/-]+", "-", full_name)
    return "github/" + safe.replace("/", "-")


def _build_frontmatter(kind: Literal["issue", "pr"], item: dict, full_name: str) -> str:
    labels = [l.get("name") for l in (item.get("labels") or []) if l.get("name")]
    assignees = [a.get("login") for a in (item.get("assignees") or []) if a.get("login")]
    author = (item.get("user") or {}).get("login") or "(unknown)"
    title = item.get("title") or "(untitled)"
    lines = [
        "---",
        f'title: "{title.replace(chr(34), chr(39))}"',
        f"source: github:{kind}",
        f"external_id: {item.get('id')}",
        f"repo: {full_name}",
        f"number: {item.get('number')}",
        f"state: {item.get('state', 'open')}",
        f"author: {author}",
        f"labels: {json.dumps(labels)}",
        f"assignees: {json.dumps(assignees)}",
        f"html_url: {item.get('html_url', '')}",
        f"created_at: {item.get('created_at', '')}",
        f"updated_at: {item.get('updated_at', '')}",
    ]
    if kind == "pr":
        merged = item.get("merged_at")
        if merged:
            lines.append(f"merged_at: {merged}")
    lines.append("---")
    return "\n".join(lines)


def _build_tags(kind: str, full_name: str, item: dict) -> list[str]:
    tags = [_tag_repo(full_name), f"state/{item.get('state','open')}", f"type/github-{kind}"]
    for label in item.get("labels") or []:
        name = label.get("name")
        if name:
            tags.append(name)
    # Dedupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def _item_title(kind: Literal["issue", "pr"], item: dict) -> str:
    prefix = "Issue" if kind == "issue" else "PR"
    return f"{prefix} #{item.get('number')}: {item.get('title', '(untitled)')}"


async def _upsert_note(
    db: AsyncSession,
    *,
    kind: Literal["issue", "pr"],
    item: dict,
    full_name: str,
    space_id: int,
) -> Literal["ingested", "updated", "skipped"]:
    external_id = item.get("id")
    if external_id is None:
        return "skipped"
    slug = _slug_for(kind, external_id)
    source = f"github:{kind}"

    result = await db.execute(select(Note).where(Note.slug == slug))
    existing = result.scalar_one_or_none()

    frontmatter = _build_frontmatter(kind, item, full_name)
    body = item.get("body") or ""
    content = f"{frontmatter}\n\n# {_item_title(kind, item)}\n\n{body.strip()}\n"
    tags = _build_tags(kind, full_name, item)
    title = _item_title(kind, item)
    summary = (body.strip().split("\n", 1)[0])[:300] if body else None

    if existing is None:
        note = Note(
            document_id=None,
            parent_id=None,
            title=title,
            content=content,
            slug=slug,
            tags=json.dumps(tags),
            level=1,
            created_at=_iso(item.get("created_at")),
            source=source,
            summary=summary,
            space_id=space_id,
            visibility="admin",
        )
        db.add(note)
        return "ingested"

    existing.title = title
    existing.content = content
    existing.tags = json.dumps(tags)
    existing.summary = summary
    if existing.space_id != space_id:
        existing.space_id = space_id
    return "updated"


# ── Main entry ───────────────────────────────────────────────────────


def _max_updated_at(items: list[dict]) -> str | None:
    # ISO 8601 with Z suffix is lexicographically ordered — GitHub guarantees
    # this shape so string max is safe. If a timezone offset ever appears,
    # switch to datetime.fromisoformat before comparing.
    out = None
    for it in items:
        ua = it.get("updated_at")
        if ua and (out is None or ua > out):
            out = ua
    return out


async def sync_repo(
    db: AsyncSession,
    resource: IntegrationResource,
    installation_id: int,
    *,
    dry_run: bool = False,
    background_tasks: BackgroundTasks | None = None,
) -> SyncResult:
    """Incremental sync of issues + PRs for a tracked repo."""
    result = SyncResult(resource_id=resource.id)

    if resource.resource_type != "repo":
        result.errors.append(f"Resource {resource.id} is not a repo")
        return result
    if resource.space_id is None:
        result.errors.append("No target space configured")
        return result
    if not resource.enabled:
        result.errors.append("Resource disabled")
        return result

    cfg: dict = {}
    try:
        cfg = json.loads(resource.config_json or "{}")
    except ValueError:
        pass

    full_name = resource.name
    owner, _, repo = full_name.partition("/")
    if not owner or not repo:
        result.errors.append(f"Invalid repo name: {full_name!r}")
        return result

    want_issues = cfg.get("issues_enabled", False)
    want_prs = cfg.get("prs_enabled", False)
    since = resource.sync_cursor  # ISO8601 or None

    # Issues: /issues returns issues AND PRs; filter PRs out here
    issues_items: list[dict] = []
    prs_items: list[dict] = []

    if want_issues or want_prs:
        token = await get_installation_token(installation_id)
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
    else:
        headers = {}

    if want_issues or want_prs:
        try:
            async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
                if want_issues:
                    params = {"state": "all", "sort": "updated", "direction": "asc"}
                    if since:
                        params["since"] = since
                    combined = await _paginate(
                        client, f"{GITHUB_API}/repos/{owner}/{repo}/issues", params
                    )
                    issues_items = [i for i in combined if i.get("pull_request") is None]

                if want_prs:
                    params = {"state": "all", "sort": "updated", "direction": "asc"}
                    prs_items = await _paginate(
                        client, f"{GITHUB_API}/repos/{owner}/{repo}/pulls", params
                    )
                    if since:
                        prs_items = [p for p in prs_items if (p.get("updated_at") or "") >= since]
        except httpx.HTTPError as e:
            logger.exception("GitHub fetch failed for resource %d", resource.id)
            result.errors.append(f"GitHub API error: {e}")
            return result

    if dry_run:
        result.issues_ingested = len(issues_items)
        result.prs_ingested = len(prs_items)
        # Wiki/code dry-run is skipped — both require clones, which is what
        # we're trying to avoid in a preview. Flag explicitly.
        if cfg.get("wiki_enabled", False):
            result.errors.append("dry-run does not preview wiki changes")
        if cfg.get("code_enabled", False):
            result.errors.append("dry-run does not preview code changes")
        result.synced_at = datetime.now(timezone.utc).isoformat()
        return result

    succeeded: list[dict] = []  # only advance cursor past rows we actually persisted

    for item in issues_items:
        try:
            outcome = await _upsert_note(
                db, kind="issue", item=item, full_name=full_name, space_id=resource.space_id,
            )
        except Exception as e:  # keep going on bad rows; report at the end
            logger.exception("upsert failed for issue %s", item.get("id"))
            result.errors.append(f"issue #{item.get('number')}: {e}")
            continue
        if outcome == "ingested":
            result.issues_ingested += 1
        elif outcome == "updated":
            result.issues_updated += 1
        succeeded.append(item)

    for item in prs_items:
        try:
            outcome = await _upsert_note(
                db, kind="pr", item=item, full_name=full_name, space_id=resource.space_id,
            )
        except Exception as e:
            logger.exception("upsert failed for pr %s", item.get("id"))
            result.errors.append(f"pr #{item.get('number')}: {e}")
            continue
        if outcome == "ingested":
            result.prs_ingested += 1
        elif outcome == "updated":
            result.prs_updated += 1
        succeeded.append(item)

    now_iso = datetime.now(timezone.utc).isoformat()
    # Cursor safety: only advance across rows we successfully upserted. A failed
    # row keeps the cursor behind it, so next sync retries the whole window past
    # that point and can't silently skip it.
    if result.errors:
        # Cap the cursor at the earliest failed row's updated_at. Anything at or
        # after a failed timestamp should be retried.
        failed_items = [it for it in (issues_items + prs_items) if it not in succeeded]
        earliest_failure = None
        for it in failed_items:
            ua = it.get("updated_at")
            if ua and (earliest_failure is None or ua < earliest_failure):
                earliest_failure = ua
        safe_cursor = None
        if earliest_failure:
            # Take the max successful updated_at that is strictly before the earliest failure
            for it in succeeded:
                ua = it.get("updated_at")
                if ua and ua < earliest_failure and (safe_cursor is None or ua > safe_cursor):
                    safe_cursor = ua
        cursor = safe_cursor or since
    else:
        cursor = _max_updated_at(succeeded) or since

    if cursor and (not resource.sync_cursor or cursor > resource.sync_cursor):
        resource.sync_cursor = cursor
    resource.last_synced_at = now_iso
    await db.commit()

    # Wiki sync — content-hash change detection, independent of issue cursor.
    if cfg.get("wiki_enabled", False):
        try:
            wiki_result = await sync_wiki(db, resource, installation_id)
        except Exception as e:
            logger.exception("wiki sync raised for resource %d", resource.id)
            result.errors.append(f"wiki: {e}")
        else:
            result.wiki_ingested = wiki_result.ingested
            result.wiki_updated = wiki_result.updated
            result.wiki_skipped = wiki_result.skipped
            result.wiki_deleted = wiki_result.deleted
            result.errors.extend(wiki_result.errors)
            await db.commit()

    # Code sync — creates Documents and kicks off the note-generation pipeline
    # in the background. Counts here represent files *queued*, not notes
    # already materialized; admin watches /api/documents for progress.
    if cfg.get("code_enabled", False):
        try:
            code_result = await sync_code(
                db, resource, installation_id, background_tasks=background_tasks
            )
        except Exception as e:
            logger.exception("code sync raised for resource %d", resource.id)
            result.errors.append(f"code: {e}")
        else:
            result.code_ingested = code_result.ingested
            result.code_updated = code_result.updated
            result.code_skipped = code_result.skipped
            result.code_skipped_too_large = code_result.skipped_too_large
            result.code_deleted = code_result.deleted
            result.errors.extend(code_result.errors)
            # sync_code commits on its own; nothing extra here

    result.cursor = resource.sync_cursor
    result.synced_at = now_iso
    return result
