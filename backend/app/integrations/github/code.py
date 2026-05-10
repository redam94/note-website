"""Ingest a GitHub repo's source files as `Document`s, feeding the existing
note-generation pipeline.

Phase 9, Option B, file-level. Each included source file becomes one
`Document` row; `run_processing_pipeline` (the same pipeline that handles
user uploads) turns it into one or more notes. Re-sync skips files whose
content hash matches the previous run's stored value.

Design choices:
- We clone the *main* repo (not `.wiki.git`) into `uploads/github-code-clones/{resource_id}/`.
- We copy each selected file into `uploads/github-code-files/{resource_id}/`
  with a stable per-file name so the pipeline has exclusive ownership of its
  input file (clone dir gets wiped on re-sync).
- The Document's `original_name` is the repo-relative path (e.g. `src/foo.py`).
  That's the natural key for upserts; combined with `space_id` it identifies
  one ingested file uniquely.
- Content hashes live in `integration_resources.config_json['code_hashes']`
  as `{path: "sha256:..."}`, so lookups are O(1) and don't need a schema change.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings as app_settings
from ...models.document import Document
from ...models.extraction_profile import ExtractionProfile
from ...models.integration_resource import IntegrationResource
from ...worker import run_processing_pipeline
from .auth import get_installation_token

logger = logging.getLogger(__name__)

# Directories we don't walk into regardless of extension filters.
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "dist", "build", ".next", ".cache", "target", "venv",
    ".venv", "env", ".tox",
}
# Filenames to skip even with a matching extension.
_SKIP_FILES = {
    "package-lock.json", "yarn.lock", "poetry.lock", "uv.lock", "Cargo.lock",
    "Gemfile.lock", "pnpm-lock.yaml",
}

_DEFAULT_MAX_FILE_BYTES = 200_000  # 200KB per file
_DEFAULT_MAX_FILES = 500


@dataclass
class CodeSyncResult:
    ingested: int = 0
    updated: int = 0
    skipped: int = 0  # content unchanged
    skipped_too_large: int = 0  # size-cap exceeded
    deleted: int = 0
    errors: list[str] = field(default_factory=list)


# ── Git ops (sync; called via asyncio.to_thread) ─────────────────────


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}"
        )
    return result


def _clone_dir_for(resource_id: int) -> Path:
    return Path(app_settings.uploads_dir) / "github-code-clones" / str(resource_id)


def _files_dir_for(resource_id: int) -> Path:
    return Path(app_settings.uploads_dir) / "github-code-files" / str(resource_id)


async def _pull_main_repo(
    resource_id: int,
    owner: str,
    repo: str,
    installation_token: str,
) -> Path:
    """Shallow clone or fast-forward the main repo. Returns the checkout dir."""
    target = _clone_dir_for(resource_id)
    remote = f"https://x-access-token:{installation_token}@github.com/{owner}/{repo}.git"

    def _work() -> Path:
        if (target / ".git").is_dir():
            _run_git(["remote", "set-url", "origin", remote], cwd=target)
            _run_git(["fetch", "--depth", "1", "origin"], cwd=target)
                    # Resolve default branch from origin/HEAD
            try:
                _run_git(["remote", "set-head", "origin", "--auto"], cwd=target)
                head = _run_git(
                    ["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=target
                ).stdout.strip()
                branch = head.rsplit("/", 1)[-1] if head else "main"
            except RuntimeError:
                branch = "main"
            # Depth-1 fetch only brings the tip; reset works on it.
            _run_git(["reset", "--hard", f"origin/{branch}"], cwd=target)
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        _run_git(["clone", "--depth", "1", remote, str(target)])
        return target

    return await asyncio.to_thread(_work)


# ── File walk + filtering ────────────────────────────────────────────


def _walk_candidate_files(
    repo_dir: Path,
    include_exts: set[str],
    max_file_bytes: int,
    max_files: int,
) -> tuple[list[tuple[str, Path, int]], int]:
    """Return (candidates, skipped_too_large_count). Filters by:
    - skip dirs in _SKIP_DIRS
    - skip filenames in _SKIP_FILES
    - extension in include_exts
    - 0 < size <= max_file_bytes
    - stop once we hit max_files
    """
    out: list[tuple[str, Path, int]] = []
    skipped_too_large = 0
    repo_dir = repo_dir.resolve()
    for root, dirs, files in os.walk(repo_dir):
        # Prune skipped dirs in-place so os.walk doesn't descend into them
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in files:
            if fname in _SKIP_FILES:
                continue
            _, _, ext = fname.rpartition(".")
            if not ext or ext.lower() not in include_exts:
                continue
            abs_path = Path(root) / fname
            try:
                size = abs_path.stat().st_size
            except OSError:
                continue
            if size == 0:
                continue
            if size > max_file_bytes:
                skipped_too_large += 1
                continue
            rel = str(abs_path.relative_to(repo_dir))
            out.append((rel, abs_path, size))
            if len(out) >= max_files:
                return out, skipped_too_large
    return out, skipped_too_large


def _content_hash(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _stable_dest_filename(resource_id: int, rel_path: str, ext: str) -> str:
    """Stable, flat-namespace filename in the per-resource files dir.

    `{hash(rel_path)}.{ext}` — the same repo path always produces the same
    dest filename so re-syncs overwrite in place.
    """
    h = hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16]
    return f"{h}.{ext}"


# ── Main entry ───────────────────────────────────────────────────────


async def _resolve_profile_for_file(
    db: AsyncSession,
    rel_path: str,
    space_id: int,
    preferred_profile_id: int | None,
) -> ExtractionProfile | None:
    """Pick an extraction profile.

    The admin sets `preferred_profile_id` deliberately, so honor it regardless
    of which space it lives in — repo sync can pull from any installed profile.

    For extension fallback, prefer a profile installed in the target space
    first; only if none matches there do we scan the rest. That keeps the
    target space's choices authoritative for auto-matched files but still
    lets a single shared profile cover repos targeting spaces that don't
    have their own copy installed.
    """
    if preferred_profile_id is not None:
        result = await db.execute(
            select(ExtractionProfile).where(ExtractionProfile.id == preferred_profile_id)
        )
        found = result.scalar_one_or_none()
        if found is not None:
            return found

    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else ""
    if not ext:
        return None

    def _matches(profile: ExtractionProfile) -> bool:
        try:
            exts = json.loads(profile.extensions or "[]")
        except ValueError:
            return False
        return ext in [e.lstrip(".").lower() for e in exts]

    same_space = await db.execute(
        select(ExtractionProfile).where(ExtractionProfile.space_id == space_id)
    )
    for profile in same_space.scalars().all():
        if _matches(profile):
            return profile

    any_space = await db.execute(
        select(ExtractionProfile).where(ExtractionProfile.space_id != space_id)
    )
    for profile in any_space.scalars().all():
        if _matches(profile):
            return profile
    return None


async def _upsert_document(
    db: AsyncSession,
    *,
    rel_path: str,
    content: bytes,
    space_id: int,
    resource_id: int,
) -> tuple[Document, str, str]:
    """Upsert a Document for one code file. Returns (doc, abs_file_path, outcome).

    outcome ∈ {"ingested", "updated"}.

    `doc.filename` stores the path RELATIVE to settings.uploads_dir so the
    existing batch/resume code paths (`os.path.join(uploads_dir, doc.filename)`)
    keep working. The absolute path we return is what we'll pass to the
    pipeline directly.
    """
    result = await db.execute(
        select(Document).where(
            Document.space_id == space_id,
            Document.original_name == rel_path,
        )
    )
    existing = result.scalar_one_or_none()

    ext = rel_path.rsplit(".", 1)[-1].lower() if "." in rel_path else "txt"
    # Plain-text mime so the existing text parser handles it.
    mime_type = "text/plain"

    dest_basename = _stable_dest_filename(resource_id, rel_path, ext)
    files_dir = _files_dir_for(resource_id)
    files_dir.mkdir(parents=True, exist_ok=True)
    abs_path = (files_dir / dest_basename).resolve()
    abs_path.write_bytes(content)

    # Relative-to-uploads_dir so it matches the upload-flow convention.
    # Both paths resolved so symlinks (e.g. /tmp → /private/tmp on macOS)
    # don't trip up relative_to.
    uploads_root = Path(app_settings.uploads_dir).resolve()
    rel_from_uploads = str(abs_path.relative_to(uploads_root))

    now_iso = datetime.now(timezone.utc).isoformat()

    if existing is None:
        doc = Document(
            filename=rel_from_uploads,
            original_name=rel_path,
            mime_type=mime_type,
            content_raw=content.decode("utf-8", errors="replace"),
            status="pending",
            created_at=now_iso,
            space_id=space_id,
        )
        db.add(doc)
        await db.flush()  # get doc.id before returning
        return doc, str(abs_path), "ingested"

    existing.filename = rel_from_uploads
    existing.mime_type = mime_type
    existing.content_raw = content.decode("utf-8", errors="replace")
    existing.status = "pending"
    existing.processing_step = None
    existing.error = None
    # Force reprocess — the pipeline will recreate notes attached to this doc
    return existing, str(abs_path), "updated"


async def _delete_document_for_path(
    db: AsyncSession, *, rel_path: str, space_id: int
) -> bool:
    result = await db.execute(
        select(Document).where(
            Document.space_id == space_id,
            Document.original_name == rel_path,
        )
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        return False
    # Notes cascade via FK
    await db.delete(doc)
    return True


async def sync_code(
    db: AsyncSession,
    resource: IntegrationResource,
    installation_id: int,
    *,
    background_tasks: BackgroundTasks | None = None,
) -> CodeSyncResult:
    """Clone/pull the repo, upsert one Document per selected file, enqueue
    the note-generation pipeline for each ingested/updated file."""
    result = CodeSyncResult()

    if shutil.which("git") is None:
        result.errors.append("git is not installed on the host")
        return result
    if not resource.name or "/" not in resource.name:
        result.errors.append(f"Invalid repo name: {resource.name!r}")
        return result
    if resource.space_id is None:
        result.errors.append("No target space configured")
        return result

    try:
        cfg = json.loads(resource.config_json or "{}")
    except ValueError:
        cfg = {}

    include_exts_raw = cfg.get("code_include_exts") or []
    include_exts = {e.lstrip(".").lower() for e in include_exts_raw if isinstance(e, str)}
    if not include_exts:
        result.errors.append(
            "No code_include_exts configured — add extensions (e.g. ['py', 'ts']) before syncing."
        )
        return result
    max_file_bytes = int(cfg.get("code_max_file_bytes") or _DEFAULT_MAX_FILE_BYTES)
    max_files = int(cfg.get("code_max_files") or _DEFAULT_MAX_FILES)
    preferred_profile_id = cfg.get("code_extraction_profile_id")
    stored_hashes: dict[str, str] = cfg.get("code_hashes") or {}

    owner, _, repo = resource.name.partition("/")
    token = await get_installation_token(installation_id)

    try:
        clone_dir = await _pull_main_repo(resource.id, owner, repo, token)
    except RuntimeError as e:
        result.errors.append(f"Clone failed: {e}")
        return result

    candidates, skipped_too_large = _walk_candidate_files(
        clone_dir, include_exts, max_file_bytes, max_files
    )
    result.skipped_too_large = skipped_too_large
    current_paths = {rel for rel, _, _ in candidates}

    # Per-file processing
    new_hashes: dict[str, str] = {}
    to_enqueue: list[tuple[int, str, str, str, int | None]] = []  # (doc_id, file_path, mime, original_name, profile_id)

    for rel, abs_path, _size in candidates:
        try:
            content = abs_path.read_bytes()
        except OSError as e:
            result.errors.append(f"{rel}: {e}")
            continue

        sha = _content_hash(content)
        if stored_hashes.get(rel) == sha:
            # Content unchanged and we've ingested before — skip pipeline run,
            # carry the existing hash forward, keep the Document row as-is.
            new_hashes[rel] = sha
            result.skipped += 1
            continue

        try:
            profile = await _resolve_profile_for_file(
                db, rel, resource.space_id, preferred_profile_id
            )
            doc, dest_path, outcome = await _upsert_document(
                db,
                rel_path=rel,
                content=content,
                space_id=resource.space_id,
                resource_id=resource.id,
            )
        except Exception as e:
            logger.exception("code upsert failed for %s", rel)
            result.errors.append(f"{rel}: {e}")
            # Do NOT record the hash — next sync should retry this file.
            continue

        new_hashes[rel] = sha
        to_enqueue.append(
            (doc.id, dest_path, doc.mime_type, rel, profile.id if profile else None)
        )
        if outcome == "ingested":
            result.ingested += 1
        else:
            result.updated += 1

    # Delete Documents for files no longer present upstream
    if not result.errors:
        for rel in list(stored_hashes.keys()):
            if rel in current_paths:
                continue
            try:
                if await _delete_document_for_path(
                    db, rel_path=rel, space_id=resource.space_id
                ):
                    result.deleted += 1
            except Exception as e:
                logger.exception("code delete failed for %s", rel)
                result.errors.append(f"delete {rel}: {e}")

    # Persist updated hash map back to the resource config. Only keep hashes
    # for paths we actually persisted this run or already had from before —
    # failed rows (errored upsert) intentionally don't get an entry so next
    # run retries them.
    cfg["code_hashes"] = {
        path: new_hashes[path]
        for path in current_paths
        if path in new_hashes
    }
    resource.config_json = json.dumps(cfg)
    resource.last_synced_at = datetime.now(timezone.utc).isoformat()

    # Commit docs + resource config BEFORE enqueuing — pipeline reads these
    await db.commit()

    # Fire off the pipeline for each ingested/updated doc. BackgroundTasks
    # runs after the response is sent; the admin polls /api/documents to see
    # progress as notes materialize.
    if background_tasks is not None:
        for doc_id, file_path, mime_type, original_name, profile_id in to_enqueue:
            background_tasks.add_task(
                run_processing_pipeline,
                doc_id,
                file_path,
                mime_type,
                original_name,
                resource.space_id,
                profile_id,
                resource.name,
            )
    else:
        # Sync fallback (tests / CLI): run inline. Caller is responsible for
        # the cost. In production, always thread BackgroundTasks through.
        for doc_id, file_path, mime_type, original_name, profile_id in to_enqueue:
            try:
                await run_processing_pipeline(
                    doc_id, file_path, mime_type, original_name,
                    resource.space_id, profile_id, resource.name,
                )
            except Exception as e:
                logger.exception("inline pipeline failed for doc %d", doc_id)
                result.errors.append(f"{original_name}: pipeline {e}")

    return result
