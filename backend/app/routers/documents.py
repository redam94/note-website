import asyncio
import os
import uuid
from datetime import datetime, timezone

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin

from ..config import settings
from ..database import get_db
from ..models.document import Document
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
async def list_documents(db: AsyncSession = Depends(get_db)) -> list[DocumentResponse]:
    result = await db.execute(select(Document))
    rows = result.scalars().all()
    return [DocumentResponse.from_row(r) for r in rows]


@router.post("/documents", status_code=201, dependencies=[Depends(require_admin)])
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
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
            "process_document_job", doc.id, file_path, mime_type, file.filename or "unknown"
        )
    else:
        background_tasks.add_task(
            run_processing_pipeline, doc.id, file_path, mime_type, file.filename or "unknown"
        )

    return DocumentResponse.from_row(doc)
