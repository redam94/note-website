"""Sync a GitHub repo's wiki into the notes table.

GitHub wikis live at `https://github.com/{owner}/{repo}.wiki.git` — a standalone
git repo with no REST API surface. We shallow-clone it (authenticated with the
installation token) into `uploads/github-wikis/{resource_id}/` and walk the
markdown files.

Each `Page-Name.md` → one note:
  - slug:       `gh-wiki-{resource_id}-{slug(page_display_name)}`
  - source:     `github:wiki`
  - visibility: `admin`
  - content:    frontmatter + the file's markdown

Change detection: sha256 of file bytes, written into the note's frontmatter
(`blob_sha:` line) and parsed back on the next sync. We deliberately do NOT
use `notes.summary` for this — that field is user-facing (search results,
index generators, maintenance flows all touch it) and would collide.
Pages deleted upstream are deleted downstream.

Files beginning with `_` (`_Sidebar.md`, `_Footer.md`, `_Header.md`) are skipped
in v1 — the Phase 8 publish flow rebuilds these from the space's hierarchy.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings as app_settings
from ...models.integration_resource import IntegrationResource
from ...models.note import Note
from .auth import get_installation_token

logger = logging.getLogger(__name__)

_SKIP_PREFIXES = ("_",)  # _Sidebar, _Footer, _Header — special wiki files


@dataclass
class WikiSyncResult:
    ingested: int = 0
    updated: int = 0
    skipped: int = 0  # unchanged pages
    deleted: int = 0
    errors: list[str] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


# ── Git operations (overridable for tests) ──────────────────────────


def _wiki_dir_for(resource_id: int) -> Path:
    base = Path(app_settings.uploads_dir) / "github-wikis" / str(resource_id)
    return base


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run git with list args (no shell) and a reasonable timeout."""
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


async def _pull_wiki_repo(
    resource_id: int,
    owner: str,
    repo: str,
    installation_token: str,
) -> Path:
    """Clone or fast-forward the wiki repo. Returns the checkout dir."""
    target = _wiki_dir_for(resource_id)
    remote = f"https://x-access-token:{installation_token}@github.com/{owner}/{repo}.wiki.git"

    def _do_clone_or_pull() -> Path:
        if (target / ".git").is_dir():
            # Update remote URL in case the token rotated, then fetch + hard reset.
            _run_git(["remote", "set-url", "origin", remote], cwd=target)
            _run_git(["fetch", "--depth", "1", "origin"], cwd=target)
            # Detect default branch from origin/HEAD
            try:
                _run_git(["remote", "set-head", "origin", "--auto"], cwd=target)
                head = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=target).stdout.strip()
                branch = head.rsplit("/", 1)[-1] if head else "master"
            except RuntimeError:
                branch = "master"
            _run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--depth", "1", remote, str(target)])
        return target

    # Run blocking git ops in a thread so we don't stall the event loop
    return await asyncio.to_thread(_do_clone_or_pull)


# ── Page parsing ────────────────────────────────────────────────────


def _page_display_name(filename: str) -> str:
    """Convert `Home.md` → `Home`, `Getting-Started.md` → `Getting Started`."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    return stem.replace("-", " ")


def _page_slug(resource_id: int, display_name: str) -> str:
    suffix = slugify(display_name, lowercase=True) or "page"
    return f"gh-wiki-{resource_id}-{suffix}"


_BLOB_SHA_RE = re.compile(r"^blob_sha:\s*(sha256:[a-f0-9]+)\s*$", re.MULTILINE)


def _content_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _extract_blob_sha(content: str | None) -> str | None:
    """Pull the sha back out of the note's frontmatter (written by _build_frontmatter)."""
    if not content:
        return None
    # Only look inside the leading frontmatter block to avoid false matches
    m = re.match(r"^---\n([\s\S]*?)\n---", content)
    if not m:
        return None
    sha_m = _BLOB_SHA_RE.search(m.group(1))
    return sha_m.group(1) if sha_m else None


def _first_paragraph(text: str, limit: int = 300) -> str | None:
    stripped = (text or "").strip()
    if not stripped:
        return None
    para = stripped.split("\n\n", 1)[0]
    # Drop heading markers and compact whitespace
    cleaned = re.sub(r"^#+\s*", "", para).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned[:limit] or None


def _html_url(owner: str, repo: str, filename: str) -> str:
    # GitHub converts `Page-Name.md` → /wiki/Page-Name
    stem = filename[:-3] if filename.endswith(".md") else filename
    return f"https://github.com/{owner}/{repo}/wiki/{stem}"


def _build_frontmatter(
    owner: str,
    repo: str,
    filename: str,
    display_name: str,
    sha: str,
) -> str:
    safe_title = display_name.replace('"', "'")
    lines = [
        "---",
        f'title: "{safe_title}"',
        "source: github:wiki",
        f"repo: {owner}/{repo}",
        f"page_name: {display_name}",
        f"external_id: {filename}",
        f"blob_sha: {sha}",
        f"html_url: {_html_url(owner, repo, filename)}",
        "---",
    ]
    return "\n".join(lines)


# ── Upsert ──────────────────────────────────────────────────────────


async def _upsert_page(
    db: AsyncSession,
    *,
    resource: IntegrationResource,
    owner: str,
    repo: str,
    filename: str,
    body: str,
) -> str:
    """Upsert one wiki page. Returns 'ingested' | 'updated' | 'skipped'."""
    display_name = _page_display_name(filename)
    slug = _page_slug(resource.id, display_name)
    sha = _content_hash(body)

    result = await db.execute(select(Note).where(Note.slug == slug))
    existing = result.scalar_one_or_none()

    if existing is not None and _extract_blob_sha(existing.content) == sha:
        return "skipped"

    frontmatter = _build_frontmatter(owner, repo, filename, display_name, sha)
    content = f"{frontmatter}\n\n# {display_name}\n\n{body.strip()}\n"
    tags = [f"github/{owner}-{repo}", "type/github-wiki"]
    summary = _first_paragraph(body)

    now_iso = datetime.now(timezone.utc).isoformat()
    if existing is None:
        note = Note(
            document_id=None,
            parent_id=None,
            title=display_name,
            content=content,
            slug=slug,
            tags=json.dumps(tags),
            level=1,
            created_at=now_iso,
            source="github:wiki",
            summary=summary,
            space_id=resource.space_id,
            visibility="admin",
        )
        db.add(note)
        return "ingested"

    existing.title = display_name
    existing.content = content
    existing.tags = json.dumps(tags)
    # Only refresh summary if it was never set by a human editor — avoid
    # clobbering admin-authored summaries on re-sync.
    if not existing.summary:
        existing.summary = summary
    if existing.space_id != resource.space_id:
        existing.space_id = resource.space_id
    return "updated"


async def _delete_missing_pages(
    db: AsyncSession,
    resource: IntegrationResource,
    present_slugs: set[str],
) -> int:
    """Drop notes for wiki pages that no longer exist upstream."""
    prefix = f"gh-wiki-{resource.id}-"
    existing_result = await db.execute(
        select(Note).where(Note.slug.like(prefix + "%"))
    )
    existing = existing_result.scalars().all()
    deleted = 0
    for note in existing:
        if note.slug not in present_slugs:
            await db.delete(note)
            deleted += 1
    return deleted


# ── Main entry ───────────────────────────────────────────────────────


async def sync_wiki(
    db: AsyncSession,
    resource: IntegrationResource,
    installation_id: int,
) -> WikiSyncResult:
    """Sync one repo's wiki. Clones / pulls, walks pages, upserts, deletes stale."""
    result = WikiSyncResult()

    if shutil.which("git") is None:
        result.errors.append("git is not installed on the host")
        return result
    if not resource.name or "/" not in resource.name:
        result.errors.append(f"Invalid repo name: {resource.name!r}")
        return result
    if resource.space_id is None:
        result.errors.append("No target space configured")
        return result

    owner, _, repo = resource.name.partition("/")
    token = await get_installation_token(installation_id)

    try:
        repo_dir = await _pull_wiki_repo(resource.id, owner, repo, token)
    except RuntimeError as e:
        # Missing wiki → clone fails with "Repository not found". Callers who
        # enabled wiki on a repo without one should just see this error.
        result.errors.append(f"Clone failed: {e}")
        return result

    # Walk .md files at the wiki root (Gollum's flat namespace).
    present_slugs: set[str] = set()
    for entry in sorted(os.listdir(repo_dir)):
        if not entry.endswith(".md"):
            continue
        if entry.startswith(_SKIP_PREFIXES):
            continue
        path = repo_dir / entry
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result.errors.append(f"{entry}: {e}")
            continue

        display_name = _page_display_name(entry)
        present_slugs.add(_page_slug(resource.id, display_name))

        try:
            outcome = await _upsert_page(
                db,
                resource=resource,
                owner=owner,
                repo=repo,
                filename=entry,
                body=body,
            )
        except Exception as e:
            logger.exception("wiki upsert failed for %s", entry)
            result.errors.append(f"{entry}: {e}")
            continue

        if outcome == "ingested":
            result.ingested += 1
        elif outcome == "updated":
            result.updated += 1
        else:
            result.skipped += 1

    # Only prune if this run completed without errors. If something blew up
    # mid-walk, we can't trust `present_slugs` to be complete, and deleting
    # notes based on an incomplete set would lose real content.
    if not result.errors:
        try:
            result.deleted = await _delete_missing_pages(db, resource, present_slugs)
        except Exception as e:
            logger.exception("wiki prune failed")
            result.errors.append(f"prune failed: {e}")

    return result
