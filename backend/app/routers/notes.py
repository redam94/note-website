from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthUser, get_current_user, note_visibility_filter, require_admin
from ..database import get_db
from ..dependencies import get_current_space
from ..models.document import Document
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.note_comment import NoteComment
from ..models.space import Space
from ..prompts import load_prompt
from ..schemas.note import LinkInfo, NoteWithLinks
from ..services.model_provider import get_provider, get_setting

router = APIRouter(prefix="/api")


@router.get("/notes/{slug}")
async def get_note(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
    user: AuthUser = Depends(get_current_user),
) -> NoteWithLinks:
    vis = note_visibility_filter(user.is_admin)
    result = await db.execute(
        select(Note)
        .where(Note.slug == slug)
        .where(Note.space_id == current_space.id)
        .where(vis)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Get edges where this note is source or target
    edges_result = await db.execute(
        select(GraphEdge).where(
            or_(GraphEdge.source_id == note.id, GraphEdge.target_id == note.id)
        )
    )
    edges = edges_result.scalars().all()

    # Get all notes for resolving links (visibility-filtered so readers never
    # see backlinks/outlinks that reference admin-only notes)
    all_notes_result = await db.execute(
        select(Note).where(Note.space_id == current_space.id).where(vis)
    )
    all_notes = all_notes_result.scalars().all()
    note_map = {n.id: n for n in all_notes}

    backlinks: list[LinkInfo] = []
    outlinks: list[LinkInfo] = []

    for e in edges:
        if e.target_id == note.id:
            source = note_map.get(e.source_id)
            if source:
                backlinks.append(
                    LinkInfo(
                        id=source.id,
                        title=source.title,
                        slug=source.slug,
                        relationship=e.relationship_type,
                    )
                )
        if e.source_id == note.id:
            target = note_map.get(e.target_id)
            if target:
                outlinks.append(
                    LinkInfo(
                        id=target.id,
                        title=target.title,
                        slug=target.slug,
                        relationship=e.relationship_type,
                    )
                )

    # Add parent-child links
    if note.parent_id:
        parent = note_map.get(note.parent_id)
        if parent and not any(b.id == parent.id for b in backlinks):
            backlinks.append(
                LinkInfo(
                    id=parent.id,
                    title=parent.title,
                    slug=parent.slug,
                    relationship="part_of",
                )
            )

    children = [n for n in all_notes if n.parent_id == note.id]
    for child in children:
        if not any(o.id == child.id for o in outlinks):
            outlinks.append(
                LinkInfo(
                    id=child.id,
                    title=child.title,
                    slug=child.slug,
                    relationship="part_of",
                )
            )

    return NoteWithLinks.from_row(note, backlinks, outlinks)


# ── Helpers ──────────────────────────────────────────────────────────


def _make_slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "note"


async def _unique_slug(db: AsyncSession, base: str, space_id: int, exclude_id: int | None = None) -> str:
    slug, i = base, 2
    while True:
        q = select(Note).where(Note.slug == slug, Note.space_id == space_id)
        if exclude_id is not None:
            q = q.where(Note.id != exclude_id)
        if not (await db.execute(q)).scalar_one_or_none():
            return slug
        slug = f"{base}-{i}"
        i += 1


# ── Edit note ─────────────────────────────────────────────────────────


class NoteUpdate(BaseModel):
    title: str | None = None
    content: str | None = None
    tags: list[str] | None = None
    chapter: str | None = None
    source: str | None = None
    summary: str | None = None


@router.patch("/notes/{slug}", dependencies=[Depends(require_admin)])
async def update_note(
    slug: str,
    body: NoteUpdate,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    result = await db.execute(
        select(Note).where(Note.slug == slug, Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # If title changed, update wiki-links in other notes
    if body.title is not None and body.title.strip() != note.title:
        old_title = note.title
        new_title = body.title.strip()
        note.title = new_title

        all_res = await db.execute(
            select(Note).where(Note.id != note.id, Note.space_id == current_space.id)
        )
        for other in all_res.scalars().all():
            if old_title not in (other.content or ""):
                continue
            updated = re.sub(
                r"\[\[" + re.escape(old_title) + r"\|([^\]]+)\]\]",
                r"[[" + new_title + r"|\1]]",
                other.content,
            )
            updated = re.sub(
                r"\[\[" + re.escape(old_title) + r"\]\]",
                f"[[{new_title}]]",
                updated,
            )
            if updated != other.content:
                other.content = updated

    if body.content is not None:
        note.content = body.content
    if body.tags is not None:
        note.tags = json.dumps(body.tags)
    if body.chapter is not None:
        note.chapter = body.chapter or None
    if body.source is not None:
        note.source = body.source or None
    if body.summary is not None:
        note.summary = body.summary or None

    await db.commit()
    await db.refresh(note)

    return {"slug": note.slug, "title": note.title}


# ── Create note ───────────────────────────────────────────────────────


class NoteCreate(BaseModel):
    title: str
    content: str = ""
    parent_id: int | None = None
    tags: list[str] = []


@router.post("/notes", dependencies=[Depends(require_admin)])
async def create_note(
    body: NoteCreate,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")

    # Determine level from parent
    level = 1
    if body.parent_id:
        parent_res = await db.execute(
            select(Note).where(Note.id == body.parent_id, Note.space_id == current_space.id)
        )
        parent = parent_res.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent note not found")
        level = min(parent.level + 1, 3)

    slug = await _unique_slug(db, _make_slug(title), current_space.id)

    note = Note(
        title=title,
        content=body.content,
        slug=slug,
        tags=json.dumps(body.tags),
        level=level,
        parent_id=body.parent_id,
        space_id=current_space.id,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(note)
    await db.flush()  # get note.id before creating edge

    # Create part_of edge if parent given (source=child, target=parent per sidebar convention)
    if body.parent_id:
        edge = GraphEdge(
            source_id=note.id,
            target_id=body.parent_id,
            relationship_type="part_of",
            confidence=1.0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(edge)

    await db.commit()
    await db.refresh(note)

    window = {"sidebar-refresh": True}  # signal to frontend
    return {"id": note.id, "slug": note.slug, "title": note.title, **window}


# ── Move note ─────────────────────────────────────────────────────────


class NoteMove(BaseModel):
    parent_id: int | None  # None = move to root


class NoteVisibilityUpdate(BaseModel):
    visibility: str  # "public" | "admin"


@router.patch("/notes/{slug}/visibility", dependencies=[Depends(require_admin)])
async def update_note_visibility(
    slug: str,
    body: NoteVisibilityUpdate,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    if body.visibility not in ("public", "admin"):
        raise HTTPException(status_code=400, detail="visibility must be 'public' or 'admin'")

    result = await db.execute(
        select(Note).where(Note.slug == slug, Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    note.visibility = body.visibility
    await db.commit()
    return {"slug": note.slug, "visibility": note.visibility}


@router.patch("/notes/{slug}/parent", dependencies=[Depends(require_admin)])
async def move_note(
    slug: str,
    body: NoteMove,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    result = await db.execute(
        select(Note).where(Note.slug == slug, Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    if body.parent_id == note.id:
        raise HTTPException(status_code=400, detail="A note cannot be its own parent")

    # Validate new parent belongs to the same space
    if body.parent_id is not None:
        par_res = await db.execute(
            select(Note).where(Note.id == body.parent_id, Note.space_id == current_space.id)
        )
        if not par_res.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="Parent note not found")

    # Remove existing part_of edge (source=this note, relationship=part_of)
    await db.execute(
        delete(GraphEdge).where(
            GraphEdge.source_id == note.id,
            GraphEdge.relationship_type == "part_of",
        )
    )

    # Update parent_id and recalculate level
    note.parent_id = body.parent_id
    if body.parent_id is None:
        note.level = 1
    else:
        par_res2 = await db.execute(select(Note).where(Note.id == body.parent_id))
        parent = par_res2.scalar_one_or_none()
        note.level = min((parent.level + 1) if parent else 1, 3)

    # Create new part_of edge if moving under a parent
    if body.parent_id is not None:
        edge = GraphEdge(
            source_id=note.id,
            target_id=body.parent_id,
            relationship_type="part_of",
            confidence=1.0,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        db.add(edge)

    await db.commit()
    return {"slug": note.slug, "parent_id": note.parent_id, "level": note.level}


@router.delete("/notes/{slug}", dependencies=[Depends(require_admin)])
async def delete_note(slug: str, db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)):
    """Delete a note and clean up all dead cross-links.

    This:
    1. Deletes all graph_edges referencing this note (CASCADE should handle
       this, but we do it explicitly for wiki-link cleanup).
    2. Removes wiki-links [[Note Title]] from other notes' content that
       pointed to this note.
    3. Clears parent_id on child notes (re-parents them to this note's parent).
    4. Removes the note.
    """
    result = await db.execute(select(Note).where(Note.slug == slug).where(Note.space_id == current_space.id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    note_id = note.id
    note_title = note.title
    note_parent_id = note.parent_id

    # 1. Delete all graph edges referencing this note
    await db.execute(
        delete(GraphEdge).where(
            or_(GraphEdge.source_id == note_id, GraphEdge.target_id == note_id)
        )
    )

    # 2. Re-parent children to this note's parent (or None)
    await db.execute(
        update(Note)
        .where(Note.parent_id == note_id)
        .values(parent_id=note_parent_id)
    )

    # 3. Clean up wiki-links in other notes' content
    #    Remove [[Note Title]], [[Note Title|display]], and broken links
    all_notes_result = await db.execute(select(Note).where(Note.id != note_id).where(Note.space_id == current_space.id))
    for other_note in all_notes_result.scalars().all():
        content = other_note.content
        if note_title not in content:
            continue

        # Replace [[Note Title|display]] with just "display"
        updated = re.sub(
            r"\[\[" + re.escape(note_title) + r"\|([^\]]+)\]\]",
            r"\1",
            content,
        )
        # Replace [[Note Title]] with just "Note Title"
        updated = re.sub(
            r"\[\[" + re.escape(note_title) + r"\]\]",
            note_title,
            updated,
        )
        # Also clean depends_on/used_by references in frontmatter
        updated = re.sub(
            r'^\s*- "\[\[' + re.escape(note_title) + r'\]\]"\s*$',
            "",
            updated,
            flags=re.MULTILINE,
        )

        if updated != content:
            other_note.content = updated

    # 4. Delete the note itself
    await db.execute(delete(Note).where(Note.id == note_id))
    await db.commit()

    return {"message": f"Deleted note '{note_title}' and cleaned up cross-links"}


# ── Reprocess note ───────────────────────────────────────────────────


class ReprocessRequest(BaseModel):
    instructions: str = ""


@router.post("/notes/{slug}/reprocess", dependencies=[Depends(require_admin)])
async def reprocess_note(
    slug: str,
    body: ReprocessRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    """Reprocess a note using its original source material."""
    result = await db.execute(
        select(Note).where(Note.slug == slug).where(Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    # Get original source text from the document
    source_text = ""
    if note.document_id:
        doc_result = await db.execute(
            select(Document).where(Document.id == note.document_id)
        )
        doc = doc_result.scalar_one_or_none()
        if doc and doc.content_raw:
            # Extract relevant section from source
            if note.page:
                # Try to get text around the noted page
                start = max(0, (note.page - 1) * 3000)
                end = min(len(doc.content_raw), (note.page + 2) * 3000)
                source_text = doc.content_raw[start:end]
            else:
                source_text = doc.content_raw[:8000]

    # Get related notes for context
    titles_result = await db.execute(
        select(Note.title).where(Note.id != note.id).where(Note.space_id == current_space.id)
    )
    all_titles = [t for (t,) in titles_result.all()]

    # Get any open comments as additional instructions
    comments_result = await db.execute(
        select(NoteComment).where(
            NoteComment.note_id == note.id, NoteComment.resolved == 0
        )
    )
    open_comments = comments_result.scalars().all()
    comment_text = ""
    if open_comments:
        comment_text = "\n\nUser feedback to address:\n" + "\n".join(
            f"- {c.content}" for c in open_comments
        )

    provider = await get_provider(db)
    model = await get_setting(db, "model_create")

    _plan_prompt = load_prompt("repair_plan")
    _execute_prompt = load_prompt("repair_execute")

    # Phase 1: Plan the rewrite
    plan_prompt = (
        f"## Note to reprocess:\n\n{note.content}\n\n"
        f"## Original source material:\n{source_text[:6000]}\n\n"
        f"## Available notes for [[wiki-links]]:\n"
        + "\n".join(f"- {t}" for t in all_titles[:60])
        + (f"\n\n## Additional instructions:\n{body.instructions}" if body.instructions else "")
        + comment_text
    )

    repair_plan = None
    plan_text = ""
    try:
        plan_response = await provider.complete(
            messages=[{"role": "user", "content": plan_prompt}],
            system=_plan_prompt.format(),
            max_tokens=2048,
            model=model,
        )
        json_match = re.search(r"\{.*\}", plan_response, re.DOTALL)
        if json_match:
            # Try to fix common JSON issues: trailing commas
            raw_json = json_match.group()
            raw_json = re.sub(r",\s*([}\]])", r"\1", raw_json)
            try:
                repair_plan = json.loads(raw_json)
                plan_text = json.dumps(repair_plan, indent=2)
            except json.JSONDecodeError:
                # Use raw LLM text as the plan
                plan_text = plan_response
        else:
            plan_text = plan_response
    except Exception as e:
        logger.warning("Repair planning failed for %s: %s", slug, e)
        plan_text = f"Rewrite this note with more depth and proper structure. {body.instructions}"

    # Phase 2: Execute the rewrite
    execute_prompt = (
        f"## Current note:\n\n{note.content}\n\n"
        f"## Original source material:\n{source_text[:6000]}\n\n"
        f"## Repair plan:\n{plan_text}\n\n"
        f"## Available notes for [[wiki-links]]:\n"
        + "\n".join(f"- {t}" for t in all_titles[:60])
        + "\n\nRewrite the note following the repair plan. Reference the original source material."
    )

    try:
        new_content = await provider.complete(
            messages=[{"role": "user", "content": execute_prompt}],
            system=_execute_prompt.format(),
            max_tokens=8192,
            model=model,
        )

        stripped = new_content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```\w*\n?", "", stripped)
            stripped = re.sub(r"\n?```$", "", stripped)

        if stripped.startswith("---") or stripped.startswith("#"):
            note.content = stripped
            # Mark open comments as resolved
            for c in open_comments:
                c.resolved = 1
            await db.commit()
            return {
                "success": True,
                "title": note.title,
                "issues": repair_plan.get("issues_found", []) if repair_plan else ["rewritten"],
                "comments_resolved": len(open_comments),
            }
        else:
            raise HTTPException(status_code=500, detail="Reprocessing produced invalid output")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Reprocessing failed: {str(e)}")


# ── Comments ─────────────────────────────────────────────────────────


class CommentCreate(BaseModel):
    content: str


class CommentResponse(BaseModel):
    id: int
    noteId: int
    author: str
    content: str
    resolved: bool
    createdAt: str


@router.get("/notes/{slug}/comments")
async def get_comments(
    slug: str,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[CommentResponse]:
    result = await db.execute(
        select(Note).where(Note.slug == slug).where(Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    comments_result = await db.execute(
        select(NoteComment).where(NoteComment.note_id == note.id).order_by(NoteComment.id)
    )
    return [
        CommentResponse(
            id=c.id, noteId=c.note_id, author=c.author,
            content=c.content, resolved=bool(c.resolved),
            createdAt=c.created_at,
        )
        for c in comments_result.scalars().all()
    ]


@router.post("/notes/{slug}/comments")
async def add_comment(
    slug: str,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
    user: AuthUser = Depends(get_current_user),
):
    if user.role == "guest":
        raise HTTPException(status_code=403, detail="Sign in to add comments")

    result = await db.execute(
        select(Note).where(Note.slug == slug).where(Note.space_id == current_space.id)
    )
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    comment = NoteComment(
        note_id=note.id,
        author=user.role,
        content=body.content.strip(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(comment)
    await db.commit()
    await db.refresh(comment)

    return CommentResponse(
        id=comment.id, noteId=comment.note_id, author=comment.author,
        content=comment.content, resolved=bool(comment.resolved),
        createdAt=comment.created_at,
    )


@router.post("/notes/comments/{comment_id}/resolve", dependencies=[Depends(require_admin)])
async def resolve_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(NoteComment).where(NoteComment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    comment.resolved = 1
    await db.commit()
    return {"resolved": True}


@router.delete("/notes/comments/{comment_id}", dependencies=[Depends(require_admin)])
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(NoteComment).where(NoteComment.id == comment_id))
    comment = result.scalar_one_or_none()
    if not comment:
        raise HTTPException(status_code=404, detail="Comment not found")

    await db.delete(comment)
    await db.commit()
    return {"deleted": True}
