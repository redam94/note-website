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
from ...schemas.note_output import NoteOutput
from ...services.model_provider import get_provider, get_setting
from ..progress import set_step
from ..state import ProcessingState

NOTE_SYSTEM = load_prompt("create_note").format()

_CONCURRENCY = 6
_MIN_QUALITY_CHARS = 500  # minimum body length to accept without escalation

logger = logging.getLogger(__name__)


def _ensure_callout_block_ids(text: str) -> str:
    """Append ^block-ids to [!definition] and [!theorem] callouts that lack one.

    A callout is a run of lines starting with '>'.  We detect the callout type
    from the opening line and append a slug id if the last line of the callout
    doesn't already start with '> ^'.
    """
    _CALLOUT_OPEN = re.compile(r"^>\s*\[!(definition|theorem|lemma|corollary|proposition)\]\s*(.*)", re.IGNORECASE)
    _BLOCK_ID = re.compile(r"^>\s*\^")

    lines = text.split("\n")
    result: list[str] = []
    i = 0
    while i < len(lines):
        m = _CALLOUT_OPEN.match(lines[i])
        if m:
            callout_type = m.group(1).lower()
            callout_name = m.group(2).strip()
            # Collect the entire callout block
            block_start = i
            block: list[str] = [lines[i]]
            i += 1
            while i < len(lines) and lines[i].startswith(">"):
                block.append(lines[i])
                i += 1
            # Check if last content line already has a block id
            last_content = next(
                (l for l in reversed(block) if l.strip() not in (">", "> ")),
                "",
            )
            if not _BLOCK_ID.match(last_content):
                # Generate a slug from type + name
                raw = f"{callout_type}-{callout_name}" if callout_name else callout_type
                # Simple slug: lowercase, replace spaces/special chars with -
                slug = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")[:40]
                block.append(f"> ^{slug}")
            result.extend(block)
        else:
            result.append(lines[i])
            i += 1
    return "\n".join(result)


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


# ── Folder → parent_id resolution ───────────────────────────────────


def _resolve_parent_id(folder: str, folder_id_map: dict[str, int]) -> int | None:
    """Return the index-note id for a given folder path.

    Tries in order:
    1. Exact match (after stripping whitespace)
    2. Case-insensitive exact match
    3. Best prefix match — longest folder path that is a prefix of `folder`
       (handles cases where the LLM used a more-specific path than what was
       created, or a deeper path that wasn't broken into intermediate nodes)
    4. None — note becomes a top-level root
    """
    if not folder or not folder_id_map:
        return None

    # 1. Exact
    if folder in folder_id_map:
        return folder_id_map[folder]

    # 2. Case-insensitive
    folder_lower = folder.lower()
    for path, note_id in folder_id_map.items():
        if path.lower() == folder_lower:
            return note_id

    # 3. Best prefix — most specific folder that is an ancestor of `folder`
    best_path = ""
    best_id: int | None = None
    for path, note_id in folder_id_map.items():
        # A path is a valid prefix if folder starts with it followed by /
        if folder_lower.startswith(path.lower() + "/") or folder_lower.startswith(path.lower()):
            if len(path) > len(best_path):
                best_path = path
                best_id = note_id

    return best_id


# ── Source text extraction (boundary-aware) ───────────────────────────


def _get_section_text(
    raw_text: str,
    page_texts: list[dict],
    page: int,
    outline: list[dict] | None = None,
    chapter: str | None = None,
    sections: list[str] | None = None,
    section_boundaries: list[dict] | None = None,
    extracted_tables: list[dict] | None = None,
    extracted_equations: list[dict] | None = None,
    max_chars: int = 10_000,
) -> str:
    """Extract source text for a note using the best available method.

    Priority: multi-section char offsets > single chapter offset >
              outline page ranges > page ± window.
    Appends relevant tables and equations if available.
    """
    main_text = ""

    # Method 1a: Multi-section character offsets (new two-phase plan format)
    if section_boundaries and sections:
        sections_lower = {s.lower().strip() for s in sections}
        parts = []
        total = 0
        for b in section_boundaries:
            if b.get("title", "").lower().strip() in sections_lower:
                chunk = raw_text[b["start_char"]:b["end_char"]]
                if total + len(chunk) > max_chars:
                    parts.append(chunk[:max_chars - total])
                    total = max_chars
                    break
                parts.append(chunk)
                total += len(chunk)
        if parts:
            main_text = "\n\n".join(parts)

    # Method 1b: Single-section character offset (backward compat)
    if not main_text and section_boundaries and chapter:
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
    aliases: list[str] | None = None,
) -> str:
    lines = ["---"]
    lines.append(f'title: "{title}"')
    if aliases:
        lines.append("aliases:")
        for alias in aliases:
            lines.append(f'  - "{alias}"')
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


def _stub_reason(note: NoteOutput) -> str | None:
    """Return why this note is a stub, or None if it passes quality."""
    content = note.assemble_markdown()
    if len(content) < _MIN_QUALITY_CHARS:
        return f"total content too short ({len(content)}/{_MIN_QUALITY_CHARS} chars)"
    if len(note.main_content) < 200:
        return f"main_content too short ({len(note.main_content)}/200 chars)"
    if "Content extracted from" in content and content.count("##") <= 2:
        return "boilerplate extraction placeholder"
    return None


def _is_stub(note: NoteOutput) -> bool:
    return _stub_reason(note) is not None


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
        model = await get_setting(db, "model_create")
        result = await db.execute(select(Note.slug))
        existing_slugs = set(result.scalars().all())

    title_list = [p["title"] for p in note_plan]
    existing_titles = state.get("existing_note_titles", [])
    all_linkable = list(set(title_list + existing_titles))[:50]
    total_planned = len(note_plan)

    await set_step(document_id, f"Creating {total_planned} notes...", notes_count=0)

    sem = asyncio.Semaphore(_CONCURRENCY)
    completed = 0

    async def _generate_one(idx: int, plan_entry: dict) -> tuple[int, dict, NoteOutput, bool]:
        nonlocal completed
        async with sem:
            page = plan_entry.get("page_start") or plan_entry.get("page", 1)
            page_end = plan_entry.get("page_end") or page
            chapter = plan_entry.get("chapter")
            # Two-phase plan uses sections list; fall back to [chapter] for old plans
            sections = plan_entry.get("sections") or ([chapter] if chapter else [])

            # Get source text using multi-section extraction when available
            section_text = _get_section_text(
                raw_text=state["raw_text"],
                page_texts=page_texts,
                page=page,
                outline=outline,
                chapter=chapter,
                sections=sections or None,
                section_boundaries=state.get("section_boundaries"),
                extracted_tables=state.get("extracted_tables"),
                extracted_equations=state.get("extracted_equations"),
                max_chars=10_000,
            )

            linkable = [t for t in all_linkable if t != plan_entry["title"]]
            depends_str = ", ".join(plan_entry.get("depends_on", [])[:5])
            used_by_str = ", ".join(plan_entry.get("used_by", [])[:5])

            sections_display = "; ".join(sections) if sections else chapter or "N/A"
            page_range_display = f"pp. {page}–{page_end}" if page_end != page else f"p. {page}"

            base_prompt = (
                f"Document: {original_name}\n"
                f"Topic: {plan_entry['title']}\n"
                f"Folder: {plan_entry.get('folder', '')}\n"
                f"Scope: {plan_entry.get('scope', 'General coverage')}\n"
                f"Sections covered: {sections_display}\n"
                f"Pages: {page_range_display}\n"
                f"Depends on: {depends_str or 'none'}\n"
                f"Used by: {used_by_str or 'none'}\n\n"
                f"Available notes to link to with [[Note Title]]:\n"
                + "\n".join(f"- {t}" for t in linkable[:40])
                + f"\n\nSource text:\n{section_text}"
            )

            title = plan_entry["title"]
            logger.info(
                "[%d/%d] Generating '%s' | model=%s page=%s chapter=%s src=%d chars",
                idx + 1, total_planned, title, model, page, chapter or "-", len(section_text),
            )

            # ── Phase 1: Generate note ────────────────────────────────
            note_output: NoteOutput | None = None
            is_fallback = False
            try:
                note_output = await provider.complete_structured(
                    NoteOutput,
                    messages=[{"role": "user", "content": base_prompt}],
                    system=NOTE_SYSTEM,
                    max_tokens=8192,
                    model=model,
                )
                assembled = note_output.assemble_markdown()
                reason = _stub_reason(note_output)
                if reason:
                    logger.warning(
                        "  Phase 1 STUB '%s': %s | main=%d chars overview=%d chars",
                        title, reason, len(note_output.main_content), len(note_output.overview),
                    )
                else:
                    logger.info(
                        "  Phase 1 OK '%s': %d chars | main=%d overview=%d see_also=%d",
                        title, len(assembled), len(note_output.main_content),
                        len(note_output.overview), len(note_output.see_also),
                    )
            except Exception as e:
                logger.warning("  Phase 1 FAILED '%s': %s", title, e)

            # ── Phase 2: Retry with expanded context if still a stub ──
            if note_output is None or _is_stub(note_output):
                prior_draft = note_output.assemble_markdown() if note_output else ""
                prior_reason = _stub_reason(note_output) if note_output else "generation failed"

                expanded_text = _get_section_text(
                    raw_text=state["raw_text"],
                    page_texts=page_texts,
                    page=page,
                    outline=outline,
                    chapter=chapter,
                    sections=sections or None,
                    section_boundaries=state.get("section_boundaries"),
                    extracted_tables=state.get("extracted_tables"),
                    extracted_equations=state.get("extracted_equations"),
                    max_chars=15_000,
                )
                logger.info(
                    "  Phase 2 retry '%s' (was: %s) | expanded src=%d chars",
                    title, prior_reason, len(expanded_text),
                )

                escalation_prompt = base_prompt.replace(section_text, expanded_text)
                if prior_draft:
                    escalation_prompt += (
                        f"\n\nA previous attempt produced this insufficient draft "
                        f"(too brief or missing sections). Rewrite with much more detail, "
                        f"specific formulas, examples, and explanations:\n\n{prior_draft[:2000]}"
                    )

                try:
                    retry_output = await provider.complete_structured(
                        NoteOutput,
                        messages=[{"role": "user", "content": escalation_prompt}],
                        system=NOTE_SYSTEM,
                        max_tokens=8192,
                        model=model,
                    )
                    retry_reason = _stub_reason(retry_output)
                    if retry_reason:
                        logger.warning("  Phase 2 still STUB '%s': %s", title, retry_reason)
                    else:
                        assembled = retry_output.assemble_markdown()
                        logger.info(
                            "  Phase 2 OK '%s': %d chars | main=%d overview=%d",
                            title, len(assembled), len(retry_output.main_content), len(retry_output.overview),
                        )
                        note_output = retry_output
                except Exception as e:
                    logger.warning("  Phase 2 FAILED '%s': %s", title, e)

            # ── Fallback: embed real source text ──────────────────────
            if note_output is None or _is_stub(note_output):
                final_reason = _stub_reason(note_output) if note_output else "all phases failed"
                logger.warning(
                    "  FALLBACK '%s': creating stub with raw source text (%s)",
                    title, final_reason,
                )
                is_fallback = True
                source_excerpt = section_text[:4000].strip()
                note_output = NoteOutput(
                    summary=f"Key concepts from {title} in {original_name}.",
                    overview=(
                        f"This note covers {plan_entry.get('scope', title)} "
                        f"from {original_name}"
                        f"{f', Chapter: {chapter}' if chapter else ''}"
                        f"{f', p. {page}' if page else ''}."
                    ),
                    main_content=f"## Source Material\n\n{source_excerpt}",
                    examples="See source document for worked examples.",
                    connections="Related to other topics covered in this document.",
                    see_also=linkable[:8],
                )

            completed += 1
            await set_step(
                document_id,
                f"Creating notes... ({completed}/{total_planned} complete)",
                notes_count=completed,
            )
            return (idx, plan_entry, note_output, is_fallback)

    results = await asyncio.gather(
        *[_generate_one(i, entry) for i, entry in enumerate(note_plan)],
        return_exceptions=True,
    )

    # ── Write to DB in plan order (preserves parent-child) ────────────
    slug_map: dict[str, int] = {}
    created_notes = []

    ordered = sorted(
        [r for r in results if not isinstance(r, BaseException)],
        key=lambda x: x[0],
    )

    now = datetime.now(timezone.utc).isoformat()

    stub_count = sum(1 for r in ordered if r[3])

    for idx, plan_entry, note_output, note_is_fallback in ordered:
        base_slug = slugify(plan_entry["title"], lowercase=True)
        slug = base_slug
        counter = 1
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing_slugs.add(slug)

        # Primary: use folder path to assign parent to the tree index note
        folder = (plan_entry.get("folder") or "").strip()
        folder_id_map = state.get("folder_id_map", {})
        parent_id = _resolve_parent_id(folder, folder_id_map)

        tags = plan_entry.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]

        doc_type = plan_entry.get("doc_type", "concept")
        depends_on = plan_entry.get("depends_on", [])
        used_by = plan_entry.get("used_by", [])

        chapter = plan_entry.get("chapter")
        page = plan_entry.get("page_start") or plan_entry.get("page", 1)
        page_end = plan_entry.get("page_end") or page

        if page_end and page_end != page:
            source_location = f"pp. {page}–{page_end}"
        elif page:
            source_location = f"p. {page}"
        else:
            source_location = ""
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
            aliases=note_output.aliases or [],
        )

        assembled = note_output.assemble_markdown()
        assembled = _ensure_callout_block_ids(assembled)
        body = _normalize_content(assembled)
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
            page=int(page) if page else None,
            summary=note_output.summary,
            space_id=state["space_id"],
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
                "is_stub": note_is_fallback,
            }
        )

    logger.info(
        "create_notes complete: %d notes created, %d stubs",
        len(created_notes), stub_count,
    )
    return {**state, "created_notes": created_notes, "stub_count": stub_count}
