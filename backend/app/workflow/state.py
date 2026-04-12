from __future__ import annotations

from typing import TypedDict


class ProcessingState(TypedDict):
    document_id: int
    file_path: str
    mime_type: str
    original_name: str
    # Populated by parse node
    raw_text: str
    page_texts: list[dict]  # [{"page": int, "text": str}]
    # Populated by outline node
    outline: list[dict]  # [{"title": str, "level": int, "page_start": int, "snippet": str}]
    # Populated by plan node
    note_plan: list[dict]  # [{"title": str, "parent_title": str|None, "tags": list, ...}]
    existing_tags: list[str]
    existing_note_titles: list[str]  # for link insertion
    # Populated by create_notes node
    created_notes: list[dict]
    # Populated by index_gen node
    index_notes: list[dict]
    # Populated by insert_links node
    linked_notes: list[dict]
    # Populated by cross_link node
    cross_links: list[dict]
    # Error tracking
    error: str | None
