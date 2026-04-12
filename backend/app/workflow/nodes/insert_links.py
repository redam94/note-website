from __future__ import annotations

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

_LINK_SYSTEM_LEGACY = """\
You are a knowledge base link editor. Given a note's content and a list of other note titles \
that exist in the knowledge base, your job is to insert wiki-links `[[Note Title]]` into the \
note content wherever a concept is mentioned that matches an existing note.

Rules:
1. Insert links using `[[Note Title]]` syntax (Obsidian wiki-links)
2. Use `[[Note Title|display text]]` when the note title doesn't read naturally in context
3. Only link to notes from the provided list — do NOT invent links
4. Link each note title at most ONCE (first meaningful mention only)
5. Do NOT link inside callout headers, YAML frontmatter, or H1 titles
6. Do NOT modify the note's meaning, structure, or content — only add links
7. Preserve all existing links, callouts, math blocks, and formatting exactly
8. If a note title appears as part of another phrase, wrap just the relevant part
9. Update the `## See Also` section: add links to related notes that aren't already mentioned \
in the body, with a brief description of the relationship
10. Update the `depends_on` and `used_by` fields in the frontmatter based on the links you inserted

Return ONLY the modified note content (the complete note including frontmatter). No JSON wrapping.
"""


async def insert_links(state: ProcessingState) -> ProcessingState:
    """Process finished notes to insert wiki-links to other notes."""
    created_notes = state["created_notes"]
    await set_step(state["document_id"], "Inserting wiki-links into notes...")

    if not created_notes:
        return {**state, "linked_notes": []}

    # Gather all note titles (both new and existing)
    new_titles = [n["title"] for n in created_notes]

    async with async_session() as db:
        provider = await get_provider(db)
        result = await db.execute(select(Note.id, Note.title, Note.slug))
        all_notes_data = result.all()

    all_titles = list({row.title for row in all_notes_data})
    new_ids = {n["id"] for n in created_notes}

    linked_notes = []

    for note_info in created_notes:
        # Read the current note content from DB
        async with async_session() as db:
            result = await db.execute(select(Note).where(Note.id == note_info["id"]))
            note = result.scalar_one_or_none()

        if not note:
            continue

        # Build the list of linkable titles (exclude self)
        linkable = [t for t in all_titles if t != note.title]

        if not linkable:
            linked_notes.append({"id": note.id, "title": note.title})
            continue

        # Group titles for context
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
                tier="advanced",
            )

            # Validate the response looks like a note (has frontmatter or heading)
            if updated_content.strip().startswith("---") or updated_content.strip().startswith("#"):
                async with async_session() as db:
                    result = await db.execute(select(Note).where(Note.id == note.id))
                    db_note = result.scalar_one()
                    db_note.content = updated_content
                    await db.commit()
        except Exception:
            pass  # Keep original content if link insertion fails

        linked_notes.append({"id": note.id, "title": note.title})

    return {**state, "linked_notes": linked_notes}
