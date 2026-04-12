import asyncio
import logging

from sqlalchemy import update

from .database import async_session
from .models.document import Document
from .workflow.graph import processing_pipeline

logger = logging.getLogger(__name__)


async def process_document_job(
    ctx: dict,
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
):
    """Run the LangGraph workflow for a document. Used by arq."""
    await run_processing_pipeline(document_id, file_path, mime_type, original_name)


async def run_processing_pipeline(
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
):
    """Run the LangGraph processing pipeline. Can be called directly or via arq."""
    initial_state = {
        "document_id": document_id,
        "file_path": file_path,
        "mime_type": mime_type,
        "original_name": original_name,
        "raw_text": "",
        "page_texts": [],
        "extracted_toc": [],
        "extracted_tables": [],
        "extracted_equations": [],
        "extracted_definitions": [],
        "section_boundaries": [],
        "doc_metadata": {},
        "outline": [],
        "note_plan": [],
        "existing_tags": [],
        "existing_note_titles": [],
        "created_notes": [],
        "index_notes": [],
        "linked_notes": [],
        "cross_links": [],
        "error": None,
    }

    try:
        await processing_pipeline.ainvoke(initial_state)
        async with async_session() as db:
            await db.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status="done")
            )
            await db.commit()
    except Exception as e:
        logger.exception("Document processing failed for doc %d", document_id)
        async with async_session() as db:
            await db.execute(
                update(Document)
                .where(Document.id == document_id)
                .values(status="error", error=str(e))
            )
            await db.commit()


class WorkerSettings:
    """arq worker settings."""

    functions = [process_document_job]

    try:
        from arq.connections import RedisSettings

        redis_settings = RedisSettings()
    except ImportError:
        pass
