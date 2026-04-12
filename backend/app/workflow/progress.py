"""Helpers for updating document processing progress."""

from __future__ import annotations

from sqlalchemy import update

from ..database import async_session
from ..models.document import Document


async def set_step(document_id: int, step: str, notes_count: int | None = None):
    """Update the processing_step (and optionally notes_count) on a document."""
    values: dict = {"processing_step": step, "status": "processing"}
    if notes_count is not None:
        values["notes_count"] = notes_count
    async with async_session() as db:
        await db.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )
        await db.commit()
