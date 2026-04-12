from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..schemas.note import LinkInfo, NoteWithLinks

router = APIRouter(prefix="/api")


@router.get("/notes/{slug}")
async def get_note(slug: str, db: AsyncSession = Depends(get_db)) -> NoteWithLinks:
    result = await db.execute(select(Note).where(Note.slug == slug))
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

    # Get all notes for resolving links
    all_notes_result = await db.execute(select(Note))
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


@router.delete("/notes/{slug}")
async def delete_note(slug: str, db: AsyncSession = Depends(get_db)):
    """Delete a note and clean up all dead cross-links.

    This:
    1. Deletes all graph_edges referencing this note (CASCADE should handle
       this, but we do it explicitly for wiki-link cleanup).
    2. Removes wiki-links [[Note Title]] from other notes' content that
       pointed to this note.
    3. Clears parent_id on child notes (re-parents them to this note's parent).
    4. Removes the note.
    """
    result = await db.execute(select(Note).where(Note.slug == slug))
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
    all_notes_result = await db.execute(select(Note).where(Note.id != note_id))
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
