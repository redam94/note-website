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

_system_prompt = load_prompt("plan")

PLAN_SYSTEM = _system_prompt.format()

_UNUSED = """\
You are a knowledge management planner creating a note extraction plan for an Obsidian-style \
knowledge base that uses wiki-links, callouts, and LaTeX math.

Given a document outline and existing tags/notes, plan what notes should be created.

Rules:
- Each note covers ONE topic and should be self-contained
- Reuse existing tags when relevant; only create new tags if no existing tag fits
- Notes form a hierarchy: level 1 (broad topic), level 2 (subtopic), level 3 (atomic note)
- Identify which notes will need:
  - `[!definition]` callouts (formal definitions)
  - `[!theorem]` callouts (mathematical theorems, propositions)
  - `[!example]` callouts (concrete examples)
  - LaTeX math (formulas, equations)
- Plan `depends_on` relationships (what prior knowledge is needed)

Return ONLY valid JSON:
{
  "notes": [
    {
      "title": "Note Title",
      "parent_title": null,
      "tags": ["tag1", "tag2"],
      "chapter": "Chapter/Section Name",
      "page": 1,
      "level": 1,
      "scope": "What this note should cover",
      "doc_type": "concept",
      "has_definitions": true,
      "has_theorems": false,
      "has_math": true,
      "depends_on": ["Other Note Title"],
      "used_by": []
    }
  ]
}

doc_type options: concept, definition, theorem, paper, textbook, tutorial
"""


async def create_plan(state: ProcessingState) -> ProcessingState:
    """Plan what notes to create based on the outline."""
    await set_step(state["document_id"], "Planning note extraction...")
    outline = state["outline"]
    original_name = state["original_name"]

    # Fetch existing tags and note titles from DB
    async with async_session() as db:
        tags_result = await db.execute(select(Note.tags))
        all_tags_raw = tags_result.scalars().all()

        titles_result = await db.execute(select(Note.title))
        existing_titles = [t for t in titles_result.scalars().all()]

        provider = await get_provider(db)

    existing_tags: set[str] = set()
    for tags_raw in all_tags_raw:
        if tags_raw:
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
                existing_tags.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass

    existing_tags_list = sorted(existing_tags)

    # Build snippets from page_texts for context
    snippets = []
    for section in outline[:15]:
        page = section.get("page_start", 1)
        page_texts = state["page_texts"]
        if page <= len(page_texts):
            text = page_texts[page - 1]["text"]
            snippets.append(f"## {section['title']} (page {page})\n{text[:400]}...")

    # Include pre-extracted structure metadata
    structure_info = ""
    tables = state.get("extracted_tables", [])
    equations = state.get("extracted_equations", [])
    definitions = state.get("extracted_definitions", [])
    if tables:
        structure_info += f"\n\nPre-extracted: {len(tables)} tables found in document."
    if equations:
        display_count = sum(1 for e in equations if e.get("type") == "display")
        structure_info += f"\n{display_count} display equations, {len(equations) - display_count} inline math expressions."
    if definitions:
        def_terms = [d["term"] for d in definitions[:10]]
        structure_info += f"\nDefinition-like patterns detected for: {', '.join(def_terms)}"

    prompt = (
        f"Document: {original_name}\n\n"
        f"Existing tags in knowledge base: {json.dumps(existing_tags_list)}\n\n"
        f"Existing notes in knowledge base (for depends_on/used_by references):\n"
        + "\n".join(f"- {t}" for t in existing_titles[:50])
        + f"\n\nDocument outline:\n{json.dumps(outline, indent=2)}\n\n"
        f"Section previews:\n{''.join(snippets)}"
        f"{structure_info}\n\n"
        "Create a plan for extracting notes. Consider which notes need definitions, "
        "theorems, math, and examples. Plan the dependency relationships."
    )

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=PLAN_SYSTEM,
            max_tokens=8192,
            tier="advanced",
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            plan_data = json.loads(json_match.group())
        else:
            plan_data = json.loads(response)
        note_plan = plan_data.get("notes", [])
    except (json.JSONDecodeError, Exception):
        # Fallback: create one note per outline section
        note_plan = [
            {
                "title": section["title"],
                "parent_title": None,
                "tags": [],
                "chapter": section["title"],
                "page": section.get("page_start", 1),
                "level": section.get("level", 1),
                "scope": section.get("snippet", ""),
                "doc_type": "concept",
                "depends_on": [],
                "used_by": [],
            }
            for section in outline
        ]

    await set_step(state["document_id"], f"Planned {len(note_plan)} notes to create")

    return {
        **state,
        "note_plan": note_plan,
        "existing_tags": existing_tags_list,
        "existing_note_titles": existing_titles,
    }
