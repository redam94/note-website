from __future__ import annotations

import asyncio
import json
import re

from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

LINK_SYSTEM = load_prompt("insert_links").format()

_CONCURRENCY = 6


async def insert_links(state: ProcessingState) -> ProcessingState:
    """Insert wiki-links into notes — all notes processed in parallel."""
    created_notes = state["created_notes"]
    await set_step(state["document_id"], "Inserting wiki-links into notes...")

    if not created_notes:
        return {**state, "linked_notes": []}

    async with async_session() as db:
        provider = await get_provider(db)
        result = await db.execute(select(Note.id, Note.title, Note.slug))
        all_notes_data = result.all()

    all_titles = list({row.title for row in all_notes_data})
    total = len(created_notes)
    completed = 0

    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _process_one(note_info: dict) -> dict:
        nonlocal completed
        async with sem:
            async with async_session() as db:
                result = await db.execute(select(Note).where(Note.id == note_info["id"]))
                note = result.scalar_one_or_none()

            if not note:
                return {"id": note_info["id"], "title": note_info.get("title", ""), "updated": False}

            linkable = [t for t in all_titles if t != note.title]
            if not linkable:
                return {"id": note.id, "title": note.title, "updated": False}

            titles_text = "\n".join(f"- {t}" for t in linkable[:60])
            prompt = (
                f"## Available notes to link to:\n{titles_text}\n\n"
                f"## Note to process:\n\n{note.content}"
            )

            try:
                updated_content = await provider.complete(
                    messages=[{"role": "user", "content": prompt}],
                    system=LINK_SYSTEM,
                    max_tokens=8192,
                    tier="simple",
                )

                if updated_content.strip().startswith("---") or updated_content.strip().startswith("#"):
                    async with async_session() as db:
                        result = await db.execute(select(Note).where(Note.id == note.id))
                        db_note = result.scalar_one()
                        db_note.content = updated_content
                        await db.commit()

                completed += 1
                await set_step(
                    state["document_id"],
                    f"Inserting links... ({completed}/{total} complete)",
                )
                return {"id": note.id, "title": note.title, "updated": True}
            except Exception:
                completed += 1
                return {"id": note.id, "title": note.title, "updated": False}

    results = await asyncio.gather(
        *[_process_one(info) for info in created_notes],
        return_exceptions=True,
    )

    linked_notes = [
        {"id": r["id"], "title": r["title"]}
        for r in results
        if not isinstance(r, BaseException)
    ]

    return {**state, "linked_notes": linked_notes}
