import asyncio
import os
import uuid
from datetime import datetime, timezone

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin

from ..config import settings
from ..database import get_db
from ..dependencies import get_current_space
from ..models.document import Document
from ..models.note import Note
from ..models.space import Space
from ..schemas.document import DocumentResponse
from ..worker import run_processing_pipeline

router = APIRouter(prefix="/api")

MIME_MAP = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def get_mime_type(filename: str) -> str:
    ext = os.path.splitext(filename)[1].lower()
    return MIME_MAP.get(ext, "text/plain")


@router.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)) -> list[DocumentResponse]:
    result = await db.execute(select(Document).where(Document.space_id == current_space.id))
    rows = result.scalars().all()

    responses = []
    for r in rows:
        recent_notes: list[str] = []
        if r.status in ("processing", "done"):
            notes_result = await db.execute(
                select(Note.title)
                .where(Note.document_id == r.id)
                .order_by(Note.id.desc())
                .limit(8)
            )
            recent_notes = [t for (t,) in notes_result.all()]
            recent_notes.reverse()  # oldest first
        responses.append(DocumentResponse.from_row(r, recent_notes=recent_notes))
    return responses


@router.post("/documents", status_code=201, dependencies=[Depends(require_admin)])
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> DocumentResponse:
    os.makedirs(settings.uploads_dir, exist_ok=True)

    ext = os.path.splitext(file.filename or "")[1]
    filename = f"{uuid.uuid4()}{ext}"
    file_path = os.path.join(settings.uploads_dir, filename)

    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    mime_type = get_mime_type(file.filename or "")
    now = datetime.now(timezone.utc).isoformat()

    doc = Document(
        filename=filename,
        original_name=file.filename or "unknown",
        mime_type=mime_type,
        status="pending",
        created_at=now,
        space_id=current_space.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Process document asynchronously
    if settings.use_redis:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job(
            "process_document_job", doc.id, file_path, mime_type, file.filename or "unknown", current_space.id
        )
    else:
        background_tasks.add_task(
            run_processing_pipeline, doc.id, file_path, mime_type, file.filename or "unknown", current_space.id
        )

    return DocumentResponse.from_row(doc)


@router.post("/documents/upload-chunk", status_code=204, dependencies=[Depends(require_admin)])
async def upload_chunk(
    session_id: str = Form(...),
    chunk_index: int = Form(...),
    total_chunks: int = Form(...),
    filename: str = Form(...),
    chunk: UploadFile = File(...),
) -> None:
    """Receive one chunk of a large file upload. Chunks are appended to a temp file."""
    tmp_dir = os.path.join(settings.uploads_dir, ".chunks")
    os.makedirs(tmp_dir, exist_ok=True)
    tmp_path = os.path.join(tmp_dir, session_id)

    data = await chunk.read()
    async with aiofiles.open(tmp_path, "ab") as f:
        await f.write(data)


@router.post("/documents/upload-complete", status_code=201, dependencies=[Depends(require_admin)])
async def upload_complete(
    background_tasks: BackgroundTasks,
    session_id: str = Form(...),
    filename: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> DocumentResponse:
    """Finalize a chunked upload: move the assembled file and start processing."""
    tmp_dir = os.path.join(settings.uploads_dir, ".chunks")
    tmp_path = os.path.join(tmp_dir, session_id)

    if not os.path.exists(tmp_path):
        raise HTTPException(status_code=404, detail="Upload session not found")

    os.makedirs(settings.uploads_dir, exist_ok=True)
    ext = os.path.splitext(filename)[1]
    dest_filename = f"{uuid.uuid4()}{ext}"
    dest_path = os.path.join(settings.uploads_dir, dest_filename)
    os.rename(tmp_path, dest_path)

    mime_type = get_mime_type(filename)
    now = datetime.now(timezone.utc).isoformat()

    doc = Document(
        filename=dest_filename,
        original_name=filename,
        mime_type=mime_type,
        status="pending",
        created_at=now,
        space_id=current_space.id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    if settings.use_redis:
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job(
            "process_document_job", doc.id, dest_path, mime_type, filename, current_space.id
        )
    else:
        background_tasks.add_task(
            run_processing_pipeline, doc.id, dest_path, mime_type, filename, current_space.id
        )

    return DocumentResponse.from_row(doc)
