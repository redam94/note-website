"""Python-side document structure extraction — no LLM needed.

Extracts headings, tables, equations, definitions, and section boundaries
from PDFs (pymupdf), DOCX (python-docx), and Markdown files.
"""

from __future__ import annotations

import re
from pathlib import Path


def extract_structure(file_path: str, mime_type: str, raw_text: str) -> dict:
    """Extract structured data from a document.

    Returns a dict with: toc, headings, tables, equations, definitions,
    section_boundaries, metadata.
    """
    result = {
        "toc": [],
        "headings": [],
        "tables": [],
        "equations": [],
        "definitions": [],
        "section_boundaries": [],
        "metadata": {"title": "", "author": "", "total_pages": 0},
    }

    if mime_type == "application/pdf":
        result = _extract_pdf(file_path, raw_text)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        result = _extract_docx(file_path, raw_text)
    else:
        result = _extract_markdown(raw_text)

    # Always extract equations and definitions from text (works for all formats)
    result["equations"] = _extract_equations(raw_text)
    result["definitions"] = _extract_definitions(raw_text)

    # Compute section boundaries from headings
    if result["headings"] and not result["section_boundaries"]:
        result["section_boundaries"] = _compute_boundaries(raw_text, result["headings"])

    return result


# ── PDF extraction ────────────────────────────────────────────────────


def _extract_pdf(file_path: str, raw_text: str) -> dict:
    """Extract PDF structure metadata.

    raw_text is already rich Markdown produced by PyMuPDF4LLM, so:
    - Headings are present as # / ## / ### syntax → use _extract_markdown
    - Tables are already Markdown tables in raw_text → no find_tables() needed
    - We only open the PDF (with fitz) for the two things 4LLM doesn't give us:
        1. The built-in PDF outline (TOC)
        2. Document metadata (title, author, page count)
    """
    import fitz

    doc = fitz.open(file_path)
    try:
        result = {
            "toc": [],
            "headings": [],
            "tables": [],
            "equations": [],
            "definitions": [],
            "section_boundaries": [],
            "metadata": {
                "title": doc.metadata.get("title", "") or "",
                "author": doc.metadata.get("author", "") or "",
                "total_pages": len(doc),
            },
        }

        # 1. Built-in TOC — nearly free (reads only the PDF's outline tree)
        toc = doc.get_toc()
        for level, title, page_num in toc:
            result["toc"].append({"title": title.strip(), "level": level, "page": page_num})

    finally:
        doc.close()

    # 2. Headings from Markdown headings in raw_text
    #    PyMuPDF4LLM already detected headings and emitted them as # / ## / ###,
    #    so _extract_markdown gives us accurate heading data at zero extra cost.
    md_result = _extract_markdown(raw_text)
    result["headings"] = md_result["headings"]

    # 3. If the built-in TOC is richer (has page numbers), prefer it and enrich
    #    the markdown headings with accurate page numbers from the TOC.
    if len(result["toc"]) >= 3:
        if not result["headings"]:
            result["headings"] = [
                {"title": t["title"], "level": t["level"], "page": t["page"], "font_size": 0}
                for t in result["toc"]
            ]
        else:
            # Annotate markdown headings with TOC page numbers where we get a match
            toc_by_title = {t["title"].lower(): t for t in result["toc"]}
            for h in result["headings"]:
                match = toc_by_title.get(h["title"].lower())
                if match:
                    h["page"] = match["page"]

    # 4. Tables: PyMuPDF4LLM already put them in raw_text as Markdown table syntax.
    #    The pipeline uses the raw_text tables for note content; we don't need to
    #    re-extract them here.  result["tables"] stays [] — that's intentional.

    return result


# ── DOCX extraction ───────────────────────────────────────────────────


def _extract_docx(file_path: str, raw_text: str) -> dict:
    from docx import Document as DocxDocument

    doc = DocxDocument(file_path)
    result = {
        "toc": [],
        "headings": [],
        "tables": [],
        "equations": [],
        "definitions": [],
        "section_boundaries": [],
        "metadata": {"title": "", "author": "", "total_pages": 1},
    }

    # Headings from paragraph styles
    for para in doc.paragraphs:
        style = para.style.name if para.style else ""
        text = para.text.strip()
        if not text:
            continue
        if style.startswith("Heading"):
            try:
                level = int(style.split()[-1])
            except (ValueError, IndexError):
                level = 1
            result["headings"].append({
                "title": text, "level": level, "page": 1, "font_size": 0,
            })

    # Tables
    for i, table in enumerate(doc.tables):
        rows = []
        for row in table.rows[:20]:
            cells = [cell.text.strip() for cell in row.cells]
            rows.append(cells)
        if rows:
            result["tables"].append({
                "page": 1, "rows": rows, "caption": "",
                "row_count": len(table.rows), "col_count": len(rows[0]) if rows else 0,
            })

    return result


# ── Markdown extraction ───────────────────────────────────────────────


def _extract_markdown(raw_text: str) -> dict:
    result = {
        "toc": [],
        "headings": [],
        "tables": [],
        "equations": [],
        "definitions": [],
        "section_boundaries": [],
        "metadata": {"title": "", "author": "", "total_pages": 1},
    }

    for line_num, line in enumerate(raw_text.split("\n")):
        m = re.match(r"^(#{1,6})\s+(.+)", line)
        if m:
            level = len(m.group(1))
            title = m.group(2).strip()
            result["headings"].append({
                "title": title, "level": level, "page": 1, "font_size": 0,
            })

    # Extract title from first H1
    if result["headings"]:
        h1s = [h for h in result["headings"] if h["level"] == 1]
        if h1s:
            result["metadata"]["title"] = h1s[0]["title"]

    # Detect markdown tables
    lines = raw_text.split("\n")
    i = 0
    while i < len(lines):
        if "|" in lines[i] and i + 1 < len(lines) and re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[i + 1]):
            rows = []
            j = i
            while j < len(lines) and "|" in lines[j]:
                if not re.match(r"^\s*\|[\s\-:|]+\|\s*$", lines[j]):
                    cells = [c.strip() for c in lines[j].split("|")[1:-1]]
                    rows.append(cells)
                j += 1
            if rows:
                result["tables"].append({
                    "page": 1, "rows": rows[:20], "caption": "",
                    "row_count": len(rows), "col_count": len(rows[0]) if rows else 0,
                })
            i = j
        else:
            i += 1

    return result


# ── Cross-format: equation extraction ─────────────────────────────────


def _extract_equations(text: str) -> list[dict]:
    """Extract LaTeX math blocks from text."""
    equations = []

    # Display math: $$...$$
    for m in re.finditer(r"\$\$\s*([\s\S]*?)\s*\$\$", text):
        content = m.group(1).strip()
        if len(content) < 3:
            continue
        # Get surrounding context (30 chars before)
        start = max(0, m.start() - 80)
        context = text[start:m.start()].strip().split("\n")[-1]
        equations.append({
            "content": content,
            "type": "display",
            "context": context,
            "char_offset": m.start(),
        })

    # Inline math with function notation or operators: $...$
    for m in re.finditer(r"(?<!\$)\$(?!\$)(.+?)\$(?!\$)", text):
        content = m.group(1).strip()
        # Only keep substantial inline math (has operators or functions)
        if len(content) > 5 and re.search(r"[\\=+\-*/^_{}]", content):
            equations.append({
                "content": content,
                "type": "inline",
                "context": "",
                "char_offset": m.start(),
            })

    return equations


# ── Cross-format: definition extraction ───────────────────────────────


def _extract_definitions(text: str) -> list[dict]:
    """Extract definition-like patterns from text."""
    definitions = []

    # Pattern: "X is defined as Y" / "X is called Y" / "Definition: X"
    patterns = [
        (r"(?:^|\n)\s*(?:Definition[:\s]+)(.+?)(?:\.|$)", "explicit"),
        (r"(\b\w[\w\s]{2,30}\b)\s+(?:is defined as|is called|refers to|means)\s+(.+?)(?:\.|$)", "inline"),
        (r">\s*\[!definition\]\s*(.+?)(?:\n(?!>)|$)", "callout"),
    ]

    for pattern, ptype in patterns:
        for m in re.finditer(pattern, text, re.IGNORECASE | re.MULTILINE):
            if ptype == "inline":
                definitions.append({
                    "term": m.group(1).strip(),
                    "definition": m.group(2).strip()[:200],
                    "type": ptype,
                })
            else:
                definitions.append({
                    "term": m.group(1).strip()[:80],
                    "definition": m.group(1).strip()[:200],
                    "type": ptype,
                })

    return definitions[:30]  # limit


# ── Section boundary computation ──────────────────────────────────────


def _compute_boundaries(raw_text: str, headings: list[dict]) -> list[dict]:
    """Compute character offsets for each heading in raw_text."""
    boundaries = []
    text_lower = raw_text.lower()

    for i, heading in enumerate(headings):
        title = heading["title"]
        # Find the heading in raw_text
        idx = text_lower.find(title.lower())
        if idx == -1:
            # Try partial match (first 30 chars)
            idx = text_lower.find(title[:30].lower())
        if idx == -1:
            continue

        # End is the start of the next heading (or end of text)
        end = len(raw_text)
        if i + 1 < len(headings):
            next_title = headings[i + 1]["title"]
            next_idx = text_lower.find(next_title.lower(), idx + len(title))
            if next_idx > idx:
                end = next_idx

        boundaries.append({
            "title": title,
            "start_char": idx,
            "end_char": end,
            "page": heading.get("page", 1),
        })

    return boundaries
