from __future__ import annotations

import asyncio
import json
import logging
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

_CONCURRENCY = 6
_MIN_QUALITY_CHARS = 500  # minimum body length to accept without escalation

logger = logging.getLogger(__name__)


def _normalize_content(text: str) -> str:
    """Clean up LLM-generated content before storage.

    Fixes common formatting issues:
    - Bare > lines (empty blockquote continuation) → > (with trailing space)
    - Inconsistent callout formatting
    - Display math on single lines
    - Excess blank lines inside callouts
    """
    lines = text.split("\n")
    result = []
    for line in lines:
        # Normalize bare > to > with space (empty continuation)
        if line.strip() == ">":
            result.append(">")  # keep it, but frontend now handles it
        # Fix single-line display math
        elif re.match(r"^\$\$.+\$\$$", line.strip()):
            inner = line.strip()[2:-2].strip()
            result.append("$$")
            result.append(inner)
            result.append("$$")
        else:
            result.append(line)
    return "\n".join(result)


# ── Source text extraction (boundary-aware) ───────────────────────────


def _get_section_text(
    raw_text: str,
    page_texts: list[dict],
    page: int,
    outline: list[dict] | None = None,
    chapter: str | None = None,
    section_boundaries: list[dict] | None = None,
    extracted_tables: list[dict] | None = None,
    extracted_equations: list[dict] | None = None,
    max_chars: int = 10_000,
) -> str:
    """Extract source text for a note using the best available method.

    Priority: char offsets > outline page ranges > page ± window.
    Appends relevant tables and equations if available.
    """
    main_text = ""

    # Method 1: Character offsets from section_boundaries (most precise)
    if section_boundaries and chapter:
        chapter_lower = chapter.lower().strip()
        for b in section_boundaries:
            if b["title"].lower().strip() == chapter_lower or chapter_lower in b["title"].lower():
                main_text = raw_text[b["start_char"]:b["end_char"]][:max_chars]
                break

    # Method 2: Outline page ranges
    if not main_text and outline and page_texts:
        matched_idx = None
        if chapter:
            chapter_lower = chapter.lower().strip()
            for i, entry in enumerate(outline):
                etitle = entry.get("title", "").lower().strip()
                if etitle == chapter_lower or chapter_lower in etitle or etitle in chapter_lower:
                    matched_idx = i
                    break
        if matched_idx is None:
            best_dist = 999
            for i, entry in enumerate(outline):
                dist = abs(entry.get("page_start", 1) - page)
                if dist < best_dist:
                    best_dist = dist
                    matched_idx = i

        if matched_idx is not None:
            start_page = outline[matched_idx].get("page_start", page)
            end_page = (
                outline[matched_idx + 1].get("page_start", start_page + 3)
                if matched_idx + 1 < len(outline)
                else len(page_texts)
            )
            parts = []
            total = 0
            for pt in page_texts[max(0, start_page - 1):min(len(page_texts), end_page)]:
                if total + len(pt["text"]) > max_chars:
                    parts.append(pt["text"][: max_chars - total])
                    break
                parts.append(pt["text"])
                total += len(pt["text"])
            main_text = "\n".join(parts)

    # Method 3: Page window fallback
    if not main_text and page_texts:
        parts = []
        total = 0
        for pt in page_texts[max(0, page - 2):min(len(page_texts), page + 2)]:
            if total + len(pt["text"]) > max_chars:
                parts.append(pt["text"][: max_chars - total])
                break
            parts.append(pt["text"])
            total += len(pt["text"])
        main_text = "\n".join(parts)

    # Append extracted tables for this section's page range
    if extracted_tables:
        relevant_tables = [t for t in extracted_tables if abs(t.get("page", 0) - page) <= 2]
        if relevant_tables:
            table_text = "\n\n--- Extracted Tables ---\n"
            for t in relevant_tables[:3]:
                rows = t.get("rows", [])
                if rows:
                    header = " | ".join(str(c) for c in rows[0])
                    sep = " | ".join("---" for _ in rows[0])
                    body = "\n".join(" | ".join(str(c) for c in row) for row in rows[1:8])
                    table_text += f"\n| {header} |\n| {sep} |\n| {body} |\n"
            main_text += table_text

    # Append relevant display equations
    if extracted_equations:
        # Find equations near this page's char range
        display_eqs = [e for e in extracted_equations if e.get("type") == "display"]
        if section_boundaries and chapter:
            for b in section_boundaries:
                if chapter.lower() in b["title"].lower():
                    display_eqs = [
                        e for e in display_eqs
                        if b["start_char"] <= e.get("char_offset", 0) <= b["end_char"]
                    ]
                    break

        if display_eqs:
            eq_text = "\n\n--- Key Equations ---\n"
            for eq in display_eqs[:5]:
                eq_text += f"\n$$\n{eq['content']}\n$$\n"
                if eq.get("context"):
                    eq_text += f"Context: {eq['context']}\n"
            main_text += eq_text

    return main_text


# ── Frontmatter builder ──────────────────────────────────────────────


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


# ── Quality check ────────────────────────────────────────────────────


def _is_stub(note_data: dict) -> bool:
    """Check if the generated note is too shallow to accept."""
    content = note_data.get("content", "")
    if len(content) < _MIN_QUALITY_CHARS:
        return True
    if "Content extracted from" in content and content.count("##") <= 2:
        return True
    # Check it has at least Overview + one more section
    headings = re.findall(r"^##\s+", content, re.MULTILINE)
    if len(headings) < 2:
        return True
    return False


def _parse_llm_response(response: str) -> dict | None:
    """Try to parse the LLM response as JSON with summary+content."""
    try:
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(response)
        if "content" in data:
            return data
    except (json.JSONDecodeError, ValueError):
        pass
    return None


# ── Main node ─────────────────────────────────────────────────────────


async def create_notes(state: ProcessingState) -> ProcessingState:
    """Create notes with Haiku-first, Sonnet-escalation strategy."""
    note_plan = state["note_plan"]
    page_texts = state["page_texts"]
    original_name = state["original_name"]
    document_id = state["document_id"]
    outline = state.get("outline", [])

    async with async_session() as db:
        provider = await get_provider(db)
        result = await db.execute(select(Note.slug))
        existing_slugs = set(result.scalars().all())

    title_list = [p["title"] for p in note_plan]
    existing_titles = state.get("existing_note_titles", [])
    all_linkable = list(set(title_list + existing_titles))[:50]
    total_planned = len(note_plan)

    await set_step(document_id, f"Creating {total_planned} notes...", notes_count=0)

    sem = asyncio.Semaphore(_CONCURRENCY)
    completed = 0

    async def _generate_one(idx: int, plan_entry: dict) -> tuple[int, dict, dict]:
        nonlocal completed
        async with sem:
            page = plan_entry.get("page", 1)
            chapter = plan_entry.get("chapter")

            # Get source text using outline for precise extraction
            section_text = _get_section_text(
                raw_text=state["raw_text"],
                page_texts=page_texts,
                page=page,
                outline=outline,
                chapter=chapter,
                section_boundaries=state.get("section_boundaries"),
                extracted_tables=state.get("extracted_tables"),
                extracted_equations=state.get("extracted_equations"),
                max_chars=10_000,
            )

            linkable = [t for t in all_linkable if t != plan_entry["title"]]
            depends_str = ", ".join(plan_entry.get("depends_on", [])[:5])
            used_by_str = ", ".join(plan_entry.get("used_by", [])[:5])

            base_prompt = (
                f"Document: {original_name}\n"
                f"Topic: {plan_entry['title']}\n"
                f"Folder: {plan_entry.get('folder', '')}\n"
                f"Scope: {plan_entry.get('scope', 'General coverage')}\n"
                f"Chapter/Section: {chapter or 'N/A'}\n"
                f"Page: {page}\n"
                f"Depends on: {depends_str or 'none'}\n"
                f"Used by: {used_by_str or 'none'}\n\n"
                f"Available notes to link to with [[Note Title]]:\n"
                + "\n".join(f"- {t}" for t in linkable[:40])
                + f"\n\nSource text from document:\n{section_text}\n\n"
                "Create a detailed, substantive note. Use [!definition], [!theorem], "
                "[!example] callouts with ^block-ids. Include LaTeX math. "
                "Cross-link to existing notes with [[wiki-links]]. "
                "Display math must have $$ on its own line."
            )

            # ── Phase 1: Try Haiku first ─────────────────────────────
            note_data = None
            try:
                response = await provider.complete(
                    messages=[{"role": "user", "content": base_prompt}],
                    system=NOTE_SYSTEM,
                    max_tokens=4096,
                    tier="simple",
                )
                note_data = _parse_llm_response(response)
            except Exception as e:
                logger.debug("Haiku failed for %s: %s", plan_entry["title"], e)

            # ── Phase 2: Escalate to Sonnet if Haiku produced a stub ──
            if note_data is None or _is_stub(note_data):
                haiku_draft = note_data.get("content", "") if note_data else ""

                # Get more source text for the retry
                expanded_text = _get_section_text(
                    raw_text=state["raw_text"],
                    page_texts=page_texts,
                    page=page,
                    outline=outline,
                    chapter=chapter,
                    section_boundaries=state.get("section_boundaries"),
                    extracted_tables=state.get("extracted_tables"),
                    extracted_equations=state.get("extracted_equations"),
                    max_chars=15_000,
                )

                escalation_prompt = base_prompt.replace(section_text, expanded_text)
                if haiku_draft:
                    escalation_prompt += (
                        f"\n\nA previous attempt produced this insufficient draft "
                        f"(too brief or missing sections). Rewrite with much more detail, "
                        f"specific formulas, examples, and explanations:\n\n{haiku_draft[:2000]}"
                    )

                try:
                    response = await provider.complete(
                        messages=[{"role": "user", "content": escalation_prompt}],
                        system=NOTE_SYSTEM,
                        max_tokens=4096,
                        tier="advanced",
                    )
                    sonnet_data = _parse_llm_response(response)
                    if sonnet_data and not _is_stub(sonnet_data):
                        note_data = sonnet_data
                except Exception as e:
                    logger.debug("Sonnet escalation failed for %s: %s", plan_entry["title"], e)

            # ── Fallback: embed real source text ──────────────────────
            if note_data is None or _is_stub(note_data):
                # Include actual source material instead of empty stub
                source_excerpt = section_text[:4000].strip()
                note_data = {
                    "summary": f"Key concepts from {plan_entry['title']} in {original_name}.",
                    "content": (
                        f"> [!summary]\n"
                        f"> Key concepts from {plan_entry['title']} in {original_name}.\n\n"
                        f"## Overview\n\n"
                        f"This note covers {plan_entry.get('scope', plan_entry['title'])} "
                        f"from {original_name}"
                        f"{f', Chapter: {chapter}' if chapter else ''}"
                        f"{f', p. {page}' if page else ''}.\n\n"
                        f"## Source Material\n\n"
                        f"{source_excerpt}\n\n"
                        f"## See Also\n\n"
                        + "\n".join(f"- [[{t}]]" for t in linkable[:8])
                    ),
                }

            completed += 1
            await set_step(
                document_id,
                f"Creating notes... ({completed}/{total_planned} complete)",
                notes_count=completed,
            )
            return (idx, plan_entry, note_data)

    results = await asyncio.gather(
        *[_generate_one(i, entry) for i, entry in enumerate(note_plan)],
        return_exceptions=True,
    )

    # ── Write to DB in plan order (preserves parent-child) ────────────
    slug_map: dict[str, int] = {}
    created_notes = []

    ordered = sorted(
        [(idx, entry, data) for idx, entry, data in results if not isinstance(data, BaseException)],
        key=lambda x: x[0],
    )

    now = datetime.now(timezone.utc).isoformat()

    for idx, plan_entry, note_data in ordered:
        base_slug = slugify(plan_entry["title"], lowercase=True)
        slug = base_slug
        counter = 1
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing_slugs.add(slug)

        # Primary: use folder path to assign parent to the tree index note
        folder = plan_entry.get("folder", "")
        folder_id_map = state.get("folder_id_map", {})
        parent_id = folder_id_map.get(folder)
        # Fallback: use parent_title within current document
        if parent_id is None:
            parent_title = plan_entry.get("parent_title")
            parent_id = slug_map.get(parent_title) if parent_title else None

        tags = plan_entry.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        doc_type = plan_entry.get("doc_type", "concept")
        depends_on = plan_entry.get("depends_on", [])
        if parent_title:
            depends_on = [parent_title] + [d for d in depends_on if d != parent_title]
        used_by = plan_entry.get("used_by", [])

        chapter = plan_entry.get("chapter")
        page = plan_entry.get("page", 1)
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

        body = _normalize_content(note_data.get("content", ""))
        full_content = f"{frontmatter}\n\n# {plan_entry['title']}\n\n{body}"

        note = Note(
            document_id=state["document_id"],
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
