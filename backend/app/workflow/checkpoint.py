"""Pipeline checkpoint system — saves state after each node for crash recovery.

State is serialized to JSON and stored in the documents.checkpoint column.
On resume, the pipeline skips already-completed nodes.
"""

from __future__ import annotations

import json
import logging
from functools import wraps

from sqlalchemy import select, update

from ..database import async_session
from ..models.document import Document
from .state import ProcessingState

logger = logging.getLogger(__name__)

# Fields that are too large to serialize (raw text stored in content_raw already)
_SKIP_FIELDS = {"raw_text"}


def _serialize_state(state: ProcessingState) -> str:
    """Serialize state to JSON, skipping large text fields."""
    data = {}
    for key, value in state.items():
        if key in _SKIP_FIELDS:
            continue
        try:
            json.dumps(value)  # test serializability
            data[key] = value
        except (TypeError, ValueError):
            data[key] = str(value)
    return json.dumps(data)


def _deserialize_state(checkpoint_json: str, document: Document) -> ProcessingState:
    """Deserialize checkpoint JSON back to ProcessingState."""
    data = json.loads(checkpoint_json)
    # Restore raw_text from the document's content_raw
    data["raw_text"] = document.content_raw or ""
    return data


async def save_checkpoint(state: ProcessingState, node_name: str):
    """Save the current pipeline state as a checkpoint."""
    doc_id = state.get("document_id")
    if not doc_id:
        return
    try:
        checkpoint_data = _serialize_state(state)
        async with async_session() as db:
            await db.execute(
                update(Document)
                .where(Document.id == doc_id)
                .values(checkpoint=checkpoint_data, last_completed_node=node_name)
            )
            await db.commit()
    except Exception as e:
        logger.warning("Failed to save checkpoint for doc %d at %s: %s", doc_id, node_name, e)


async def load_checkpoint(document_id: int) -> tuple[ProcessingState | None, str | None]:
    """Load the last checkpoint for a document.

    Returns (state, last_completed_node) or (None, None) if no checkpoint.
    """
    async with async_session() as db:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()

    if not doc or not doc.checkpoint or not doc.last_completed_node:
        return None, None

    try:
        state = _deserialize_state(doc.checkpoint, doc)
        return state, doc.last_completed_node
    except Exception as e:
        logger.warning("Failed to load checkpoint for doc %d: %s", document_id, e)
        return None, None


async def clear_checkpoint(document_id: int):
    """Clear the checkpoint after successful completion."""
    async with async_session() as db:
        await db.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(checkpoint=None, last_completed_node=None)
        )
        await db.commit()


def with_checkpoint(node_name: str):
    """Decorator that saves a checkpoint after a node completes successfully."""
    def decorator(fn):
        @wraps(fn)
        async def wrapper(state: ProcessingState) -> ProcessingState:
            result = await fn(state)
            await save_checkpoint(result, node_name)
            return result
        return wrapper
    return decorator
