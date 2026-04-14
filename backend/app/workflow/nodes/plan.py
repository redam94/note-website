from __future__ import annotations

import json
import re

from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt_builder
from ...services.model_provider import get_provider, get_setting
from ..progress import set_step
from ..state import ProcessingState

def _build_fallback_plan(outline: list[dict], doc_name: str) -> list[dict]:
    """Build a minimal note plan from the outline when the LLM response is unusable.

    Level-1 sections map to top-level folders named after the document.
    Level-2 sections get a sub-folder under their nearest level-1 parent.
    """
    plan = []
    current_l1_title = doc_name

    for section in outline:
        level = section.get("level", 1)
        title = section.get("title", "").strip()
        if not title:
            continue

        if level == 1:
            current_l1_title = title
            folder = doc_name
        else:
            folder = f"{doc_name}/{current_l1_title}"

        plan.append({
            "title": title,
            "parent_title": None,
            "folder": folder,
            "tags": [],
            "chapter": title,
            "page": section.get("page_start", 1),
            "level": level,
            "scope": section.get("snippet", ""),
            "doc_type": "concept",
            "has_definitions": False,
            "has_theorems": False,
            "has_math": False,
            "depends_on": [],
            "used_by": [],
        })
    return plan


def _backfill_folders(note_plan: list[dict], doc_name: str) -> None:
    """Fill empty `folder` fields in-place using surrounding context.

    Strategy:
    - Collect all non-empty folders already assigned by the LLM.
    - For entries with empty/missing folder: look for the nearest preceding
      entry (by index) that has the same level and a non-empty folder.
      If none found, build `doc_name/chapter` as a safe default.
    """
    # Index of (level, folder) for entries that have a folder
    level_folder: dict[int, str] = {}

    for entry in note_plan:
        folder = (entry.get("folder") or "").strip()
        level = entry.get("level", 1)
        if folder:
            level_folder[level] = folder  # update running latest per level

    # Second pass: fill blanks
    running: dict[int, str] = {}
    for entry in note_plan:
        folder = (entry.get("folder") or "").strip()
        level = entry.get("level", 1)

        if folder:
            running[level] = folder
        else:
            # Try: same level from running window, then parent level, then fallback
            inferred = (
                running.get(level)
                or running.get(level - 1)
                or level_folder.get(level)
                or level_folder.get(level - 1)
            )
            if not inferred:
                chapter = (entry.get("chapter") or entry.get("title") or "").strip()
                inferred = f"{doc_name}/{chapter}" if chapter else doc_name
            entry["folder"] = inferred
            running[level] = inferred


def _infer_folder_for_section(
    section: dict, note_plan: list[dict], doc_name: str
) -> str:
    """Return a folder path for an injected coverage-gap note.

    Looks for an existing plan entry covering the same outline level and
    reuses its folder.  Falls back to doc_name/{section_title}.
    """
    level = section.get("level", 1)
    section_title = section.get("title", "").strip()

    # Look for any already-planned note at the same level with a non-empty folder
    for entry in note_plan:
        if entry.get("level") == level and (entry.get("folder") or "").strip():
            return (entry.get("folder") or "").strip()

    # Fall back to a simple path derived from the document + section title
    return f"{doc_name}/{section_title}" if level > 1 else doc_name


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


def _build_tree_text(index_notes: list) -> str:
    """Reconstruct the topic tree as indented text from index notes."""
    if not index_notes:
        return "(empty — no topics yet)"

    paths: list[str] = []

    for n in index_notes:
        # Extract path from title: "Index: Statistics/Bayesian Methods" -> "Statistics/Bayesian Methods"
        path = n.title.removeprefix("Index: ").strip()
        if path:
            paths.append(path)

    if not paths:
        return "(empty — no topics yet)"

    # Sort by depth then alphabetically for clean indented output
    paths.sort(key=lambda p: (p.count("/"), p))
    lines = []
    for path in paths:
        depth = path.count("/")
        leaf = path.rsplit("/", 1)[-1] if "/" in path else path
        lines.append("  " * depth + f"- {leaf}/")

    return "\n".join(lines)


async def create_plan(state: ProcessingState) -> ProcessingState:
    """Plan what notes to create based on the outline."""
    await set_step(state["document_id"], "Planning note extraction...")
    outline = state["outline"]
    original_name = state["original_name"]

    # Fetch existing tags, note titles, and topic tree from DB
    async with async_session() as db:
        tags_result = await db.execute(select(Note.tags).where(Note.space_id == state["space_id"]))
        all_tags_raw = tags_result.scalars().all()

        titles_result = await db.execute(select(Note.title).where(Note.space_id == state["space_id"]))
        existing_titles = [t for t in titles_result.scalars().all()]

        # Query existing index notes for the topic tree
        index_result = await db.execute(
            select(Note).where(Note.title.like("Index: %")).where(Note.space_id == state["space_id"])
        )
        index_notes = index_result.scalars().all()

        provider = await get_provider(db)
        model = await get_setting(db, "model_plan")

    tree_text = _build_tree_text(index_notes)

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

    # Build system prompt — conditional sections based on doc_type and state
    doc_type = state.get("doc_type", "article")

    prompt = (
        f"Document: {original_name}\n"
        f"Document type: {doc_type}\n\n"
        f"Existing tags in knowledge base: {json.dumps(existing_tags_list)}\n\n"
        f"Existing notes in knowledge base (for depends_on/used_by references):\n"
        + "\n".join(f"- {t}" for t in existing_titles[:50])
        + f"\n\nDocument outline:\n{json.dumps(outline, indent=2)}\n\n"
        f"Section previews:\n{''.join(snippets)}"
        f"{structure_info}\n\n"
        "Create a plan for extracting notes. Consider which notes need definitions, "
        "theorems, math, and examples. Plan the dependency relationships."
    )
    plan_prompt_builder = (
        load_prompt_builder("plan")
        .include("role", "doc_type_strategy", "hierarchy_rules", "tag_rules",
                 "callout_planning", "dependency_rules", "output_schema")
        .include_if(doc_type == "textbook", "textbook_rules")
        .include_if(doc_type == "paper", "paper_rules")
        .include_if(doc_type == "tutorial", "tutorial_rules")
        .include_if(bool(index_notes), "existing_tree")
    )
    plan_template = plan_prompt_builder.build()
    fmt_vars: dict = {"doc_type": doc_type}
    if index_notes:
        fmt_vars["existing_tree"] = tree_text
    PLAN_SYSTEM = plan_template.format(**fmt_vars)

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=PLAN_SYSTEM,
            max_tokens=8192,
            model=model,
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            plan_data = json.loads(json_match.group())
        else:
            plan_data = json.loads(response)
        note_plan = plan_data.get("notes", [])
    except (json.JSONDecodeError, Exception):
        # Fallback: create one note per outline section, building folders from
        # the outline hierarchy so notes are properly placed in the tree.
        note_plan = _build_fallback_plan(outline, original_name)

    # ── Folder backfill ───────────────────────────────────────────────
    # Some LLM responses omit the `folder` field or return an empty string.
    # Fill those gaps using a heuristic based on what nearby notes were assigned.
    _backfill_folders(note_plan, original_name)

    # ── Coverage enforcement ──────────────────────────────────────────
    # For every level-1 and level-2 outline section, ensure at least one
    # planned note has its "chapter" field matching that section.  This
    # mechanical check means section coverage is guaranteed regardless of
    # what the LLM chose to include.
    planned_chapters: set[str] = {
        (p.get("chapter") or "").strip().lower()
        for p in note_plan
        if p.get("chapter")
    }
    injected = 0
    for section in outline:
        if section.get("level", 1) > 2:
            continue
        section_title = section.get("title", "").strip()
        if not section_title:
            continue
        if section_title.lower() not in planned_chapters:
            # Infer a folder from existing plan entries for similar-level sections
            inferred_folder = _infer_folder_for_section(
                section, note_plan, original_name
            )
            note_plan.append({
                "title": section_title,
                "parent_title": None,
                "folder": inferred_folder,
                "tags": [],
                "chapter": section_title,
                "page": section.get("page_start", 1),
                "level": section.get("level", 1),
                "scope": section.get("snippet", f"Content from the '{section_title}' section."),
                "doc_type": "concept",
                "has_definitions": False,
                "has_theorems": False,
                "has_math": False,
                "depends_on": [],
                "used_by": [],
            })
            planned_chapters.add(section_title.lower())
            injected += 1

    # ── Bidirectional dependency enforcement ──────────────────────────
    # For every A.depends_on B, ensure B.used_by includes A (and vice-versa).
    # This is pure Python — no LLM trust required.
    title_to_plan: dict[str, dict] = {p["title"]: p for p in note_plan}
    for entry in note_plan:
        title = entry["title"]
        for dep in list(entry.get("depends_on", [])):
            if dep in title_to_plan:
                used_by = title_to_plan[dep].setdefault("used_by", [])
                if title not in used_by:
                    used_by.append(title)
        for user in list(entry.get("used_by", [])):
            if user in title_to_plan:
                deps = title_to_plan[user].setdefault("depends_on", [])
                if title not in deps:
                    deps.append(title)

    if injected:
        await set_step(
            state["document_id"],
            f"Planned {len(note_plan)} notes ({injected} sections auto-added for full coverage)",
        )
    else:
        await set_step(state["document_id"], f"Planned {len(note_plan)} notes to create")

    return {
        **state,
        "note_plan": note_plan,
        "existing_tags": existing_tags_list,
        "existing_note_titles": existing_titles,
    }
