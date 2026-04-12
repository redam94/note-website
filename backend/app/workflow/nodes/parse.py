import asyncio

from sqlalchemy import update

from ...database import async_session
from ...models.document import Document
from ...parsers.docx import parse_docx
from ...parsers.pdf import parse_pdf
from ...parsers.text import parse_text
from ..progress import set_step
from ..state import ProcessingState


async def parse_document(state: ProcessingState) -> ProcessingState:
    """Extract text from the uploaded document."""
    doc_id = state["document_id"]
    file_path = state["file_path"]
    mime_type = state["mime_type"]

    await set_step(doc_id, "Parsing document...")

    if mime_type == "application/pdf":
        raw_text, page_texts = await asyncio.to_thread(parse_pdf, file_path)
    elif mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        raw_text, page_texts = await asyncio.to_thread(parse_docx, file_path)
    else:
        raw_text, page_texts = await parse_text(file_path)

    page_count = len(page_texts)
    word_count = len(raw_text.split())
    await set_step(doc_id, f"Parsed {page_count} pages, {word_count:,} words")

    # Save raw text
    async with async_session() as db:
        await db.execute(
            update(Document)
            .where(Document.id == doc_id)
            .values(content_raw=raw_text, status="processing")
        )
        await db.commit()

    return {**state, "raw_text": raw_text, "page_texts": page_texts}
