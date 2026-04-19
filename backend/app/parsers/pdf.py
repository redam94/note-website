"""PDF parser.

Uses raw PyMuPDF (`fitz.open` + `page.get_text`) instead of
`pymupdf4llm.to_markdown`. The markdown-conversion path in pymupdf4llm
walks every page to detect tables, code fences, and headings, which is
minutes of C-side compute on large documents (and was hanging for us on
55MB+ PDFs). Raw text extraction is orders of magnitude faster and the
downstream LLM pipeline re-infers structure from section boundaries and
the outline anyway.

Signature preserved so callers don't change:
    parse_pdf(file_path) -> (full_text: str, page_texts: list[dict])
where each dict is {"page": int, "text": str} with 1-based page numbers.
"""

from __future__ import annotations

import pymupdf


def parse_pdf(file_path: str) -> tuple[str, list[dict]]:
    page_texts: list[dict] = []
    full_parts: list[str] = []

    with pymupdf.open(file_path) as doc:
        for page_index, page in enumerate(doc):
            # get_text("text") is the fastest extraction mode — plain reading-
            # order text, no layout detection, no markup.
            text = page.get_text("text") or ""
            text = text.strip()
            if not text:
                continue
            page_num = page_index + 1
            page_texts.append({"page": page_num, "text": text})
            full_parts.append(text)

    return "\n\n".join(full_parts), page_texts
