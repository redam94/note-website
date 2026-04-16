import asyncio
import logging

from sqlalchemy import select, update

from .database import async_session
from .models.document import Document
from .workflow.checkpoint import clear_checkpoint, load_checkpoint
from .workflow.graph import NODE_ALIASES, NODE_ORDER, _nodes, per_doc_pipeline, processing_pipeline
from .workflow.nodes.community_update import update_communities
from .workflow.nodes.cross_link import detect_cross_links
from .workflow.progress import set_step

logger = logging.getLogger(__name__)


async def process_document_job(
    ctx: dict,
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
    space_id: int = 1,
    extraction_profile_id: int | None = None,
):
    await run_processing_pipeline(
        document_id, file_path, mime_type, original_name,
        space_id=space_id, extraction_profile_id=extraction_profile_id,
    )


async def run_processing_pipeline(
    document_id: int,
    file_path: str,
    mime_type: str,
    original_name: str,
    space_id: int = 1,
    extraction_profile_id: int | None = None,
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
            "space_id": space_id,
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
            "macro_plan": {},
            "skipped_sections": [],
            "note_plan": [],
            "existing_tags": [],
            "existing_note_titles": [],
            "folder_id_map": {},
            "created_notes": [],
            "index_notes": [],
            "linked_notes": [],
            "cross_links": [],
            "community_updates": [],
            "extraction_profile_id": extraction_profile_id,
            "doc_type_override": None,
            "prompt_additions": "",
            "error": None,
        }
        last_node = None

    try:
        if last_node:
            # Resolve aliases (e.g. old "plan" checkpoint → "chapter_plan")
            resolved_node = NODE_ALIASES.get(last_node, last_node)
            if resolved_node not in NODE_ORDER:
                logger.warning(
                    "Unknown last_node '%s' (resolved: '%s') — restarting from scratch",
                    last_node, resolved_node,
                )
                last_node = None

        if last_node:
            resolved_node = NODE_ALIASES.get(last_node, last_node)
            # Resume from checkpoint — run remaining nodes
            start_idx = NODE_ORDER.index(resolved_node) + 1
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


async def run_batch_pipeline(
    batch_docs: list[dict],
    space_id: int,
):
    """Run extraction for multiple documents in parallel, then cross-link once across all."""

    async def _run_one(doc: dict):
        document_id = doc["document_id"]
        state = {
            "document_id": document_id,
            "space_id": space_id,
            "file_path": doc["file_path"],
            "mime_type": doc["mime_type"],
            "original_name": doc["original_name"],
            "raw_text": "",
            "page_texts": [],
            "extracted_toc": [],
            "extracted_tables": [],
            "extracted_equations": [],
            "extracted_definitions": [],
            "section_boundaries": [],
            "doc_metadata": {},
            "outline": [],
            "macro_plan": {},
            "skipped_sections": [],
            "note_plan": [],
            "existing_tags": [],
            "existing_note_titles": [],
            "folder_id_map": {},
            "created_notes": [],
            "index_notes": [],
            "linked_notes": [],
            "cross_links": [],
            "community_updates": [],
            "extraction_profile_id": doc.get("extraction_profile_id"),
            "doc_type_override": None,
            "prompt_additions": "",
            "error": None,
        }
        try:
            result = await per_doc_pipeline.ainvoke(state)
            async with async_session() as db:
                await db.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(processing_step="Waiting for cross-linking...")
                )
                await db.commit()
            return result
        except Exception as e:
            logger.exception("Per-doc pipeline failed for doc %d", document_id)
            async with async_session() as db:
                await db.execute(
                    update(Document)
                    .where(Document.id == document_id)
                    .values(status="error", error=str(e))
                )
                await db.commit()
            return e

    results = await asyncio.gather(*[_run_one(doc) for doc in batch_docs])

    successful: list[dict] = []
    all_created_notes: list[dict] = []
    for doc, result in zip(batch_docs, results):
        if not isinstance(result, BaseException):
            created = result.get("created_notes") or []
            all_created_notes.extend(created)
            successful.append(doc)

    if not successful or not all_created_notes:
        # Nothing to cross-link; mark successful docs done
        async with async_session() as db:
            for doc in successful:
                await db.execute(
                    update(Document)
                    .where(Document.id == doc["document_id"])
                    .values(status="done")
                )
            await db.commit()
        return

    # Notify all successful docs that cross-linking is underway
    for doc in successful:
        await set_step(doc["document_id"], "Detecting cross-links...")

    primary_id = successful[0]["document_id"]
    combined_state = {
        "document_id": primary_id,
        "space_id": space_id,
        "created_notes": all_created_notes,
        "cross_links": [],
        "community_updates": [],
        # Dummy fields required by ProcessingState typing (not used by these nodes)
        "file_path": "", "mime_type": "", "original_name": "", "raw_text": "",
        "page_texts": [], "extracted_toc": [], "extracted_tables": [],
        "extracted_equations": [], "extracted_definitions": [], "section_boundaries": [],
        "doc_metadata": {}, "outline": [], "macro_plan": {}, "skipped_sections": [],
        "note_plan": [], "existing_tags": [], "existing_note_titles": [],
        "folder_id_map": {}, "index_notes": [], "linked_notes": [],
        "extraction_profile_id": None, "doc_type_override": None,
        "prompt_additions": "", "error": None,
    }

    try:
        cross_result = await detect_cross_links(combined_state)

        for doc in successful:
            await set_step(doc["document_id"], "Updating topic clusters...")

        await update_communities({**cross_result, "document_id": primary_id})

        async with async_session() as db:
            for doc in successful:
                await db.execute(
                    update(Document)
                    .where(Document.id == doc["document_id"])
                    .values(status="done")
                )
            await db.commit()

    except Exception as e:
        logger.exception("Batch finalize (cross-link/community) failed")
        async with async_session() as db:
            for doc in successful:
                await db.execute(
                    update(Document)
                    .where(Document.id == doc["document_id"])
                    .values(status="done", error=f"Warning: cross-link step failed: {e}")
                )
            await db.commit()


async def process_batch_job(
    ctx: dict,
    batch_docs: list[dict],
    space_id: int,
):
    await run_batch_pipeline(batch_docs, space_id)


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
                run_processing_pipeline(doc.id, doc.filename, doc.mime_type, doc.original_name, space_id=doc.space_id)
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
    functions = [process_document_job, process_batch_job]
    try:
        from arq.connections import RedisSettings
        redis_settings = RedisSettings()
    except ImportError:
        pass
