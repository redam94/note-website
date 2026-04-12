from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from slugify import slugify
from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

INDEX_SYSTEM = load_prompt("index_gen").format()

_INDEX_SYSTEM_LEGACY = """\
You are a knowledge base organizer creating an index note in Obsidian format.

The index note MUST follow this exact structure:

```
> [!abstract] Routing Summary
> Brief description of what this section covers and how many notes it contains.
> - For [topic A] -> [[Note A]]
> - For [topic B] -> [[Note B]]

## Sub-topics / Notes
- [[Note Title 1]] — COVERS: brief description of what this note covers
- [[Note Title 2]] — COVERS: brief description of what this note covers

## Key Concept Dependencies
(A simple ASCII diagram or bullet list showing how concepts relate)

## Source
- [[raw/filename.pdf]] — Full citation
```

Rules:
- Use `[[Note Title]]` wiki-link syntax for all note references
- The routing summary should help a reader quickly find the right note
- Each note entry needs a one-line "COVERS:" description
- Keep it concise but informative

Return ONLY valid JSON:
{"summary": "Brief description for the routing summary", "content": "Full markdown content after the H1 title"}
"""


async def generate_indices(state: ProcessingState) -> ProcessingState:
    """Generate _Index-style notes for each hierarchy level."""
    created_notes = state["created_notes"]
    original_name = state["original_name"]
    document_id = state["document_id"]

    await set_step(state["document_id"], "Generating index notes...", notes_count=len(created_notes))

    if not created_notes:
        return {**state, "index_notes": []}

    # Group notes by parent
    children_by_parent: dict[int | None, list[dict]] = defaultdict(list)
    for note in created_notes:
        children_by_parent[note.get("parent_id")].append(note)

    async with async_session() as db:
        provider = await get_provider(db)
        existing_slugs_result = await db.execute(select(Note.slug))
        existing_slugs = set(existing_slugs_result.scalars().all())

    index_notes = []
    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Generate index for top-level notes (root index)
    top_level = children_by_parent.get(None, [])
    if len(top_level) >= 1:
        children_desc = "\n".join(
            f"- [[{n['title']}]] (level {n['level']}, tags: {', '.join(n.get('tags', []))})"
            for n in top_level
        )
        prompt = (
            f"Document: {original_name}\n"
            f"Total notes created: {len(created_notes)}\n\n"
            f"Top-level notes:\n{children_desc}\n\n"
            "Create an index note for these. Use [[Note Title]] wiki-link syntax."
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=INDEX_SYSTEM,
                max_tokens=4096,
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
                f"> - For {n['title']} -> [[{n['title']}]]" for n in top_level
            )
            subtopic_lines = "\n".join(
                f"- [[{n['title']}]] — COVERS: {', '.join(n.get('tags', []))}"
                for n in top_level
            )
            index_data = {
                "summary": f"Index of {len(created_notes)} notes from {original_name}",
                "content": (
                    f"> [!abstract] Routing Summary\n"
                    f"> Contains {len(created_notes)} notes from {original_name}.\n"
                    f"{routing_lines}\n\n"
                    f"## Notes\n{subtopic_lines}\n\n"
                    f"## Source\n- [[raw/{original_name}]]"
                ),
            }

        # Build frontmatter
        index_title = f"Index: {original_name}"
        frontmatter = (
            f"---\n"
            f'title: "{index_title}"\n'
            f"tags:\n"
            f"  - type/index\n"
            f"  - source/ingested\n"
            f"date_updated: {today}\n"
            f"concept_count: {len(created_notes)}\n"
            f"---"
        )

        body = index_data.get("content", "")
        full_content = f"{frontmatter}\n\n# {original_name}\n\n{body}"

        slug = slugify(f"{original_name}-index", lowercase=True)
        counter = 1
        base_slug = slug
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing_slugs.add(slug)

        index_note = Note(
            document_id=document_id,
            parent_id=None,
            title=index_title,
            content=full_content,
            slug=slug,
            tags=json.dumps(["index"]),
            level=0,
            created_at=now,
            source=original_name,
            summary=index_data.get("summary"),
        )

        async with async_session() as db:
            db.add(index_note)
            await db.commit()
            await db.refresh(index_note)

        index_notes.append(
            {"id": index_note.id, "title": index_note.title, "slug": index_note.slug}
        )

    # Generate sub-indexes for parents with multiple children
    for parent_id, children in children_by_parent.items():
        if parent_id is None or len(children) < 2:
            continue

        # Get parent note title
        async with async_session() as db:
            result = await db.execute(select(Note).where(Note.id == parent_id))
            parent_note = result.scalar_one_or_none()

        if not parent_note:
            continue

        children_desc = "\n".join(
            f"- [[{n['title']}]] (tags: {', '.join(n.get('tags', []))})"
            for n in children
        )
        prompt = (
            f"Parent topic: {parent_note.title}\n"
            f"Child notes:\n{children_desc}\n\n"
            "Create a sub-index for these child notes."
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
                sub_index_data = json.loads(json_match.group())
            else:
                sub_index_data = json.loads(response)
        except (json.JSONDecodeError, Exception):
            continue

        sub_title = f"Index: {parent_note.title}"
        sub_frontmatter = (
            f"---\n"
            f'title: "{sub_title}"\n'
            f"tags:\n"
            f"  - type/index\n"
            f"  - source/ingested\n"
            f'parent: "[[{original_name} - Index]]"\n'
            f"date_updated: {today}\n"
            f"---"
        )

        sub_body = sub_index_data.get("content", "")
        sub_full = f"{sub_frontmatter}\n\n# {parent_note.title}\n\n{sub_body}"

        sub_slug = slugify(f"{parent_note.title}-index", lowercase=True)
        counter = 1
        base_sub = sub_slug
        while sub_slug in existing_slugs:
            sub_slug = f"{base_sub}-{counter}"
            counter += 1
        existing_slugs.add(sub_slug)

        sub_note = Note(
            document_id=document_id,
            parent_id=parent_id,
            title=sub_title,
            content=sub_full,
            slug=sub_slug,
            tags=json.dumps(["index"]),
            level=0,
            created_at=now,
            source=original_name,
            summary=sub_index_data.get("summary"),
        )

        async with async_session() as db:
            db.add(sub_note)
            await db.commit()
            await db.refresh(sub_note)

        index_notes.append(
            {"id": sub_note.id, "title": sub_note.title, "slug": sub_note.slug}
        )

    return {**state, "index_notes": index_notes}
