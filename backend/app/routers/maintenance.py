"""Note maintenance endpoints — repair, reindex, and audit operations.

Long-running operations (repair-shallow, reindex) run as background tasks
and store results in a module-level dict that the frontend can poll.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, Depends
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import async_session, get_db
from ..dependencies import get_current_space
from ..models.document import Document
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.space import Space
from ..models.subgraph_node import SubgraphNode
from ..prompts import load_prompt
from ..services.model_provider import get_provider, get_setting

router = APIRouter(prefix="/api/maintenance", dependencies=[Depends(require_admin)])

_plan_prompt = load_prompt("repair_plan")
_execute_prompt = load_prompt("repair_execute")

# In-memory job status for background tasks
_jobs: dict[str, dict] = {}


# ── Schemas ───────────────────────────────────────────────────────────


class AuditResult(BaseModel):
    total_notes: int
    missing_frontmatter: list[dict]
    shallow_notes: list[dict]
    orphan_notes: list[dict]
    broken_links: list[dict]
    missing_indexes: list[str]


class RepairRequest(BaseModel):
    note_id: int


class RepairResult(BaseModel):
    note_id: int
    title: str
    changes: list[str]
    new_links: list[str]


class ReindexResult(BaseModel):
    indexes_updated: int
    indexes_created: int
    details: list[str]


class JobStatus(BaseModel):
    job_id: str
    status: str  # "running", "done", "error"
    progress: str
    result: dict | None


# ── Job status polling ────────────────────────────────────────────────


@router.get("/jobs/{job_id}")
async def get_job_status(job_id: str) -> JobStatus:
    job = _jobs.get(job_id)
    if not job:
        return JobStatus(job_id=job_id, status="not_found", progress="", result=None)
    return JobStatus(**job)


# ── Audit (fast, no LLM) ─────────────────────────────────────────────


@router.get("/audit")
async def audit_vault(db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)) -> AuditResult:
    result = await db.execute(select(Note).where(Note.space_id == current_space.id))
    all_notes = result.scalars().all()

    # Get edges that involve notes in this space
    space_note_ids_result = await db.execute(select(Note.id).where(Note.space_id == current_space.id))
    space_note_ids = set(space_note_ids_result.scalars().all())
    edges_result = await db.execute(
        select(GraphEdge).where(
            or_(GraphEdge.source_id.in_(space_note_ids), GraphEdge.target_id.in_(space_note_ids))
        )
    )
    all_edges = edges_result.scalars().all()

    note_ids = {n.id for n in all_notes}
    linked_ids = set()
    for e in all_edges:
        linked_ids.add(e.source_id)
        linked_ids.add(e.target_id)
    for n in all_notes:
        if n.parent_id:
            linked_ids.add(n.id)
            linked_ids.add(n.parent_id)

    missing_fm = []
    shallow = []
    orphans = []
    broken = []

    all_titles = {n.title for n in all_notes}

    for n in all_notes:
        content = n.content or ""

        # Frontmatter check
        issues = []
        if not content.startswith("---"):
            issues.append("no frontmatter")
        else:
            fm = content.split("---")[1] if "---" in content[3:] else ""
            for field in ["depends_on", "used_by", "doc_type", "folder"]:
                if field not in fm:
                    issues.append(f"missing {field}")
        if issues:
            missing_fm.append({"id": n.id, "title": n.title, "issues": issues})

        # Shallow check
        body = re.sub(r"^---[\s\S]*?---\n*", "", content)
        word_count = len(body.split())
        if word_count < 150:
            shallow.append({"id": n.id, "title": n.title, "words": word_count})

        # Orphan check
        if n.id not in linked_ids:
            orphans.append({"id": n.id, "title": n.title, "slug": n.slug})

        # Broken wiki-links
        wiki_links = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", content)
        for target in wiki_links:
            if target not in all_titles and not target.startswith("raw/"):
                broken.append({"note_id": n.id, "note_title": n.title, "broken_link": target})

    # Missing indexes
    folders = set()
    for n in all_notes:
        fm_match = re.search(r'folder:\s*"?([^"\n]+)"?', n.content or "")
        if fm_match:
            parts = fm_match.group(1).strip().split("/")
            for i in range(len(parts)):
                folders.add("/".join(parts[: i + 1]))

    index_titles = {n.title for n in all_notes if n.title.startswith("Index:")}
    missing_indexes = [f for f in sorted(folders) if f and f"Index: {f}" not in index_titles]

    return AuditResult(
        total_notes=len(all_notes),
        missing_frontmatter=missing_fm[:50],
        shallow_notes=shallow[:50],
        orphan_notes=orphans[:50],
        broken_links=broken[:50],
        missing_indexes=missing_indexes[:30],
    )


# ── Repair Single Note (two-phase: Sonnet plans, Haiku executes) ──────


@router.post("/repair")
async def repair_note(
    body: RepairRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> RepairResult:
    result = await db.execute(select(Note).where(Note.id == body.note_id).where(Note.space_id == current_space.id))
    note = result.scalar_one_or_none()
    if not note:
        return RepairResult(note_id=body.note_id, title="Not found", changes=[], new_links=[])

    titles_result = await db.execute(select(Note.title).where(Note.id != note.id).where(Note.space_id == current_space.id))
    all_titles = [t for (t,) in titles_result.all()]

    # Get context from related notes (neighbors) for enrichment
    edges_result = await db.execute(
        select(GraphEdge).where(
            or_(GraphEdge.source_id == note.id, GraphEdge.target_id == note.id)
        )
    )
    neighbor_ids = set()
    for e in edges_result.scalars().all():
        neighbor_ids.add(e.source_id if e.source_id != note.id else e.target_id)
    if note.parent_id:
        neighbor_ids.add(note.parent_id)

    neighbor_context = ""
    if neighbor_ids:
        neighbors_result = await db.execute(
            select(Note).where(Note.id.in_(neighbor_ids))
        )
        neighbor_summaries = []
        for n in neighbors_result.scalars().all():
            summary = n.summary or n.content[:300]
            neighbor_summaries.append(f"[[{n.title}]]: {summary}")
        neighbor_context = "\n".join(neighbor_summaries[:10])

    provider = await get_provider(db)
    model = await get_setting(db, "model_create")
    titles_text = "\n".join(f"- {t}" for t in all_titles[:60])

    # ── Phase 1: Sonnet diagnoses and plans the repair ────────────
    plan_prompt = (
        f"## Note to analyze:\n\n{note.content}\n\n"
        f"## Related notes context:\n{neighbor_context}\n\n"
        f"## Available notes for cross-linking:\n{titles_text}"
    )

    try:
        plan_response = await provider.complete(
            messages=[{"role": "user", "content": plan_prompt}],
            system=_plan_prompt.format(),
            max_tokens=2048,
            model=model,
        )
        json_match = re.search(r"\{.*\}", plan_response, re.DOTALL)
        repair_plan = json.loads(json_match.group()) if json_match else json.loads(plan_response)
    except Exception as e:
        return RepairResult(
            note_id=note.id, title=note.title,
            changes=[f"Planning failed: {str(e)}"], new_links=[],
        )

    issues = repair_plan.get("issues_found", [])
    if not issues:
        return RepairResult(
            note_id=note.id, title=note.title,
            changes=["No issues found"], new_links=[],
        )

    # ── Phase 2: Haiku executes the repair plan ───────────────────
    execute_prompt = (
        f"## Current note:\n\n{note.content}\n\n"
        f"## Repair plan to follow:\n{json.dumps(repair_plan, indent=2)}\n\n"
        f"## Related notes for context:\n{neighbor_context}\n\n"
        f"## Available notes for [[wiki-links]]:\n{titles_text}\n\n"
        "Rewrite the note according to the repair plan. Include all fixes."
    )

    try:
        new_content = await provider.complete(
            messages=[{"role": "user", "content": execute_prompt}],
            system=_execute_prompt.format(),
            max_tokens=8192,
            model=model,
        )

        # Validate response looks like a note
        stripped = new_content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```\w*\n?", "", stripped)
            stripped = re.sub(r"\n?```$", "", stripped)

        if stripped.startswith("---") or stripped.startswith("#"):
            note.content = stripped
            await db.commit()

            links_to_insert = repair_plan.get("links_to_insert", [])
            return RepairResult(
                note_id=note.id, title=note.title,
                changes=issues, new_links=links_to_insert,
            )
        else:
            return RepairResult(
                note_id=note.id, title=note.title,
                changes=["Execution produced invalid output"], new_links=[],
            )
    except Exception as e:
        return RepairResult(
            note_id=note.id, title=note.title,
            changes=[f"Execution failed: {str(e)}"], new_links=[],
        )


# ── Fix Cross-References (fast, no LLM) ──────────────────────────────


@router.post("/fix-links")
async def fix_cross_references(db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)) -> dict:
    result = await db.execute(select(Note).where(Note.space_id == current_space.id))
    all_notes = result.scalars().all()
    title_to_note = {n.title: n for n in all_notes}

    fixes = 0
    for note in all_notes:
        content = note.content or ""
        fm_match = re.match(r"^---\n([\s\S]*?)\n---", content)
        if not fm_match:
            continue

        depends = re.findall(r'depends_on:.*?(?=\n\w|\n---|\Z)', fm_match.group(1), re.DOTALL)
        dep_titles = re.findall(r'\[\[([^\]]+)\]\]', "".join(depends))

        for dep_title in dep_titles:
            target = title_to_note.get(dep_title)
            if not target:
                continue
            target_content = target.content or ""
            if f"[[{note.title}]]" not in target_content:
                if "used_by:" in target_content:
                    target.content = target_content.replace(
                        "used_by:", f'used_by:\n  - "[[{note.title}]]"', 1
                    )
                    fixes += 1

    await db.commit()
    return {"fixes_applied": fixes, "notes_scanned": len(all_notes)}


# ── Reindex (background task) ─────────────────────────────────────────


@router.post("/reindex")
async def reindex_vault(background_tasks: BackgroundTasks, current_space: Space = Depends(get_current_space)) -> dict:
    job_id = f"reindex-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    _jobs[job_id] = {"job_id": job_id, "status": "running", "progress": "Starting...", "result": None}
    background_tasks.add_task(_run_reindex, job_id, current_space.id)
    return {"job_id": job_id, "status": "started"}


async def _run_reindex(job_id: str, space_id: int):
    try:
        async with async_session() as db:
            result = await db.execute(select(Note).where(Note.space_id == space_id))
            all_notes = result.scalars().all()

        # Pass 0: Load community detection clusters to inform folder assignment
        _jobs[job_id]["progress"] = "Loading community clusters..."
        cluster_folder_map: dict[int, str] = {}  # note_id -> cluster-derived folder
        async with async_session() as db:
            clusters_result = await db.execute(select(SubgraphNode).where(SubgraphNode.space_id == space_id))
            clusters = clusters_result.scalars().all()

        if clusters:
            for cluster in clusters:
                member_ids = json.loads(cluster.member_node_ids) if cluster.member_node_ids else []
                # Use the hierarchical path if available, otherwise the label
                folder_path = (cluster.path or cluster.label or "Uncategorized").strip()
                for nid in member_ids:
                    # Keep the deepest (most specific) cluster path per note
                    existing = cluster_folder_map.get(nid, "")
                    if len(folder_path) > len(existing):
                        cluster_folder_map[nid] = folder_path

        # Map notes to their folder paths from frontmatter, falling back to cluster labels
        note_folders: dict[int, str] = {}  # note_id -> folder_path
        folder_notes: dict[str, list] = {}  # folder_path -> [notes]
        for n in all_notes:
            if n.title.startswith("Index:"):
                continue  # Skip index notes themselves
            if n.title.startswith("Q: "):
                continue  # Q&A notes handled separately
            fm_match = re.search(r'folder:\s*"?([^"\n]+)"?', n.content or "")
            folder = fm_match.group(1).strip() if fm_match else ""
            # Fall back to community cluster label if no folder in frontmatter
            if not folder and n.id in cluster_folder_map:
                folder = cluster_folder_map[n.id]
            if folder:
                note_folders[n.id] = folder
                # Ensure all ancestor paths are tracked
                parts = folder.split("/")
                for i in range(len(parts)):
                    path = "/".join(parts[: i + 1])
                    folder_notes.setdefault(path, []).append(n)

        async with async_session() as db:
            provider = await get_provider(db)
            index_model = await get_setting(db, "model_index")
            slugs_result = await db.execute(select(Note.slug))
            existing_slugs = set(slugs_result.scalars().all())
            existing_indexes: dict[str, Note] = {}
            for n in all_notes:
                if n.title.startswith("Index:"):
                    path = n.title.removeprefix("Index: ").strip()
                    existing_indexes[path] = n

        from ..prompts import load_prompt
        index_system = load_prompt("index_gen").format()

        created = 0
        updated = 0
        details = []
        now = datetime.now(timezone.utc)

        # Find Root index to parent top-level folders under it
        root_id: int | None = None
        async with async_session() as db:
            root_result = await db.execute(
                select(Note).where(Note.title == "Index: Root").where(Note.space_id == space_id)
            )
            root_note = root_result.scalar_one_or_none()
            if root_note:
                root_id = root_note.id

        # Sort by depth so parents are created before children
        all_folder_paths = sorted(folder_notes.keys(), key=lambda p: (p.count("/"), p))
        total_folders = len(all_folder_paths)
        path_to_id: dict[str, int] = {}

        # Pass 1: Create/update index notes with proper parent_id chains
        for done, folder_path in enumerate(all_folder_paths):
            _jobs[job_id]["progress"] = f"Indexing {folder_path}... ({done}/{total_folders})"

            notes = folder_notes[folder_path]
            index_title = f"Index: {folder_path}"
            children_desc = "\n".join(f"- {n.title}" for n in notes[:20])

            # Determine parent index — top-level folders go under Root
            parts = folder_path.split("/")
            parent_path = "/".join(parts[:-1]) if len(parts) > 1 else None
            parent_id = path_to_id.get(parent_path) if parent_path else root_id

            try:
                response = await provider.complete(
                    messages=[{"role": "user", "content": f"Folder: {folder_path}\nNotes ({len(notes)}):\n{children_desc}"}],
                    system=index_system,
                    max_tokens=2048,
                    model=index_model,
                )
                json_match = re.search(r"\{.*\}", response, re.DOTALL)
                index_data = json.loads(json_match.group()) if json_match else json.loads(response)
            except Exception:
                index_data = {"summary": f"Index for {folder_path}", "content": f"## Notes\n{children_desc}"}

            leaf_name = parts[-1]
            frontmatter = f"---\ntitle: \"{index_title}\"\ntags:\n  - type/index\ndate_updated: {now.strftime('%Y-%m-%d')}\nconcept_count: {len(notes)}\n---"
            full_content = f"{frontmatter}\n\n# {leaf_name}\n\n{index_data.get('content', '')}"

            async with async_session() as db:
                if folder_path in existing_indexes:
                    existing = await db.execute(select(Note).where(Note.title == index_title).where(Note.space_id == space_id))
                    idx_note = existing.scalar_one_or_none()
                    if idx_note:
                        idx_note.content = full_content
                        idx_note.summary = index_data.get("summary")
                        idx_note.parent_id = parent_id
                        path_to_id[folder_path] = idx_note.id
                        updated += 1
                        details.append(f"Updated: {index_title}")
                else:
                    slug = slugify(index_title, lowercase=True)
                    c = 1
                    while slug in existing_slugs:
                        slug = f"{slugify(index_title, lowercase=True)}-{c}"
                        c += 1
                    existing_slugs.add(slug)

                    new_note = Note(
                        document_id=None,
                        parent_id=parent_id,
                        title=index_title,
                        content=full_content,
                        slug=slug,
                        tags=json.dumps(["type/index"]),
                        level=0,
                        created_at=now.isoformat(),
                        summary=index_data.get("summary"),
                        space_id=space_id,
                    )
                    db.add(new_note)
                    await db.commit()
                    await db.refresh(new_note)
                    path_to_id[folder_path] = new_note.id
                    created += 1
                    details.append(f"Created: {index_title}")
                    continue  # already committed
                await db.commit()

        # Pass 2: Re-parent content notes to their folder's index note
        reparented = 0
        _jobs[job_id]["progress"] = "Re-parenting content notes into topic tree..."
        async with async_session() as db:
            for note_id, folder_path in note_folders.items():
                index_id = path_to_id.get(folder_path)
                if index_id:
                    await db.execute(
                        update(Note)
                        .where(and_(Note.id == note_id, or_(Note.parent_id != index_id, Note.parent_id.is_(None))))
                        .values(parent_id=index_id)
                    )
                    reparented += 1
            await db.commit()

        details.append(f"Re-parented {reparented} content notes into topic tree")

        # Pass 3: Remove orphaned index notes (indexes with no children)
        _jobs[job_id]["progress"] = "Cleaning up orphaned index notes..."
        cleaned = 0
        async with async_session() as db:
            idx_result = await db.execute(
                select(Note).where(Note.title.like("Index: %")).where(Note.space_id == space_id)
            )
            all_indexes = idx_result.scalars().all()
            all_index_ids = {n.id for n in all_indexes}

            for idx_note in all_indexes:
                # Check if this index has any children
                children_result = await db.execute(
                    select(func.count()).select_from(Note).where(Note.parent_id == idx_note.id)
                )
                child_count = children_result.scalar()
                if child_count == 0:
                    # Also delete any graph edges referencing this note
                    await db.execute(
                        GraphEdge.__table__.delete().where(
                            or_(GraphEdge.source_id == idx_note.id, GraphEdge.target_id == idx_note.id)
                        )
                    )
                    await db.execute(
                        Note.__table__.delete().where(Note.id == idx_note.id)
                    )
                    cleaned += 1
                    details.append(f"Removed orphan: {idx_note.title}")
            await db.commit()

        if cleaned:
            details.append(f"Cleaned up {cleaned} orphaned index notes")

        # Pass 4: Ensure "Questions" folder exists for Q&A notes
        _jobs[job_id]["progress"] = "Ensuring Questions folder..."
        async with async_session() as db:
            q_idx_result = await db.execute(
                select(Note).where(Note.title == "Index: Questions").where(Note.space_id == space_id)
            )
            q_idx = q_idx_result.scalar_one_or_none()
            if not q_idx:
                q_slug = "index-questions"
                c = 1
                while q_slug in existing_slugs:
                    q_slug = f"index-questions-{c}"
                    c += 1
                existing_slugs.add(q_slug)
                q_idx = Note(
                    document_id=None, parent_id=None,
                    title="Index: Questions",
                    content=f"---\ntitle: \"Index: Questions\"\ntags:\n  - type/index\ndate_updated: {now.strftime('%Y-%m-%d')}\n---\n\n# Questions\n\n> Saved Q&A answers from the knowledge base.",
                    slug=q_slug, tags=json.dumps(["type/index"]),
                    level=0, created_at=now.isoformat(),
                    summary="Saved Q&A answers",
                    space_id=space_id,
                )
                db.add(q_idx)
                await db.commit()
                await db.refresh(q_idx)
                created += 1
                details.append("Created: Index: Questions")

            # Re-parent Q&A notes into Questions folder
            qa_result = await db.execute(
                select(Note).where(Note.title.like("Q: %")).where(Note.space_id == space_id)
            )
            qa_reparented = 0
            for qa_note in qa_result.scalars().all():
                if qa_note.parent_id != q_idx.id:
                    qa_note.parent_id = q_idx.id
                    qa_reparented += 1
            if qa_reparented:
                await db.commit()
                details.append(f"Re-parented {qa_reparented} Q&A notes into Questions folder")

        # Pass 5: Create/update root index
        _jobs[job_id]["progress"] = "Building root index..."
        async with async_session() as db:
            # Find all root-level index notes (no parent)
            root_children_result = await db.execute(
                select(Note).where(
                    and_(Note.parent_id.is_(None), Note.title.like("Index: %"), Note.title != "Index: Root", Note.space_id == space_id)
                )
            )
            root_children = root_children_result.scalars().all()

            # Also find root-level content notes (no parent, not indexes, not Q&A in Questions)
            orphan_content_result = await db.execute(
                select(Note).where(
                    and_(
                        Note.parent_id.is_(None),
                        ~Note.title.like("Index: %"),
                        Note.title != "Index: Root",
                        Note.space_id == space_id,
                    )
                )
            )
            orphan_content = orphan_content_result.scalars().all()

            all_root_items = root_children + orphan_content
            children_desc = "\n".join(f"- {n.title}" for n in all_root_items[:30])

            try:
                response = await provider.complete(
                    messages=[{"role": "user", "content": f"Root of knowledge base\nTop-level items ({len(all_root_items)}):\n{children_desc}"}],
                    system=index_system,
                    max_tokens=2048,
                    model=index_model,
                )
                json_match = re.search(r"\{.*\}", response, re.DOTALL)
                root_data = json.loads(json_match.group()) if json_match else json.loads(response)
            except Exception:
                root_data = {"summary": f"Knowledge base with {len(all_root_items)} topics", "content": f"## Topics\n{children_desc}"}

            root_fm = f"---\ntitle: \"Index: Root\"\ntags:\n  - type/index\ndate_updated: {now.strftime('%Y-%m-%d')}\nconcept_count: {len(all_root_items)}\n---"
            root_content = f"{root_fm}\n\n# Knowledge Base\n\n{root_data.get('content', '')}"

            root_result = await db.execute(select(Note).where(Note.title == "Index: Root").where(Note.space_id == space_id))
            root_note = root_result.scalar_one_or_none()

            if root_note:
                root_note.content = root_content
                root_note.summary = root_data.get("summary")
                updated += 1
                details.append("Updated: Index: Root")
            else:
                r_slug = "index-root"
                c = 1
                while r_slug in existing_slugs:
                    r_slug = f"index-root-{c}"
                    c += 1
                root_note = Note(
                    document_id=None, parent_id=None,
                    title="Index: Root",
                    content=root_content, slug=r_slug,
                    tags=json.dumps(["type/index"]),
                    level=0, created_at=now.isoformat(),
                    summary=root_data.get("summary"),
                    space_id=space_id,
                )
                db.add(root_note)
                await db.commit()
                await db.refresh(root_note)
                created += 1
                details.append("Created: Index: Root")

            # Re-parent root-level indexes under root note
            for child in root_children:
                if child.parent_id != root_note.id:
                    child.parent_id = root_note.id

            await db.commit()

        _jobs[job_id] = {
            "job_id": job_id,
            "status": "done",
            "progress": f"Complete: {created} created, {updated} updated, {reparented} re-parented, {cleaned} cleaned",
            "result": {"indexes_created": created, "indexes_updated": updated, "details": details},
        }
    except Exception as e:
        _jobs[job_id] = {"job_id": job_id, "status": "error", "progress": str(e), "result": None}


# ── Cleanup Orphaned Index Notes ─────────────────────────────────────


@router.post("/cleanup-indexes")
async def cleanup_orphaned_indexes(db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)) -> dict:
    """Remove index notes that have no children (orphaned by re-parenting)."""
    idx_result = await db.execute(
        select(Note).where(Note.title.like("Index: %")).where(Note.space_id == current_space.id)
    )
    all_indexes = idx_result.scalars().all()

    removed = []
    for idx_note in all_indexes:
        children_result = await db.execute(
            select(func.count()).select_from(Note).where(Note.parent_id == idx_note.id)
        )
        child_count = children_result.scalar()
        if child_count == 0:
            await db.execute(
                GraphEdge.__table__.delete().where(
                    or_(GraphEdge.source_id == idx_note.id, GraphEdge.target_id == idx_note.id)
                )
            )
            await db.delete(idx_note)
            removed.append(idx_note.title)

    await db.commit()
    return {"removed": len(removed), "details": removed}


# ── Repair Shallow (background task) ──────────────────────────────────


@router.post("/repair-shallow")
async def repair_shallow_notes(background_tasks: BackgroundTasks, current_space: Space = Depends(get_current_space)) -> dict:
    job_id = f"repair-{datetime.now(timezone.utc).strftime('%H%M%S')}"
    _jobs[job_id] = {"job_id": job_id, "status": "running", "progress": "Finding shallow notes...", "result": None}
    background_tasks.add_task(_run_repair_shallow, job_id, current_space.id)
    return {"job_id": job_id, "status": "started"}


async def _run_repair_shallow(job_id: str, space_id: int):
    try:
        async with async_session() as db:
            result = await db.execute(select(Note).where(Note.space_id == space_id))
            all_notes = result.scalars().all()

        shallow = []
        for n in all_notes:
            body = re.sub(r"^---[\s\S]*?---\n*", "", n.content or "")
            if len(body.split()) < 150:
                shallow.append(n)

        if not shallow:
            _jobs[job_id] = {"job_id": job_id, "status": "done", "progress": "No shallow notes found", "result": {"repaired": 0, "skipped": 0, "details": []}}
            return

        repaired = 0
        skipped = 0
        details = []

        for i, note in enumerate(shallow[:10]):
            _jobs[job_id]["progress"] = f"Repairing {i + 1}/{min(len(shallow), 10)}: {note.title[:40]}..."

            try:
                async with async_session() as db:
                    r = await repair_note(RepairRequest(note_id=note.id), db)
                    if r.changes and not any("Error" in c for c in r.changes):
                        repaired += 1
                        details.append({"title": note.title, "changes": r.changes})
                    else:
                        skipped += 1
            except Exception:
                skipped += 1

        _jobs[job_id] = {
            "job_id": job_id,
            "status": "done",
            "progress": f"Complete: {repaired} repaired, {skipped} skipped",
            "result": {"repaired": repaired, "skipped": skipped, "details": details},
        }
    except Exception as e:
        _jobs[job_id] = {"job_id": job_id, "status": "error", "progress": str(e), "result": None}
