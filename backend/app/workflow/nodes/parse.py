import asyncio
import re

from sqlalchemy import update

from ...database import async_session
from ...models.document import Document
from ...parsers.docx import parse_docx
from ...parsers.pdf import parse_pdf
from ...parsers.text import parse_text
from ..progress import set_step
from ..state import ProcessingState


def _clean_text(text: str) -> str:
    """Clean raw extracted text before it enters the LLM pipeline.

    Removes web clipping metadata, redundant whitespace, and artifacts
    that confuse note creation.
    """
    # Strip YAML frontmatter from clippings (title/source/author/tags blocks)
    text = re.sub(r"^---\n[\s\S]*?\n---\n*", "", text)

    # Remove common web clipping headers
    text = re.sub(
        r"^(title|source|author|published|created|description|tags)\s*:\s*.*$",
        "",
        text,
        flags=re.MULTILINE,
    )

    # Remove clipping tags like "clippings"
    text = re.sub(r'^"?clippings"?\s*$', "", text, flags=re.MULTILINE)

    # Collapse excessive blank lines (3+ → 2)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # Remove leading/trailing whitespace
    text = text.strip()

    return text


def _clean_page_texts(page_texts: list[dict]) -> list[dict]:
    """Clean each page's text."""
    cleaned = []
    for pt in page_texts:
        cleaned_text = _clean_text(pt["text"])
        if cleaned_text:  # Skip empty pages
            cleaned.append({"page": pt["page"], "text": cleaned_text})
    return cleaned


async def parse_document(state: ProcessingState) -> ProcessingState:
    """Extract and clean text from the uploaded document."""
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

    # Clean the extracted text
    raw_text = _clean_text(raw_text)
    page_texts = _clean_page_texts(page_texts)

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
