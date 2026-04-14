"""Chapter-plan node: per-chapter parallel note planning.

Runs after macro_plan.  For each high-value chapter identified by the macro
plan, spawns a parallel LLM call that plans the exact notes to create, with:
- sections: list[str] (which subsections each note covers)
- Accurate page ranges computed in Python from section_boundaries
- Coverage enforcement ensuring every chapter subsection appears in a note
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import re

from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ...prompts import load_prompt_builder
from ...services.model_provider import get_provider, get_setting
from ..progress import set_step
from ..state import ProcessingState

logger = logging.getLogger(__name__)


# ── Page-range computation (pure Python) ─────────────────────────────


def _build_char_page_map(page_texts: list[dict]) -> list[int]:
    """Return cumulative end-char offset per page (0-indexed list)."""
    offsets: list[int] = []
    total = 0
    for pt in page_texts:
        total += len(pt.get("text", ""))
        offsets.append(total)
    return offsets


def _char_to_page(char_offset: int, cumulative: list[int]) -> int:
    """1-indexed page number for a character offset."""
    idx = bisect.bisect_right(cumulative, char_offset)
    return min(idx + 1, len(cumulative))


def _compute_page_range(
    sections: list[str],
    section_boundaries: list[dict],
    char_page_map: list[int],
    fallback_page: int = 1,
) -> tuple[int, int]:
    """Return (page_start, page_end) for a list of section titles."""
    if not sections or not section_boundaries or not char_page_map:
        return fallback_page, fallback_page

    sections_lower = {s.lower().strip() for s in sections}
    matches = [
        b for b in section_boundaries
        if b.get("title", "").lower().strip() in sections_lower
    ]
    if not matches:
        return fallback_page, fallback_page

    page_start = min(b.get("page", fallback_page) for b in matches)
    max_end_char = max(b.get("end_char", 0) for b in matches)
    page_end = _char_to_page(max_end_char, char_page_map)
    return page_start, max(page_end, page_start)


# ── Outline grouping ──────────────────────────────────────────────────


def _group_outline_by_chapter(
    outline: list[dict],
    high_value_chapters: list[dict],
    skipped: set[str],
) -> dict[str, list[dict]]:
    """Map chapter title → its level-2+ subsections from the outline.

    Walks the outline in order; each level-1 section starts a new chapter
    bucket.  Level-2+ sections are appended to the current bucket.
    Skipped chapter titles and their subsections are omitted.
    """
    chapter_titles = {ch["title"] for ch in high_value_chapters}
    result: dict[str, list[dict]] = {ch["title"]: [] for ch in high_value_chapters}

    current_chapter: str | None = None
    include_current = False

    for section in outline:
        level = section.get("level", 1)
        title = section.get("title", "").strip()

        if level == 1:
            current_chapter = title
            include_current = title in chapter_titles and title not in skipped
        elif include_current and current_chapter:
            result.setdefault(current_chapter, []).append(section)

    return result


# ── Per-chapter LLM planning ──────────────────────────────────────────


async def _plan_one_chapter(
    chapter_info: dict,
    chapter_outline: list[dict],
    state: ProcessingState,
    provider,
    model: str,
    existing_tags_list: list[str],
    existing_titles: list[str],
    char_page_map: list[int],
    doc_type: str,
    tree_text: str,
    index_notes: list,
) -> list[dict]:
    """Plan notes for a single chapter.  Returns a list of note plan entries."""
    chapter_title = chapter_info["title"]
    chapter_folder = chapter_info.get("folder", chapter_title)
    subsection_strategy = chapter_info.get("subsection_strategy", "one_per_subsection")
    grouping_hint = chapter_info.get("grouping_hint", "")
    original_name = state["original_name"]
    section_boundaries = state.get("section_boundaries", [])

    # Subsections available in this chapter
    chapter_sections_json = json.dumps(
        [{"title": s["title"], "level": s["level"], "page_start": s.get("page_start", 1)}
         for s in chapter_outline],
        indent=2,
    )

    # Build prompt (chapter-scoped)
    prompt_builder = (
        load_prompt_builder("plan")
        .include("role", "doc_type_strategy", "chapter_context",
                 "tag_rules", "callout_planning", "dependency_rules", "output_schema")
        .include_if(doc_type == "textbook", "textbook_rules")
        .include_if(doc_type == "paper", "paper_rules")
        .include_if(doc_type == "tutorial", "tutorial_rules")
        .include_if(bool(index_notes), "existing_tree")
        .include_if(bool(index_notes), "hierarchy_rules")
    )
    system_template = prompt_builder.build()

    fmt_vars: dict = {
        "doc_type": doc_type,
        "chapter_title": chapter_title,
        "chapter_folder": chapter_folder,
        "subsection_strategy": subsection_strategy,
        "grouping_hint": grouping_hint or "Follow the subsection_strategy.",
        "chapter_sections_json": chapter_sections_json,
    }
    if index_notes:
        fmt_vars["existing_tree"] = tree_text

    system_prompt = system_template.format(**fmt_vars)

    user_prompt = (
        f"Document: {original_name}\n"
        f"Document type: {doc_type}\n"
        f"Chapter: {chapter_title}\n\n"
        f"Existing tags: {json.dumps(existing_tags_list)}\n\n"
        f"Existing notes for depends_on/used_by:\n"
        + "\n".join(f"- {t}" for t in existing_titles[:40])
        + f"\n\nPlan the notes for this chapter."
    )

    note_plan_entries: list[dict] = []
    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": user_prompt}],
            system=system_prompt,
            max_tokens=4096,
            model=model,
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(response)
        note_plan_entries = data.get("notes", [])
    except Exception as exc:
        logger.warning("Chapter plan LLM failed for '%s': %s", chapter_title, exc)

    # Validate sections references — drop refs to sections not in this chapter
    valid_section_titles = {s["title"].lower().strip() for s in chapter_outline}
    for entry in note_plan_entries:
        raw_sections = entry.get("sections") or []
        entry["sections"] = [
            s for s in raw_sections
            if s.lower().strip() in valid_section_titles
        ]
        # Ensure folder is set
        if not (entry.get("folder") or "").strip():
            entry["folder"] = chapter_folder

    # ── Coverage enforcement ──────────────────────────────────────────
    # Every subsection in chapter_outline must appear in at least one note
    covered_sections: set[str] = {
        s.lower().strip()
        for entry in note_plan_entries
        for s in entry.get("sections", [])
    }
    for section in chapter_outline:
        section_title = section.get("title", "").strip()
        if not section_title:
            continue
        if section_title.lower().strip() not in covered_sections:
            note_plan_entries.append({
                "title": section_title,
                "folder": chapter_folder,
                "sections": [section_title],
                "tags": [],
                "page_start": section.get("page_start", 1),
                "page_end": section.get("page_start", 1),
                "level": section.get("level", 2),
                "scope": f"Content from the '{section_title}' section.",
                "doc_type": "concept",
                "has_definitions": False,
                "has_theorems": False,
                "has_math": False,
                "depends_on": [],
                "used_by": [],
            })
            covered_sections.add(section_title.lower().strip())

    # ── Page range computation (pure Python) ─────────────────────────
    for entry in note_plan_entries:
        sections = entry.get("sections") or []
        llm_page_start = entry.get("page_start") or 1
        computed_start, computed_end = _compute_page_range(
            sections, section_boundaries, char_page_map, llm_page_start
        )
        entry["page_start"] = computed_start
        entry["page_end"] = computed_end
        # Legacy compat field
        entry["page"] = computed_start
        # chapter field for backward compat: join section titles
        entry["chapter"] = "; ".join(sections) if sections else chapter_title

    return note_plan_entries


# ── Helper: build tree text from index notes ──────────────────────────


def _build_tree_text(index_notes: list) -> str:
    if not index_notes:
        return "(empty — no topics yet)"
    paths: list[str] = []
    for n in index_notes:
        path = n.title.removeprefix("Index: ").strip()
        if path:
            paths.append(path)
    if not paths:
        return "(empty — no topics yet)"
    paths.sort(key=lambda p: (p.count("/"), p))
    lines = []
    for path in paths:
        depth = path.count("/")
        leaf = path.rsplit("/", 1)[-1] if "/" in path else path
        lines.append("  " * depth + f"- {leaf}/")
    return "\n".join(lines)


# ── Fallback plan helpers (kept for backward compat / escalation) ─────


def _build_fallback_plan(outline: list[dict], doc_name: str) -> list[dict]:
    """Build a minimal note plan from the outline when LLM response is unusable."""
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
            "folder": folder,
            "sections": [title],
            "chapter": title,
            "tags": [],
            "page_start": section.get("page_start", 1),
            "page_end": section.get("page_start", 1),
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
    """Fill empty `folder` fields in-place using surrounding context."""
    level_folder: dict[int, str] = {}
    for entry in note_plan:
        folder = (entry.get("folder") or "").strip()
        level = entry.get("level", 1)
        if folder:
            level_folder[level] = folder

    running: dict[int, str] = {}
    for entry in note_plan:
        folder = (entry.get("folder") or "").strip()
        level = entry.get("level", 1)
        if folder:
            running[level] = folder
        else:
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


# ── Main node ─────────────────────────────────────────────────────────


async def create_chapter_plans(state: ProcessingState) -> ProcessingState:
    """Plan notes chapter-by-chapter with parallel LLM calls."""
    await set_step(state["document_id"], "Planning notes by chapter...")

    macro_plan = state.get("macro_plan") or {}
    skipped = set(state.get("skipped_sections") or [])
    original_name = state["original_name"]
    outline = state["outline"]
    page_texts = state.get("page_texts", [])
    section_boundaries = state.get("section_boundaries", [])

    high_value_chapters: list[dict] = macro_plan.get("high_value_chapters", [])

    # If macro plan produced no chapters, use full outline as fallback
    if not high_value_chapters:
        logger.warning("No high_value_chapters in macro_plan — falling back to full outline")
        note_plan = _build_fallback_plan(outline, original_name)
        _backfill_folders(note_plan, original_name)
        return {**state, "note_plan": note_plan, "existing_tags": [], "existing_note_titles": []}

    # Fetch DB data
    async with async_session() as db:
        tags_result = await db.execute(select(Note.tags).where(Note.space_id == state["space_id"]))
        all_tags_raw = tags_result.scalars().all()

        titles_result = await db.execute(select(Note.title).where(Note.space_id == state["space_id"]))
        existing_titles = list(titles_result.scalars().all())

        index_result = await db.execute(
            select(Note).where(Note.title.like("Index: %")).where(Note.space_id == state["space_id"])
        )
        index_notes = index_result.scalars().all()

        provider = await get_provider(db)
        model = await get_setting(db, "model_chapter_plan")

    existing_tags: set[str] = set()
    for tags_raw in all_tags_raw:
        if tags_raw:
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
                existing_tags.update(tags)
            except (json.JSONDecodeError, TypeError):
                pass
    existing_tags_list = sorted(existing_tags)

    tree_text = _build_tree_text(index_notes)
    doc_type = state.get("doc_type", "article")
    char_page_map = _build_char_page_map(page_texts)

    # Group outline subsections by chapter
    chapter_sections = _group_outline_by_chapter(outline, high_value_chapters, skipped)

    # Parallel chapter planning with concurrency limit
    sem = asyncio.Semaphore(6)

    async def _throttled(ch_info: dict) -> list[dict] | Exception:
        async with sem:
            try:
                return await _plan_one_chapter(
                    chapter_info=ch_info,
                    chapter_outline=chapter_sections.get(ch_info["title"], []),
                    state=state,
                    provider=provider,
                    model=model,
                    existing_tags_list=existing_tags_list,
                    existing_titles=existing_titles,
                    char_page_map=char_page_map,
                    doc_type=doc_type,
                    tree_text=tree_text,
                    index_notes=index_notes,
                )
            except Exception as exc:
                logger.warning("Chapter plan failed for '%s': %s", ch_info.get("title"), exc)
                return exc

    results = await asyncio.gather(
        *[_throttled(ch) for ch in high_value_chapters],
        return_exceptions=True,
    )

    note_plan: list[dict] = []
    for r in results:
        if isinstance(r, list):
            note_plan.extend(r)
        # Exceptions already logged in _throttled

    # If all chapters failed, fall back to full outline
    if not note_plan:
        logger.warning("All chapter plans failed — falling back to full outline plan")
        note_plan = _build_fallback_plan(outline, original_name)

    # ── Folder backfill ───────────────────────────────────────────────
    _backfill_folders(note_plan, macro_plan.get("folder_root", original_name))

    # ── Bidirectional dependency enforcement ──────────────────────────
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

    await set_step(
        state["document_id"],
        f"Planned {len(note_plan)} notes across {len(high_value_chapters)} chapters",
    )
    logger.info("Chapter plans complete: %d notes planned", len(note_plan))

    return {
        **state,
        "note_plan": note_plan,
        "existing_tags": existing_tags_list,
        "existing_note_titles": existing_titles,
    }


# Alias so old checkpoints importing create_plan still resolve
create_plan = create_chapter_plans
