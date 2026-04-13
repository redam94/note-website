import asyncio
import logging

from sqlalchemy import select, update

from .database import async_session
from .models.document import Document
from .workflow.checkpoint import clear_checkpoint, load_checkpoint
from .workflow.graph import NODE_ORDER, processing_pipeline, _nodes
from .workflow.progress import set_step

logger = logging.getLogger(__name__)


async def process_document_job(
    ctx: dict,
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
):
    await run_processing_pipeline(document_id, file_path, mime_type, original_name)


async def run_processing_pipeline(
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
):
    """Run the pipeline with checkpoint resumption."""

    saved_state, last_node = await load_checkpoint(document_id)

    if saved_state and last_node:
        logger.info("Resuming doc %d from after '%s'", document_id, last_node)
        await set_step(document_id, f"Resuming from {last_node}...")
        state = saved_state
        state["file_path"] = file_path
    else:
        state = {
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
        last_node = None

    try:
        if last_node:
            # Resume from checkpoint — run remaining nodes
            start_idx = NODE_ORDER.index(last_node) + 1
            remaining = NODE_ORDER[start_idx:]

            if remaining:
                logger.info("Remaining nodes: %s", remaining)
                from langgraph.graph import END, StateGraph
                from .workflow.state import ProcessingState

                sub = StateGraph(ProcessingState)
                for name in remaining:
                    sub.add_node(name, _nodes[name])
                sub.set_entry_point(remaining[0])
                for i in range(len(remaining) - 1):
                    sub.add_edge(remaining[i], remaining[i + 1])
                sub.add_edge(remaining[-1], END)

                await sub.compile().ainvoke(state)
        else:
            await processing_pipeline.ainvoke(state)

        await clear_checkpoint(document_id)
        async with async_session() as db:
            await db.execute(
                update(Document).where(Document.id == document_id).values(status="done")
            )
            await db.commit()

    except Exception as e:
        logger.exception("Processing failed for doc %d", document_id)
        async with async_session() as db:
            await db.execute(
                update(Document).where(Document.id == document_id)
                .values(status="error", error=str(e))
            )
            await db.commit()


async def resume_interrupted_documents():
    """Resume documents that were interrupted mid-processing (called on startup)."""
    async with async_session() as db:
        result = await db.execute(
            select(Document).where(Document.status == "processing")
        )
        stuck_docs = result.scalars().all()

    resumed = 0
    errored = 0
    for doc in stuck_docs:
        if doc.checkpoint and doc.last_completed_node:
            logger.info("Resuming doc %d from '%s'", doc.id, doc.last_completed_node)
            asyncio.create_task(
                run_processing_pipeline(doc.id, doc.filename, doc.mime_type, doc.original_name)
            )
            resumed += 1
        else:
            async with async_session() as db:
                await db.execute(
                    update(Document).where(Document.id == doc.id)
                    .values(status="error", error="Processing interrupted. Please re-upload.")
                )
                await db.commit()
            errored += 1

    if resumed or errored:
        logger.info("Startup recovery: %d resumed, %d marked error", resumed, errored)


class WorkerSettings:
    functions = [process_document_job]
    try:
        from arq.connections import RedisSettings
        redis_settings = RedisSettings()
    except ImportError:
        pass
