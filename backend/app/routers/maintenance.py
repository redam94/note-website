"""Note maintenance endpoints — repair, reindex, and audit operations."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..prompts import load_prompt
from ..services.model_provider import get_provider

router = APIRouter(prefix="/api/maintenance")

_repair_prompt = load_prompt("repair_note")


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


class BulkRepairResult(BaseModel):
    repaired: int
    skipped: int
    details: list[dict]


# ── Audit ─────────────────────────────────────────────────────────────


@router.get("/audit")
async def audit_vault(db: AsyncSession = Depends(get_db)) -> AuditResult:
    """Scan all notes for structural issues."""
    result = await db.execute(select(Note))
    all_notes = result.scalars().all()

    edges_result = await db.execute(select(GraphEdge))
    all_edges = edges_result.scalars().all()

    # Build sets for analysis
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

    for n in all_notes:
        # Check frontmatter completeness
        issues = []
        content = n.content or ""
        has_fm = content.startswith("---")
        if not has_fm:
            issues.append("no frontmatter")
        else:
            fm = content.split("---")[1] if "---" in content[3:] else ""
            if "depends_on" not in fm:
                issues.append("missing depends_on")
            if "used_by" not in fm:
                issues.append("missing used_by")
            if "doc_type" not in fm:
                issues.append("missing doc_type")
            if "folder" not in fm:
                issues.append("missing folder")

        if issues:
            missing_fm.append({"id": n.id, "title": n.title, "issues": issues})

        # Check if shallow
        body = re.sub(r"^---[\s\S]*?---\n*", "", content)
        word_count = len(body.split())
        if word_count < 150:
            shallow.append({"id": n.id, "title": n.title, "words": word_count})

        # Check if orphan (no edges, no parent, no children)
        if n.id not in linked_ids:
            orphans.append({"id": n.id, "title": n.title, "slug": n.slug})

        # Check for broken wiki-links
        wiki_links = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", content)
        all_titles = {note.title for note in all_notes}
        for link_target in wiki_links:
            if link_target not in all_titles and not link_target.startswith("raw/"):
                broken.append({"note_id": n.id, "note_title": n.title, "broken_link": link_target})

    # Find folders that need indexes
    folders = set()
    for n in all_notes:
        if n.source:
            folders.add(n.source)
        fm_match = re.search(r'folder:\s*"?([^"\n]+)"?', n.content or "")
        if fm_match:
            parts = fm_match.group(1).strip().split("/")
            for i in range(len(parts)):
                folders.add("/".join(parts[: i + 1]))

    index_titles = {n.title for n in all_notes if "index" in n.title.lower()}
    missing_indexes = []
    for folder in sorted(folders):
        expected = f"Index: {folder}"
        if expected not in index_titles and folder:
            missing_indexes.append(folder)

    return AuditResult(
        total_notes=len(all_notes),
        missing_frontmatter=missing_fm[:50],
        shallow_notes=shallow[:50],
        orphan_notes=orphans[:50],
        broken_links=broken[:50],
        missing_indexes=missing_indexes[:30],
    )


# ── Repair Single Note ───────────────────────────────────────────────


@router.post("/repair")
async def repair_note(
    body: RepairRequest,
    db: AsyncSession = Depends(get_db),
) -> RepairResult:
    """Repair a single note: fix frontmatter, enrich content, add links."""
    result = await db.execute(select(Note).where(Note.id == body.note_id))
    note = result.scalar_one_or_none()
    if not note:
        return RepairResult(note_id=body.note_id, title="Not found", changes=[], new_links=[])

    # Get all note titles for linking
    titles_result = await db.execute(select(Note.title).where(Note.id != note.id))
    all_titles = [t for (t,) in titles_result.all()]

    provider = await get_provider(db)

    prompt = (
        f"## Note to repair:\n\n{note.content}\n\n"
        f"## Available notes for cross-linking:\n"
        + "\n".join(f"- {t}" for t in all_titles[:60])
    )

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_repair_prompt.format(),
            max_tokens=8192,
            tier="advanced",
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(response)

        new_content = data.get("content", note.content)
        changes = data.get("changes", [])
        new_links = data.get("new_links_added", [])

        # Validate the response looks like a note
        if new_content and (new_content.startswith("---") or new_content.startswith("#")):
            note.content = new_content
            await db.commit()

        return RepairResult(
            note_id=note.id,
            title=note.title,
            changes=changes,
            new_links=new_links,
        )
    except Exception as e:
        return RepairResult(
            note_id=note.id,
            title=note.title,
            changes=[f"Error: {str(e)}"],
            new_links=[],
        )


# ── Fix Cross-References ─────────────────────────────────────────────


@router.post("/fix-links")
async def fix_cross_references(db: AsyncSession = Depends(get_db)) -> dict:
    """Ensure depends_on/used_by are bidirectionally consistent across all notes."""
    result = await db.execute(select(Note))
    all_notes = result.scalars().all()
    title_to_note = {n.title: n for n in all_notes}

    fixes = 0
    for note in all_notes:
        content = note.content or ""
        fm_match = re.match(r"^---\n([\s\S]*?)\n---", content)
        if not fm_match:
            continue

        # Extract depends_on from frontmatter
        depends = re.findall(r'depends_on:.*?(?=\n\w|\n---|\Z)', fm_match.group(1), re.DOTALL)
        dep_titles = re.findall(r'\[\[([^\]]+)\]\]', "".join(depends))

        # For each dependency, ensure the target has used_by pointing back
        for dep_title in dep_titles:
            target = title_to_note.get(dep_title)
            if not target:
                continue
            target_content = target.content or ""
            if f"[[{note.title}]]" not in target_content:
                # Add to used_by if section exists
                if "used_by:" in target_content:
                    target.content = target_content.replace(
                        "used_by:",
                        f'used_by:\n  - "[[{note.title}]]"',
                        1,
                    )
                    fixes += 1

    await db.commit()
    return {"fixes_applied": fixes, "notes_scanned": len(all_notes)}


# ── Reindex ───────────────────────────────────────────────────────────


@router.post("/reindex")
async def reindex_vault(db: AsyncSession = Depends(get_db)) -> ReindexResult:
    """Create or update index notes for each folder level in the hierarchy."""
    result = await db.execute(select(Note))
    all_notes = result.scalars().all()

    # Build folder hierarchy from note content
    folder_notes: dict[str, list[Note]] = {}
    for n in all_notes:
        folder = None
        fm_match = re.search(r'folder:\s*"?([^"\n]+)"?', n.content or "")
        if fm_match:
            folder = fm_match.group(1).strip()
        if not folder and n.source:
            folder = n.source

        if folder:
            # Add to this folder and all parent folders
            parts = folder.split("/")
            for i in range(len(parts)):
                path = "/".join(parts[: i + 1])
                folder_notes.setdefault(path, []).append(n)

    provider = await get_provider(db)
    existing_slugs_result = await db.execute(select(Note.slug))
    existing_slugs = set(existing_slugs_result.scalars().all())
    existing_index_titles = {n.title: n for n in all_notes if n.title.startswith("Index:")}

    created = 0
    updated = 0
    details = []
    now = datetime.now(timezone.utc)

    from ..prompts import load_prompt
    index_system = load_prompt("index_gen").format()

    for folder_path, notes in sorted(folder_notes.items()):
        # Only create indexes for folders with 2+ notes
        if len(notes) < 2:
            continue

        index_title = f"Index: {folder_path}"
        children_desc = "\n".join(
            f"- {n.title} (tags: {n.tags or '[]'})" for n in notes[:20]
        )

        prompt = (
            f"Folder: {folder_path}\n"
            f"Notes in this folder ({len(notes)}):\n{children_desc}\n\n"
            "Create an index note for this folder."
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=index_system,
                max_tokens=2048,
                tier="simple",
            )
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                index_data = json.loads(json_match.group())
            else:
                index_data = json.loads(response)
        except Exception:
            index_data = {
                "summary": f"Index for {folder_path}",
                "content": f"## Notes\n{children_desc}",
            }

        frontmatter = (
            f"---\n"
            f'title: "{index_title}"\n'
            f"tags:\n  - type/index\n  - source/ingested\n"
            f"date_updated: {now.strftime('%Y-%m-%d')}\n"
            f"concept_count: {len(notes)}\n"
            f"---"
        )
        full_content = f"{frontmatter}\n\n# {folder_path}\n\n{index_data.get('content', '')}"

        if index_title in existing_index_titles:
            # Update existing
            existing = existing_index_titles[index_title]
            existing.content = full_content
            existing.summary = index_data.get("summary")
            updated += 1
            details.append(f"Updated: {index_title}")
        else:
            # Create new
            slug = slugify(index_title, lowercase=True)
            counter = 1
            base_slug = slug
            while slug in existing_slugs:
                slug = f"{base_slug}-{counter}"
                counter += 1
            existing_slugs.add(slug)

            index_note = Note(
                document_id=None,
                parent_id=None,
                title=index_title,
                content=full_content,
                slug=slug,
                tags=json.dumps(["type/index"]),
                level=0,
                created_at=now.isoformat(),
                summary=index_data.get("summary"),
            )
            db.add(index_note)
            created += 1
            details.append(f"Created: {index_title}")

    await db.commit()
    return ReindexResult(indexes_updated=updated, indexes_created=created, details=details)


# ── Bulk Repair Shallow Notes ─────────────────────────────────────────


@router.post("/repair-shallow")
async def repair_shallow_notes(
    db: AsyncSession = Depends(get_db),
) -> BulkRepairResult:
    """Find and repair all notes with < 150 words of content."""
    result = await db.execute(select(Note))
    all_notes = result.scalars().all()

    shallow = []
    for n in all_notes:
        body = re.sub(r"^---[\s\S]*?---\n*", "", n.content or "")
        if len(body.split()) < 150:
            shallow.append(n)

    if not shallow:
        return BulkRepairResult(repaired=0, skipped=0, details=[])

    repaired = 0
    skipped = 0
    details = []

    for note in shallow[:10]:  # Limit to 10 at a time
        try:
            repair_result = await repair_note(
                RepairRequest(note_id=note.id), db
            )
            if repair_result.changes:
                repaired += 1
                details.append({"title": note.title, "changes": repair_result.changes})
            else:
                skipped += 1
        except Exception:
            skipped += 1

    return BulkRepairResult(repaired=repaired, skipped=skipped, details=details)
