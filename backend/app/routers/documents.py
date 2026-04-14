import logging
import os
import uuid
from datetime import datetime, timezone

import aiofiles
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..config import settings
from ..database import get_db
from ..dependencies import get_current_space
from ..models.document import Document
from ..models.note import Note
from ..models.space import Space
from ..prompts import load_prompt
from ..schemas.document import DocumentResponse
from ..schemas.note_output import NoteOutput
from ..services.model_provider import get_provider, get_setting
from ..worker import run_processing_pipeline

logger = logging.getLogger(__name__)

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


def _note_is_stub(note: Note) -> bool:
    """Detect whether a saved note is a stub by its content patterns."""
    content = note.content or ""
    # Strip frontmatter
    if content.startswith("---"):
        end = content.find("---", 3)
        content = content[end + 3:] if end != -1 else content
    return (
        "## Source Material" in content
        or len(content.strip()) < 500
    )


@router.get("/documents")
async def list_documents(db: AsyncSession = Depends(get_db), current_space: Space = Depends(get_current_space)) -> list[DocumentResponse]:
    result = await db.execute(select(Document).where(Document.space_id == current_space.id))
    rows = result.scalars().all()

    responses = []
    for r in rows:
        recent_notes: list[str] = []
        stub_count = 0
        if r.status in ("processing", "done"):
            notes_result = await db.execute(
                select(Note)
                .where(Note.document_id == r.id)
                .order_by(Note.id.desc())
                .limit(8)
            )
            notes = notes_result.scalars().all()
            recent_notes = [n.title for n in reversed(notes)]
            # Count total stubs for this document
            all_notes_result = await db.execute(
                select(Note).where(Note.document_id == r.id)
            )
            all_notes = all_notes_result.scalars().all()
            stub_count = sum(1 for n in all_notes if _note_is_stub(n))
        responses.append(DocumentResponse.from_row(r, recent_notes=recent_notes, stub_count=stub_count))
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


# ── Stub detection & repair ──────────────────────────────────────────


class StubNoteInfo(BaseModel):
    id: int
    title: str
    slug: str
    reason: str


def _body_from_content(content: str) -> str:
    """Strip YAML frontmatter and return the note body."""
    if content.startswith("---"):
        end = content.find("---", 3)
        return content[end + 3:] if end != -1 else content
    return content


@router.get("/documents/{doc_id}/stubs", dependencies=[Depends(require_admin)])
async def list_stubs(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[StubNoteInfo]:
    """Return all stub notes for a document."""
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id).where(Document.space_id == current_space.id)
    )
    if not doc_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Document not found")

    notes_result = await db.execute(select(Note).where(Note.document_id == doc_id))
    stubs = []
    for note in notes_result.scalars().all():
        body = _body_from_content(note.content or "")
        reason = None
        if "## Source Material" in body:
            reason = "raw source material (LLM generation failed)"
        elif len(body.strip()) < 500:
            reason = f"content too short ({len(body.strip())} chars)"
        if reason:
            stubs.append(StubNoteInfo(id=note.id, title=note.title, slug=note.slug, reason=reason))

    return stubs


class RepairResult(BaseModel):
    slug: str
    title: str
    success: bool
    error: str | None = None


NOTE_SYSTEM_REPAIR = load_prompt("create_note").format()


@router.post("/documents/{doc_id}/repair-stubs", dependencies=[Depends(require_admin)])
async def repair_stubs(
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[RepairResult]:
    """Attempt to regenerate all stub notes for a document using additional context."""
    doc_result = await db.execute(
        select(Document).where(Document.id == doc_id).where(Document.space_id == current_space.id)
    )
    doc = doc_result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    raw_text = doc.content_raw or ""

    notes_result = await db.execute(select(Note).where(Note.document_id == doc_id))
    all_notes = notes_result.scalars().all()

    stubs = [
        n for n in all_notes
        if "## Source Material" in _body_from_content(n.content or "")
        or len(_body_from_content(n.content or "").strip()) < 500
    ]

    if not stubs:
        return []

    all_titles = [n.title for n in all_notes]
    provider = await get_provider(db)
    model = await get_setting(db, "model_create")
    results: list[RepairResult] = []

    for note in stubs:
        logger.info("Repairing stub '%s' (model=%s)", note.title, model)
        try:
            page = note.page or 1
            start = max(0, (page - 2) * 3000)
            end_pos = min(len(raw_text), (page + 3) * 3000)
            source_text = raw_text[start:end_pos] or raw_text[:8000]

            linkable = [t for t in all_titles if t != note.title][:40]

            prompt = (
                f"Document: {doc.original_name}\n"
                f"Topic: {note.title}\n"
                f"Chapter/Section: {note.chapter or 'N/A'}\n"
                f"Page: {page}\n\n"
                f"Available notes to link to with [[Note Title]]:\n"
                + "\n".join(f"- {t}" for t in linkable)
                + f"\n\nSource text from document:\n{source_text}\n\n"
                "This note previously failed to generate properly. Create a detailed, substantive note. "
                "Use [!definition], [!theorem], [!example] callouts with ^block-ids. "
                "Include LaTeX math. Cross-link with [[wiki-links]]. "
                "Display math must have $$ on its own line."
            )

            new_output = await provider.complete_structured(
                NoteOutput,
                messages=[{"role": "user", "content": prompt}],
                system=NOTE_SYSTEM_REPAIR,
                max_tokens=8192,
                model=model,
            )

            assembled = new_output.assemble_markdown()
            if len(assembled) < 300 or len(new_output.main_content) < 100:
                raise ValueError(f"Repaired content still too short ({len(assembled)} chars)")

            # Preserve frontmatter, replace body
            existing = note.content or ""
            if existing.startswith("---"):
                fm_end = existing.find("---", 3)
                frontmatter = existing[: fm_end + 3] if fm_end != -1 else ""
            else:
                frontmatter = ""

            from ..workflow.nodes.create_notes import _normalize_content
            note.content = frontmatter + f"\n\n# {note.title}\n\n" + _normalize_content(assembled)
            note.summary = new_output.summary
            await db.commit()

            logger.info("  Repaired '%s': %d chars", note.title, len(assembled))
            results.append(RepairResult(slug=note.slug, title=note.title, success=True))

        except Exception as e:
            logger.warning("  Repair FAILED '%s': %s", note.title, e)
            results.append(RepairResult(slug=note.slug, title=note.title, success=False, error=str(e)))

    return results
