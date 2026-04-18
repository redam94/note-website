"""Helpers for updating document processing progress."""

from __future__ import annotations

import json
import logging

from sqlalchemy import update

from ..database import async_session
from ..models.document import Document

logger = logging.getLogger(__name__)

# Redis key pattern for live progress (TTL 2 hours)
_PROGRESS_KEY = "doc:progress:{}"
_PROGRESS_TTL = 7200

_redis_pool = None


async def _get_redis():
    """Return a shared Redis pool, creating it on first call."""
    global _redis_pool
    if _redis_pool is None:
        from ..config import settings
        from arq.connections import RedisSettings, create_pool
        _redis_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _redis_pool


async def set_step(document_id: int, step: str, notes_count: int | None = None):
    """Update the processing_step (and optionally notes_count) on a document.

    Always writes to the local SQLite database.  When USE_REDIS=true the same
    data is also written to a Redis key so the web-service process can serve
    live progress to the frontend without waiting for the DB sync cycle.
    """
    values: dict = {"processing_step": step, "status": "processing"}
    if notes_count is not None:
        values["notes_count"] = notes_count

    async with async_session() as db:
        await db.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )
        await db.commit()

    # Publish to Redis so the web service can read it without a DB sync
    from ..config import settings
    if settings.use_redis:
        try:
            pool = await _get_redis()
            await pool.redis.set(
                _PROGRESS_KEY.format(document_id),
                json.dumps(values),
                ex=_PROGRESS_TTL,
            )
        except Exception as exc:
            logger.debug("Redis progress publish failed for doc %d: %s", document_id, exc)


async def clear_progress(document_id: int):
    """Remove the Redis progress key once a document reaches a terminal state."""
    from ..config import settings
    if not settings.use_redis:
        return
    try:
        pool = await _get_redis()
        await pool.redis.delete(_PROGRESS_KEY.format(document_id))
    except Exception as exc:
        logger.debug("Redis progress clear failed for doc %d: %s", document_id, exc)
