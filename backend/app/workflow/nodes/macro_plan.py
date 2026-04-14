"""Macro-plan node: document-level decisions before per-chapter planning.

This node runs a fast single LLM call to decide:
1. Which sections are high-value (worthy of note extraction)
2. Which sections to skip (bibliography, indices, appendices, etc.)
3. How to group subsections within each high-value chapter
4. The top-level folder structure

A Python regex pre-filter runs first to flag obviously low-value sections
so the LLM confirmation isn't needed for the clearest cases.
"""

from __future__ import annotations

import json
import logging
import re

from ...database import async_session
from ...prompts import load_prompt
from ...services.model_provider import get_provider, get_setting
from ..progress import set_step
from ..state import ProcessingState

logger = logging.getLogger(__name__)

# ── Python pre-filter ─────────────────────────────────────────────────

_LOW_VALUE_RE = re.compile(
    r"^("
    r"bibliography|references?|works?\s+cited|further\s+reading|"
    r"index|indices|"
    r"acknowledgements?|acknowledgments?|"
    r"table\s+of\s+contents?|contents?|"
    r"appendix\s*[a-z0-9]*|"
    r"list\s+of\s+(figures?|tables?|abbreviations?|symbols?|algorithms?)|"
    r"glossary|"
    r"foreword|preface|"
    r"about\s+the\s+authors?|about\s+this\s+book|about\s+this\s+document|"
    r"colophon|errata|copyright|permissions?|"
    r"notation|conventions?"
    r")\s*$",
    re.IGNORECASE,
)


def _python_pre_filter(outline: list[dict]) -> set[str]:
    """Return set of level-1 section titles matching low-value patterns."""
    return {
        s["title"]
        for s in outline
        if s.get("level", 1) == 1 and _LOW_VALUE_RE.match(s["title"].strip())
    }


# ── Validation ────────────────────────────────────────────────────────


def _validate_macro_plan(data: dict, outline: list[dict]) -> dict:
    """Drop any chapter/skip entries whose title doesn't exist in outline."""
    valid_titles = {s["title"] for s in outline}

    data["high_value_chapters"] = [
        c for c in data.get("high_value_chapters", [])
        if c.get("title") in valid_titles
    ]
    data["skip_sections"] = [
        s for s in data.get("skip_sections", [])
        if s.get("title") in valid_titles
    ]
    return data


# ── Fallback builder ──────────────────────────────────────────────────


def _build_fallback_macro(
    outline: list[dict],
    skipped: set[str],
    doc_name: str,
) -> dict:
    """Build a conservative macro plan when LLM call fails.

    Every non-skipped level-1 section becomes a high-value chapter with
    subsection_strategy='one_per_subsection'.
    """
    high_value = []
    for section in outline:
        if section.get("level", 1) != 1:
            continue
        title = section.get("title", "").strip()
        if not title or title in skipped:
            continue
        high_value.append({
            "title": title,
            "subsection_strategy": "one_per_subsection",
            "grouping_hint": "",
            "estimated_notes": 3,
            "folder": f"{doc_name}/{title}",
        })

    return {
        "doc_summary": f"Notes extracted from {doc_name}.",
        "folder_root": doc_name,
        "high_value_chapters": high_value,
        "skip_sections": [{"title": t, "reason": "pre-filtered"} for t in sorted(skipped)],
    }


# ── Main node ─────────────────────────────────────────────────────────


async def create_macro_plan(state: ProcessingState) -> ProcessingState:
    """Document-level macro plan: skip low-value sections, decide grouping."""
    await set_step(state["document_id"], "Building document macro plan...")

    outline = state["outline"]
    original_name = state["original_name"]
    doc_type = state.get("doc_type", "article")
    doc_metadata = state.get("doc_metadata", {})
    total_pages = doc_metadata.get("total_pages", len(state.get("page_texts", [])))

    # ── Step 1: Python pre-filter (deterministic, no LLM) ─────────────
    pre_flagged = _python_pre_filter(outline)
    if pre_flagged:
        logger.info("Macro plan pre-filter flagged %d sections: %s", len(pre_flagged), sorted(pre_flagged))

    # ── Step 2: LLM macro call ─────────────────────────────────────────
    prompt_template = load_prompt("macro_plan")
    system_prompt = prompt_template.format(
        doc_name=original_name,
        doc_type=doc_type,
        total_pages=total_pages,
        outline_json=json.dumps(outline, indent=2),
        pre_flagged_sections=", ".join(sorted(pre_flagged)) if pre_flagged else "(none)",
    )

    macro_data: dict | None = None
    async with async_session() as db:
        provider = await get_provider(db)
        model = await get_setting(db, "model_macro_plan")

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": "Analyse the document and return the macro plan JSON."}],
            system=system_prompt,
            max_tokens=4096,
            model=model,
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            raw = json.loads(json_match.group())
        else:
            raw = json.loads(response)
        macro_data = _validate_macro_plan(raw, outline)

        # If LLM returned no high-value chapters, fall back
        if not macro_data.get("high_value_chapters"):
            logger.warning("Macro plan LLM returned no high_value_chapters — using fallback")
            macro_data = None

    except Exception as exc:
        logger.warning("Macro plan LLM failed (%s) — using Python fallback", exc)

    if macro_data is None:
        macro_data = _build_fallback_macro(outline, pre_flagged, original_name)

    # ── Step 3: Merge — Python pre-flagged sections always skipped ─────
    llm_skip_titles = {s["title"] for s in macro_data.get("skip_sections", [])}
    all_skipped_titles = llm_skip_titles | pre_flagged

    # Ensure pre_flagged sections aren't mistakenly in high_value_chapters
    macro_data["high_value_chapters"] = [
        c for c in macro_data["high_value_chapters"]
        if c["title"] not in all_skipped_titles
    ]

    # Rebuild skip_sections to reflect merged set
    existing_skip_map = {s["title"]: s["reason"] for s in macro_data.get("skip_sections", [])}
    merged_skip = []
    for title in sorted(all_skipped_titles):
        merged_skip.append({
            "title": title,
            "reason": existing_skip_map.get(title, "pre-filtered"),
        })
    macro_data["skip_sections"] = merged_skip

    skipped_section_titles = list(all_skipped_titles)

    n_high = len(macro_data["high_value_chapters"])
    n_skip = len(skipped_section_titles)
    await set_step(
        state["document_id"],
        f"Macro plan: {n_high} chapters to extract, {n_skip} sections skipped",
    )
    logger.info(
        "Macro plan complete: %d high-value chapters, %d skipped, folder_root=%s",
        n_high, n_skip, macro_data.get("folder_root", ""),
    )

    return {
        **state,
        "macro_plan": macro_data,
        "skipped_sections": skipped_section_titles,
    }
