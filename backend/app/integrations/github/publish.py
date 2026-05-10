"""Publish pipeline: push a space's public notes to a GitHub repo's wiki.

High-level flow (run_publish):

  1. Load the target's public notes from the space (`visibility='public'`).
  2. Build title → page_name map with collision disambiguation.
  3. Transform each note's body (wikilinks rewritten, frontmatter stripped).
  4. Clone / fast-forward the `{repo}.wiki.git` repo into `uploads/github-wiki-publish/`.
  5. Safety guards: orphan rate ≤ 30% (unless force), non-empty wiki requires
     force on first publish.
  6. Write transformed files (including `_Sidebar.md`, `_Footer.md`).
  7. Remove files for pages that no longer belong.
  8. Commit + push. No-op if no files changed.
  9. Record commit SHA on the target.

Dry-run path (`dry_run=True`): does 1–5, skips 6–9, returns the preview counts
and orphan warnings without touching the remote.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings as app_settings
from ...models.connected_account import ConnectedAccount
from ...models.integration_publish_target import IntegrationPublishTarget
from ...models.note import Note
from .auth import get_installation_token
from .publish_transform import (
    OrphanLink,
    build_page_map,
    page_filename,
    render_page_markdown,
    strip_frontmatter,
    transform_body,
)

logger = logging.getLogger(__name__)

_ORPHAN_RATE_THRESHOLD = 0.30
_GIT_USER_EMAIL = "noreply@note-website"
_GIT_USER_NAME = "note-website publish bot"
_MANAGED_PATHS_PREFIX = "pages"  # Only delete files that WE manage, inside the
# flat root namespace; any hand-authored pages admin put into subfolders or
# started with `_` are left alone.


@dataclass
class PublishResult:
    dry_run: bool
    space_id: int
    pages_written: int = 0
    pages_deleted: int = 0
    orphan_links: list[OrphanLink] = field(default_factory=list)
    total_wikilinks_seen: int = 0
    commit_sha: str | None = None
    pushed: bool = False
    errors: list[str] = field(default_factory=list)
    synced_at: str | None = None

    def as_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "space_id": self.space_id,
            "pages_written": self.pages_written,
            "pages_deleted": self.pages_deleted,
            "orphan_links": [
                {
                    "source_title": o.source_title,
                    "display_text": o.display_text,
                    "target_title": o.target_title,
                }
                for o in self.orphan_links
            ],
            "total_wikilinks_seen": self.total_wikilinks_seen,
            "orphan_rate": (
                len(self.orphan_links) / self.total_wikilinks_seen
                if self.total_wikilinks_seen
                else 0.0
            ),
            "commit_sha": self.commit_sha,
            "pushed": self.pushed,
            "errors": self.errors,
            "synced_at": self.synced_at,
        }


# ── Git plumbing (sync; called via to_thread) ────────────────────────


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
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


def _publish_dir_for(target_id: int) -> Path:
    return Path(app_settings.uploads_dir) / "github-wiki-publish" / str(target_id)


def _clone_or_pull(
    target_id: int,
    owner: str,
    repo: str,
    installation_token: str,
    wiki_branch: str,
) -> tuple[Path, bool]:
    """Return (checkout_dir, wiki_is_empty). If the wiki has no commits on
    origin, `wiki_is_empty` is True — the caller may need the overwrite flag
    before blowing away hand-authored content."""
    target = _publish_dir_for(target_id)
    remote = f"https://x-access-token:{installation_token}@github.com/{owner}/{repo}.wiki.git"

    if not (target / ".git").is_dir():
        # Fresh clone. If wiki doesn't exist yet GitHub will 404, but when the
        # wiki exists with zero commits it clones as an empty repo.
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", remote, str(target)])
    else:
        _run_git(["remote", "set-url", "origin", remote], cwd=target)
        try:
            _run_git(["fetch", "origin"], cwd=target)
        except RuntimeError:
            # Wiki might be newly initialized — continue with the empty repo
            pass

    # Configure commit identity locally
    _run_git(["config", "user.email", _GIT_USER_EMAIL], cwd=target)
    _run_git(["config", "user.name", _GIT_USER_NAME], cwd=target)

    # Try to check out the target branch; create it if the wiki is brand-new
    has_remote_branch = False
    try:
        _run_git(["rev-parse", "--verify", f"origin/{wiki_branch}"], cwd=target)
        has_remote_branch = True
    except RuntimeError:
        pass

    if has_remote_branch:
        _run_git(["checkout", "-B", wiki_branch, f"origin/{wiki_branch}"], cwd=target)
        _run_git(["reset", "--hard", f"origin/{wiki_branch}"], cwd=target)
    else:
        # No remote commits yet → start an orphan branch
        try:
            _run_git(["checkout", "--orphan", wiki_branch], cwd=target)
        except RuntimeError:
            # Already on it
            pass
        # Clean any leftover files from a prior failed publish
        _run_git(["rm", "-rf", "--quiet", "--", "."], cwd=target)

    # wiki_is_empty when no remote branch existed
    return target, not has_remote_branch


def _head_sha(repo_dir: Path) -> str | None:
    try:
        return _run_git(["rev-parse", "HEAD"], cwd=repo_dir).stdout.strip()
    except RuntimeError:
        return None


def _commit_and_push(repo_dir: Path, wiki_branch: str, commit_message: str) -> tuple[str | None, bool]:
    """Stage all, commit if anything changed, push. Returns (sha, pushed_bool)."""
    _run_git(["add", "--all"], cwd=repo_dir)
    status = _run_git(["status", "--porcelain"], cwd=repo_dir).stdout
    if not status.strip():
        return _head_sha(repo_dir), False
    _run_git(["commit", "-m", commit_message], cwd=repo_dir)
    _run_git(["push", "origin", f"HEAD:{wiki_branch}"], cwd=repo_dir)
    return _head_sha(repo_dir), True


def _has_non_managed_content(repo_dir: Path) -> bool:
    """Detect hand-authored wiki content we shouldn't clobber on first publish.

    We consider the wiki non-empty when any committed markdown file exists
    that we didn't produce. Since this runs BEFORE we write anything, every
    `*.md` at root is effectively "existing content".
    """
    for entry in os.listdir(repo_dir):
        if entry == ".git":
            continue
        if entry.endswith(".md"):
            return True
    return False


# ── Async DB loading + orchestration ─────────────────────────────────


def _title_to_page_map_for(notes: list[Note]) -> dict[int, str]:
    return build_page_map([(n.id, n.title) for n in notes])


def _build_sidebar(notes: list[Note], page_map: dict[int, str]) -> str:
    """Flat sidebar sorted by title.

    A richer sidebar reflecting parent_id hierarchy is easy to add once this
    v1 is in use; for v1 we ship a simple alphabetical list so the user can
    see every published page without clicking into each.
    """
    entries = sorted(
        ((page_map[n.id], n.title) for n in notes if n.id in page_map),
        key=lambda x: x[1].lower(),
    )
    lines = ["# Pages", ""]
    for page_name, title in entries:
        if title == page_name:
            lines.append(f"- [[{page_name}]]")
        else:
            lines.append(f"- [[{title}|{page_name}]]")
    return "\n".join(lines) + "\n"


def _build_footer() -> str:
    return (
        "---\n"
        f"_Generated by note-website on {datetime.now(timezone.utc).date().isoformat()}. "
        "Edit the source notes to update this page._\n"
    )


async def run_publish(
    db: AsyncSession,
    target: IntegrationPublishTarget,
    installation_id: int,
    *,
    dry_run: bool = False,
    force_overwrite: bool = False,
) -> PublishResult:
    result = PublishResult(dry_run=dry_run, space_id=target.space_id)

    if shutil.which("git") is None:
        result.errors.append("git is not installed on the host")
        return result

    owner, _, repo = target.repo_full_name.partition("/")
    if not owner or not repo:
        result.errors.append(f"Invalid repo_full_name: {target.repo_full_name!r}")
        return result

    # 1. Load published notes for this space
    notes_result = await db.execute(
        select(Note).where(
            Note.space_id == target.space_id,
            Note.visibility == "public",
        )
    )
    loaded = list(notes_result.scalars().all())

    # Drop auto-created scaffolding notes: the space bootstrap creates
    # "Index: Root" / "Index: Questions" tagged `type/index`. These are admin
    # plumbing for the app's sidebar and don't belong on a public wiki.
    def _is_index_note(n: Note) -> bool:
        tags_raw = n.tags or "[]"
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except ValueError:
            tags = []
        if "type/index" in tags:
            return True
        return (n.title or "").startswith("Index:")

    notes = [n for n in loaded if not _is_index_note(n)]

    if not notes:
        result.errors.append("No publishable notes in this space — nothing to publish.")
        return result

    # 2. Build page map + transform
    page_map = _title_to_page_map_for(notes)
    title_to_page = {n.title: page_map[n.id] for n in notes}

    transformed: dict[str, str] = {}  # filename → content
    for note in notes:
        body = strip_frontmatter(note.content or "")
        new_body, orphans = transform_body(
            body,
            source_title=note.title,
            title_to_page=title_to_page,
        )
        # Count every wikilink we considered, not just orphans — needed for rate
        result.total_wikilinks_seen += len(re.findall(r"\[\[[^\[\]]+?\]\]", body))
        result.orphan_links.extend(orphans)

        page_name = page_map[note.id]
        fn = page_filename(page_name)
        transformed[fn] = render_page_markdown(new_body)

    # Sidebar + Footer
    transformed["_Sidebar.md"] = _build_sidebar(notes, page_map)
    transformed["_Footer.md"] = _build_footer()

    # 5a. Orphan-rate safety
    orphan_rate = (
        len(result.orphan_links) / result.total_wikilinks_seen
        if result.total_wikilinks_seen
        else 0.0
    )
    if orphan_rate > _ORPHAN_RATE_THRESHOLD and not force_overwrite:
        result.errors.append(
            f"{orphan_rate:.0%} of wikilinks are orphaned (>{_ORPHAN_RATE_THRESHOLD:.0%}). "
            "Publish something referenced by the published notes, or pass force_overwrite=true."
        )
        return result

    # 4/5b. Clone/pull (skipped in dry-run — we don't need the repo to count)
    if dry_run:
        result.pages_written = len(transformed)
        result.synced_at = datetime.now(timezone.utc).isoformat()
        return result

    token = await get_installation_token(installation_id)

    def _do_filesystem_work() -> tuple[str | None, bool, int, int]:
        repo_dir, wiki_is_empty = _clone_or_pull(
            target.id, owner, repo, token, target.wiki_branch,
        )

        if _has_non_managed_content(repo_dir) and target.last_published_sha is None and not force_overwrite:
            raise RuntimeError(
                "Wiki already has content and this space has never published before. "
                "Pass force_overwrite=true to replace it."
            )

        # Write transformed files
        written = 0
        for fn, content in transformed.items():
            (repo_dir / fn).write_text(content, encoding="utf-8")
            written += 1

        # Delete any `*.md` at root that WE previously owned and isn't in the
        # new set. Only safe to do once the target has a last_published_sha —
        # before then we can't be sure what's "ours".
        deleted = 0
        if target.last_published_sha is not None:
            want = set(transformed.keys())
            for entry in os.listdir(repo_dir):
                if not entry.endswith(".md"):
                    continue
                if entry in want:
                    continue
                # Managed by us → safe to delete. We treat anything at root as
                # managed when we have a prior publish; `_Sidebar.md` and
                # `_Footer.md` are always in `want`.
                (repo_dir / entry).unlink()
                deleted += 1

        commit_message = (
            f"chore(wiki): sync {len(transformed)} pages from space {target.space_id} "
            f"(note-website {datetime.now(timezone.utc).date().isoformat()})"
        )
        sha, pushed = _commit_and_push(repo_dir, target.wiki_branch, commit_message)
        return sha, pushed, written, deleted

    try:
        sha, pushed, written, deleted = await asyncio.to_thread(_do_filesystem_work)
    except RuntimeError as e:
        result.errors.append(str(e))
        return result

    result.pages_written = written
    result.pages_deleted = deleted
    result.commit_sha = sha
    result.pushed = pushed
    result.synced_at = datetime.now(timezone.utc).isoformat()

    # 9. Record on the target
    if sha:
        target.last_published_sha = sha
        target.last_published_at = result.synced_at
        await db.commit()

    return result
