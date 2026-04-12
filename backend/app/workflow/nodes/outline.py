import json
import re

from ...database import async_session
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

_system_prompt = load_prompt("outline")


def _extract_header_hints(text: str) -> str:
    lines = text.split("\n")
    hints = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,4}\s+", stripped):
            hints.append(f"Line {i + 1}: {stripped}")
        elif stripped.isupper() and 3 < len(stripped) < 100:
            hints.append(f"Line {i + 1}: {stripped}")
        elif re.match(r"^\d+(\.\d+)*\.?\s+[A-Z]", stripped):
            hints.append(f"Line {i + 1}: {stripped}")
    return "\n".join(hints[:50])


async def detect_outline(state: ProcessingState) -> ProcessingState:
    """Build outline from pre-extracted structure, falling back to LLM."""
    doc_id = state["document_id"]
    extracted_toc = state.get("extracted_toc", [])
    section_boundaries = state.get("section_boundaries", [])
    raw_text = state["raw_text"]
    page_texts = state["page_texts"]

    # ── Fast path: use pre-extracted TOC/headings if available ────────
    if extracted_toc and len(extracted_toc) >= 2:
        await set_step(doc_id, f"Using extracted TOC ({len(extracted_toc)} entries)")

        outline = []
        for entry in extracted_toc:
            # Get a snippet from the section
            snippet = ""
            for b in section_boundaries:
                if b["title"] == entry["title"]:
                    snippet = raw_text[b["start_char"]:b["start_char"] + 150].strip()
                    break

            outline.append({
                "title": entry["title"],
                "level": min(entry.get("level", 1), 3),
                "page_start": entry.get("page", 1),
                "snippet": snippet[:100],
            })

        await set_step(doc_id, f"Outline: {len(outline)} sections from document structure")
        return {**state, "outline": outline}

    # Check if headings were extracted from font analysis or markdown
    headings = []
    for b in section_boundaries:
        headings.append({
            "title": b["title"],
            "level": b.get("page", 1),  # approximate
            "page_start": b.get("page", 1),
            "snippet": raw_text[b["start_char"]:b["start_char"] + 100].strip(),
        })

    if headings and len(headings) >= 3:
        # Normalize levels
        levels = sorted(set(h.get("level", 1) for h in headings))
        level_map = {lv: i + 1 for i, lv in enumerate(levels[:3])}
        for h in headings:
            h["level"] = level_map.get(h.get("level", 1), 1)

        await set_step(doc_id, f"Outline: {len(headings)} sections from heading analysis")
        return {**state, "outline": headings}

    # ── Slow path: LLM-based outline detection ───────────────────────
    await set_step(doc_id, "Detecting document structure with LLM...")

    text_preview = raw_text[:3000]
    header_hints = _extract_header_hints(raw_text)

    # Include pre-extracted data as context for the LLM
    extra_context = ""
    tables = state.get("extracted_tables", [])
    equations = state.get("extracted_equations", [])
    if tables:
        extra_context += f"\n\nNote: {len(tables)} tables detected in the document."
    if equations:
        display_eq = sum(1 for e in equations if e.get("type") == "display")
        extra_context += f"\n{display_eq} display equations detected."

    prompt = (
        f"Document: {state['original_name']}\n\n"
        f"Detected potential headers:\n{header_hints}\n\n"
        f"Document preview (first ~3000 chars):\n{text_preview}\n\n"
        f"Total pages: {len(page_texts)}{extra_context}\n\n"
        "Analyze this document and return a structured outline as JSON."
    )

    async with async_session() as db:
        provider = await get_provider(db)

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_system_prompt.format(),
            max_tokens=4096,
            tier="simple",
        )
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        outline = json.loads(json_match.group()) if json_match else json.loads(response)
    except (json.JSONDecodeError, Exception):
        outline = []
        for hint in header_hints.split("\n"):
            if hint:
                match = re.match(r"Line (\d+): (.+)", hint)
                if match:
                    outline.append({"title": match.group(2).strip(), "level": 1, "page_start": 1, "snippet": ""})

    await set_step(doc_id, f"Outline: {len(outline)} sections detected")
    return {**state, "outline": outline}
