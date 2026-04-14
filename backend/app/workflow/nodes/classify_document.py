"""Pure-Python document classification node.

Runs after extract_structure and before outline.  Uses signals already
in state — page count, equation density, TOC depth, abstract/code
detection — to assign a doc_type without any LLM call.

Doc types:
  textbook  — 50+ pages, multi-level TOC or high heading count, high math density
  paper     — 5-40 pages, has abstract, structured argument sections
  tutorial  — code fences present and step-by-step signal
  reference — API docs, spec tables, short pages with dense link/table content
  article   — everything else (blog posts, essays, short reads)
"""

from __future__ import annotations

import re

from ..progress import set_step
from ..state import ProcessingState

# ── Signal regexes ────────────────────────────────────────────────────

_ABSTRACT_RE = re.compile(
    r"^\s*(abstract|summary)\s*[\n:]",
    re.IGNORECASE | re.MULTILINE,
)
_SECTION_HEADERS_RE = re.compile(
    r"^\s*(introduction|related work|background|methodology|methods|results|"
    r"discussion|conclusion|acknowledgements?|references?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CODE_FENCE_RE = re.compile(r"^```", re.MULTILINE)
_STEP_RE = re.compile(
    r"^\s*(step\s+\d+|#{1,3}\s+step|\d+\.\s+(install|run|configure|set up|create|add|open))",
    re.IGNORECASE | re.MULTILINE,
)
_API_SIGNAL_RE = re.compile(
    r"(GET|POST|PUT|DELETE|PATCH)\s+/[^\s]+|`[A-Z_]{3,}\(`|Parameters\s*\n[-|]",
    re.MULTILINE,
)


def _classify(
    total_pages: int,
    raw_text: str,
    extracted_toc: list[dict],
    extracted_equations: list[dict],
    extracted_headings: list[dict],
) -> tuple[str, str]:
    """Return (doc_type, reason) using pure Python heuristics.

    Priority order: tutorial > reference > paper > textbook > article
    (more specific types are checked first so they can't be masked by
    a high page count alone).
    """
    # Scan only the first ~5000 chars for abstract / code signals (cheap)
    preview = raw_text[:5000]

    has_code = bool(_CODE_FENCE_RE.search(raw_text))
    has_step = bool(_STEP_RE.search(preview))
    has_abstract = bool(_ABSTRACT_RE.search(preview))
    has_paper_sections = len(_SECTION_HEADERS_RE.findall(raw_text[:8000])) >= 3
    has_api_signals = bool(_API_SIGNAL_RE.search(preview))

    equation_count = len(extracted_equations)
    toc_depth = max((e.get("level", 1) for e in extracted_toc), default=0)
    heading_count = len(extracted_headings)

    # Tutorial — code + steps trump page count entirely
    if has_code and has_step:
        return "tutorial", f"code fences + step-by-step headings detected"

    # Reference — API / spec signals
    if has_api_signals and total_pages < 100:
        return "reference", "API/spec signals (HTTP verbs, parameter tables)"

    # Paper — abstract + paper structure sections, not huge
    if has_abstract and has_paper_sections and total_pages <= 60:
        return "paper", f"{total_pages} pages, abstract + structured argument sections"

    # Textbook — large, structured, possibly math-heavy
    if total_pages >= 50 or (heading_count >= 20 and equation_count >= 10):
        reason_parts = []
        if total_pages >= 50:
            reason_parts.append(f"{total_pages} pages")
        if toc_depth >= 2:
            reason_parts.append(f"TOC depth {toc_depth}")
        if equation_count >= 10:
            reason_parts.append(f"{equation_count} equations")
        if heading_count >= 20:
            reason_parts.append(f"{heading_count} headings")
        return "textbook", ", ".join(reason_parts)

    # Default
    return "article", f"{total_pages} pages, no strong structural signals"


async def classify_document(state: ProcessingState) -> ProcessingState:
    """Classify document type using Python heuristics — no LLM."""
    doc_id = state["document_id"]

    doc_metadata = state.get("doc_metadata", {})
    total_pages = doc_metadata.get("total_pages", len(state.get("page_texts", [])))

    doc_type, reason = _classify(
        total_pages=total_pages,
        raw_text=state.get("raw_text", ""),
        extracted_toc=state.get("extracted_toc", []),
        extracted_equations=state.get("extracted_equations", []),
        extracted_headings=state.get("extracted_toc", []),  # headings proxy
    )

    await set_step(doc_id, f"Document classified as: {doc_type} ({reason})")

    return {**state, "doc_type": doc_type}
