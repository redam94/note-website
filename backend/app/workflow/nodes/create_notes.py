from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from slugify import slugify
from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

NOTE_SYSTEM = load_prompt("create_note").format()

_NOTE_SYSTEM_LEGACY = """\
You are a knowledge extraction expert creating notes for an Obsidian-style knowledge base.

Each note MUST follow this exact format:

1. Start with a `> [!summary]` callout (2-3 sentences capturing the key point)
2. An `## Overview` section providing context
3. `## Main Content` with detailed subsections using `###` and `####`
4. Use Obsidian-style callouts for special content:
   - `> [!definition] Definition Name` for formal definitions
   - `> [!theorem] Theorem Name` for mathematical theorems/propositions
   - `> [!example] Example Title` for concrete examples
   - `> [!important]` for critical insights
   - `> [!warning]` for caveats and edge cases
5. Use LaTeX math: inline `$...$` and display `$$...$$` with `\\tag{}` for numbered equations
6. End with a `## See Also` section listing related topics as bullet points

Rules:
- The note must be SELF-CONTAINED — understandable without the source document
- Focus on ONE topic with rich, specific detail
- Use markdown headers, bold, bullet points for structure
- Include specific formulas, parameters, and examples from the source text
- Write in an academic but accessible style

Return ONLY valid JSON:
{"summary": "2-3 sentence summary for the [!summary] callout", "content": "Full markdown content (everything AFTER the H1 title, starting with the [!summary] callout)"}
"""


def _get_section_text(page_texts: list[dict], page: int, max_chars: int = 5000) -> str:
    """Extract text around the given page number."""
    if not page_texts:
        return ""
    start_idx = max(0, page - 2)
    end_idx = min(len(page_texts), page + 2)
    parts = []
    total = 0
    for pt in page_texts[start_idx:end_idx]:
        text = pt["text"]
        if total + len(text) > max_chars:
            parts.append(text[: max_chars - total])
            break
        parts.append(text)
        total += len(text)
    return "\n".join(parts)


def _build_frontmatter(
    title: str,
    tags: list[str],
    source: str,
    source_location: str,
    chapter: str | None,
    folder: str,
    depends_on: list[str],
    used_by: list[str],
    doc_type: str = "concept",
) -> str:
    """Build YAML frontmatter matching the second-brain format."""
    lines = ["---"]
    lines.append(f'title: "{title}"')
    lines.append("tags:")
    lines.append("  - source/ingested")
    for tag in tags:
        sanitized = tag.lower().replace(" ", "-")
        lines.append(f"  - topic/{sanitized}")
    lines.append(f"  - type/{doc_type}")
    lines.append(f'source: "{source}"')
    if source_location:
        lines.append(f'source_location: "{source_location}"')
    if chapter:
        lines.append(f'chapter: "{chapter}"')
    lines.append(f"date_ingested: {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    lines.append(f'folder: "{folder}"')
    lines.append(f"doc_type: {doc_type}")
    if depends_on:
        lines.append("depends_on:")
        for dep in depends_on:
            lines.append(f'  - "[[{dep}]]"')
    if used_by:
        lines.append("used_by:")
        for u in used_by:
            lines.append(f'  - "[[{u}]]"')
    lines.append("---")
    return "\n".join(lines)


async def create_notes(state: ProcessingState) -> ProcessingState:
    """Create notes based on the plan, in Obsidian second-brain format."""
    note_plan = state["note_plan"]
    page_texts = state["page_texts"]
    original_name = state["original_name"]
    document_id = state["document_id"]

    async with async_session() as db:
        provider = await get_provider(db)
        result = await db.execute(select(Note.slug))
        existing_slugs = set(result.scalars().all())

    created_notes = []
    slug_map: dict[str, int] = {}  # title -> note_id
    title_list = [p["title"] for p in note_plan]

    total_planned = len(note_plan)
    for note_idx, plan_entry in enumerate(note_plan):
        await set_step(
            document_id,
            f"Creating note {note_idx + 1}/{total_planned}: {plan_entry['title'][:50]}",
            notes_count=note_idx,
        )
        page = plan_entry.get("page", 1)
        section_text = _get_section_text(page_texts, page)

        # Build context about sibling and existing notes for cross-referencing
        sibling_titles = [t for t in title_list if t != plan_entry["title"]]
        existing_titles = state.get("existing_note_titles", [])
        all_linkable = list(set(sibling_titles + existing_titles))[:40]

        depends_str = ", ".join(plan_entry.get("depends_on", [])[:5])
        used_by_str = ", ".join(plan_entry.get("used_by", [])[:5])

        prompt = (
            f"Document: {original_name}\n"
            f"Topic: {plan_entry['title']}\n"
            f"Folder: {plan_entry.get('folder', '')}\n"
            f"Scope: {plan_entry.get('scope', 'General coverage')}\n"
            f"Chapter/Section: {plan_entry.get('chapter', 'N/A')}\n"
            f"Page: {page}\n"
            f"Depends on: {depends_str or 'none'}\n"
            f"Used by: {used_by_str or 'none'}\n\n"
            f"Available notes to link to with [[Note Title]]:\n"
            + "\n".join(f"- {t}" for t in all_linkable)
            + f"\n\nSource text from document:\n{section_text}\n\n"
            "Create a detailed note. Use [!definition], [!theorem], [!example] callouts "
            "with ^block-ids. Include LaTeX math. Cross-link to existing notes with [[wiki-links]]. "
            "Display math must have $$ on its own line."
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=NOTE_SYSTEM,
                max_tokens=4096,
                tier="advanced",
            )
            json_match = re.search(r"\{.*\}", response, re.DOTALL)
            if json_match:
                note_data = json.loads(json_match.group())
            else:
                note_data = json.loads(response)
        except (json.JSONDecodeError, Exception):
            note_data = {
                "summary": f"Note about {plan_entry['title']}",
                "content": (
                    f"> [!summary]\n"
                    f"> Note about {plan_entry['title']} from {original_name}.\n\n"
                    f"## Overview\n\nContent extracted from {original_name}.\n\n"
                    f"## See Also\n"
                ),
            }

        # Generate unique slug
        base_slug = slugify(plan_entry["title"], lowercase=True)
        slug = base_slug
        counter = 1
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing_slugs.add(slug)

        # Determine parent
        parent_title = plan_entry.get("parent_title")
        parent_id = slug_map.get(parent_title) if parent_title else None

        tags = plan_entry.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        # Determine doc_type from plan
        doc_type = plan_entry.get("doc_type", "concept")

        # Build depends_on/used_by from plan
        depends_on = plan_entry.get("depends_on", [])
        if parent_title:
            depends_on = [parent_title] + [d for d in depends_on if d != parent_title]
        used_by = plan_entry.get("used_by", [])

        # Build the full note content with frontmatter
        chapter = plan_entry.get("chapter")
        source_location = f"pp. {page}" if page else ""
        folder = plan_entry.get("folder", "")

        frontmatter = _build_frontmatter(
            title=plan_entry["title"],
            tags=tags,
            source=f"[[raw/{original_name}]]",
            source_location=source_location,
            chapter=chapter,
            folder=folder,
            depends_on=depends_on,
            used_by=used_by,
            doc_type=doc_type,
        )

        # Assemble full content: frontmatter + H1 + body
        body = note_data.get("content", "")
        full_content = f"{frontmatter}\n\n# {plan_entry['title']}\n\n{body}"

        now = datetime.now(timezone.utc).isoformat()
        note = Note(
            document_id=document_id,
            parent_id=parent_id,
            title=plan_entry["title"],
            content=full_content,
            slug=slug,
            tags=json.dumps(tags),
            level=plan_entry.get("level", 1),
            created_at=now,
            source=original_name,
            chapter=chapter,
            page=page,
            summary=note_data.get("summary"),
        )

        async with async_session() as db:
            db.add(note)
            await db.commit()
            await db.refresh(note)
            slug_map[plan_entry["title"]] = note.id

        created_notes.append(
            {
                "id": note.id,
                "title": note.title,
                "slug": note.slug,
                "level": note.level,
                "parent_id": note.parent_id,
                "tags": tags,
            }
        )

    return {**state, "created_notes": created_notes}
