"""Pre-LLM structure extraction node.

Runs Python-side parsing to extract headings, tables, equations,
definitions, and section boundaries before the LLM pipeline.
"""

from __future__ import annotations

import asyncio

from ...parsers.structure import extract_structure
from ..progress import set_step
from ..state import ProcessingState


async def extract_document_structure(state: ProcessingState) -> ProcessingState:
    """Extract structure from the parsed document using Python only (no LLM)."""
    doc_id = state["document_id"]
    await set_step(doc_id, "Extracting document structure...")

    structure = await asyncio.to_thread(
        extract_structure,
        state["file_path"],
        state["mime_type"],
        state["raw_text"],
    )

    toc = structure.get("toc", [])
    headings = structure.get("headings", [])
    tables = structure.get("tables", [])
    equations = structure.get("equations", [])
    definitions = structure.get("definitions", [])
    boundaries = structure.get("section_boundaries", [])
    metadata = structure.get("metadata", {})

    summary_parts = []
    if headings:
        summary_parts.append(f"{len(headings)} headings")
    if toc:
        summary_parts.append(f"{len(toc)} TOC entries")
    if tables:
        summary_parts.append(f"{len(tables)} tables")
    if equations:
        display = sum(1 for e in equations if e.get("type") == "display")
        summary_parts.append(f"{display} equations")
    if definitions:
        summary_parts.append(f"{len(definitions)} definitions")

    await set_step(
        doc_id,
        f"Structure extracted: {', '.join(summary_parts) or 'minimal structure detected'}",
    )

    return {
        **state,
        "extracted_toc": toc,
        "extracted_tables": tables,
        "extracted_equations": equations,
        "extracted_definitions": definitions,
        "section_boundaries": boundaries,
        "doc_metadata": metadata,
    }
