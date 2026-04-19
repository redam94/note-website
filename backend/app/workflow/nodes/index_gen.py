"""Pipeline node: update index notes with routing summaries.

Templated rendering — no LLM calls. Each folder's index note is rebuilt from
the children it actually has, using `note_plan` for richer doc_type /
depends_on when available.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...services.index_templates import (
    child_from_note,
    child_from_plan_entry,
    render_folder_index_note,
)
from ..progress import set_step
from ..state import ProcessingState


async def generate_indices(state: ProcessingState) -> ProcessingState:
    """Update index notes in the topic tree with routing summaries."""
    folder_id_map = state.get("folder_id_map", {})
    created_notes = state.get("created_notes", [])

    await set_step(
        state["document_id"],
        "Updating topic indexes...",
        notes_count=len(created_notes),
    )

    if not folder_id_map:
        return {**state, "index_notes": []}

    note_plan = state.get("note_plan", []) or []
    plan_by_title: dict[str, dict] = {p["title"]: p for p in note_plan}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    index_notes: list[dict] = []

    for folder_path, index_id in folder_id_map.items():
        async with async_session() as db:
            children_result = await db.execute(
                select(Note).where(Note.parent_id == index_id)
            )
            children = list(children_result.scalars().all())

            idx_result = await db.execute(
                select(Note).where(Note.id == index_id)
            )
            index_note = idx_result.scalar_one_or_none()

        if not index_note or not children:
            continue

        facts = []
        for c in children:
            plan_entry = plan_by_title.get(c.title)
            if plan_entry is not None:
                facts.append(child_from_plan_entry(plan_entry, c))
            else:
                facts.append(child_from_note(c))

        summary, full_md = render_folder_index_note(
            folder_path, facts, today=today
        )

        async with async_session() as db:
            res = await db.execute(select(Note).where(Note.id == index_id))
            note_to_update = res.scalar_one_or_none()
            if note_to_update:
                note_to_update.content = full_md
                note_to_update.summary = summary
                await db.commit()

        index_notes.append(
            {"id": index_id, "title": index_note.title, "slug": index_note.slug}
        )

    return {**state, "index_notes": index_notes}
