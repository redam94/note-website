from __future__ import annotations

from typing import TypedDict


class ProcessingState(TypedDict):
    document_id: int
    space_id: int
    file_path: str
    mime_type: str
    original_name: str
    # Populated by parse node
    raw_text: str
    page_texts: list[dict]  # [{"page": int, "text": str}]
    # Populated by extract_structure node (Python, no LLM)
    extracted_toc: list[dict]          # [{"title", "level", "page"}]
    extracted_tables: list[dict]       # [{"page", "rows", "caption", "row_count", "col_count"}]
    extracted_equations: list[dict]    # [{"content", "type", "context", "char_offset"}]
    extracted_definitions: list[dict]  # [{"term", "definition", "type"}]
    section_boundaries: list[dict]    # [{"title", "start_char", "end_char", "page"}]
    doc_metadata: dict                # {"title", "author", "total_pages"}
    # Populated by classify_document node (pure Python, no LLM)
    doc_type: str  # textbook | paper | tutorial | reference | article
    # Populated by outline node
    outline: list[dict]  # [{"title", "level", "page_start", "snippet"}]
    # Populated by macro_plan node
    macro_plan: dict        # {doc_summary, folder_root, high_value_chapters, skip_sections}
    skipped_sections: list[str]  # titles confirmed as low-value
    # Populated by chapter_plan node (renamed from plan)
    note_plan: list[dict]
    existing_tags: list[str]
    existing_note_titles: list[str]
    # Populated by ensure_tree node
    folder_id_map: dict[str, int]
    # Populated by create_notes node
    created_notes: list[dict]
    # Populated by index_gen node
    index_notes: list[dict]
    # Populated by insert_links node
    linked_notes: list[dict]
    # Populated by cross_link node
    cross_links: list[dict]
    # Populated by community_update node
    community_updates: list[dict]
    # Extraction profile (optional) — set by the upload router before pipeline starts
    extraction_profile_id: int | None
    # Overrides the classifier doc_type when set by an extraction profile
    doc_type_override: str | None
    # Extra instructions injected into the LLM system prompt during note creation
    prompt_additions: str
    # Error tracking
    error: str | None
