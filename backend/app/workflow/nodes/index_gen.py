"""Pipeline node: update index notes with routing summaries.

After content notes are created and attached to the topic tree,
this node updates each index note with a summary of its children.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

INDEX_SYSTEM = load_prompt("index_gen").format()


async def generate_indices(state: ProcessingState) -> ProcessingState:
    """Update index notes in the topic tree with routing summaries."""
    folder_id_map = state.get("folder_id_map", {})
    created_notes = state.get("created_notes", [])
    original_name = state["original_name"]

    await set_step(
        state["document_id"],
        "Updating topic indexes...",
        notes_count=len(created_notes),
    )

    if not folder_id_map:
        return {**state, "index_notes": []}

    async with async_session() as db:
        provider = await get_provider(db)

    index_notes = []
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for folder_path, index_id in folder_id_map.items():
        # Query all children of this index note
        async with async_session() as db:
            result = await db.execute(
                select(Note).where(Note.parent_id == index_id)
            )
            children = result.scalars().all()

            # Also get the index note itself
            idx_result = await db.execute(
                select(Note).where(Note.id == index_id)
            )
            index_note = idx_result.scalar_one_or_none()

        if not index_note or not children:
            continue

        # Separate child indexes from content notes
        child_indexes = [c for c in children if c.title.startswith("Index:")]
        content_children = [c for c in children if not c.title.startswith("Index:")]

        children_desc_parts = []
        for c in child_indexes:
            children_desc_parts.append(f"- [[{c.title}]] (sub-topic index)")
        for c in content_children:
            tags = []
            try:
                tags = json.loads(c.tags) if c.tags else []
            except (json.JSONDecodeError, TypeError):
                pass
            summary = c.summary or c.content[:150] if c.content else ""
            children_desc_parts.append(
                f"- [[{c.title}]] (tags: {', '.join(tags[:3])}) — {summary[:100]}"
            )

        children_desc = "\n".join(children_desc_parts)
        leaf_name = folder_path.rsplit("/", 1)[-1] if "/" in folder_path else folder_path

        prompt = (
            f"Topic folder: {folder_path}\n"
            f"Total items: {len(children)} ({len(child_indexes)} sub-topics, {len(content_children)} notes)\n\n"
            f"Contents:\n{children_desc}\n\n"
            "Create an index note for this topic folder. Use [[Note Title]] wiki-link syntax."
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=INDEX_SYSTEM,
                max_tokens=2048,
                tier="simple",
            )
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                index_data = json.loads(json_match.group())
            else:
                index_data = json.loads(response)
        except (json.JSONDecodeError, Exception):
            # Fallback: build index manually
            routing_lines = "\n".join(
                f"> - For {c.title} -> [[{c.title}]]" for c in children[:10]
            )
            subtopic_lines = "\n".join(
                f"- [[{c.title}]]" for c in children
            )
            index_data = {
                "summary": f"Index for {folder_path} ({len(children)} items)",
                "content": (
                    f"> [!abstract] Routing Summary\n"
                    f"> Contains {len(children)} items.\n"
                    f"{routing_lines}\n\n"
                    f"## Contents\n{subtopic_lines}"
                ),
            }

        # Update the index note content
        frontmatter = (
            f"---\n"
            f'title: "{index_note.title}"\n'
            f"tags:\n"
            f"  - type/index\n"
            f"date_updated: {today}\n"
            f"concept_count: {len(children)}\n"
            f"---"
        )

        body = index_data.get("content", "")
        full_content = f"{frontmatter}\n\n# {leaf_name}\n\n{body}"

        async with async_session() as db:
            result = await db.execute(
                select(Note).where(Note.id == index_id)
            )
            note_to_update = result.scalar_one_or_none()
            if note_to_update:
                note_to_update.content = full_content
                note_to_update.summary = index_data.get("summary")
                await db.commit()

        index_notes.append(
            {"id": index_id, "title": index_note.title, "slug": index_note.slug}
        )

    return {**state, "index_notes": index_notes}
