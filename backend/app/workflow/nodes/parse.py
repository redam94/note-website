import asyncio
import logging
import re
import subprocess
import sys

from sqlalchemy import select, update

from ...database import async_session
from ...models.document import Document
from ...models.extraction_profile import ExtractionProfile
from ...parsers.docx import parse_docx
from ...parsers.pdf import parse_pdf
from ...parsers.text import parse_text
from ..progress import set_step
from ..state import ProcessingState

logger = logging.getLogger(__name__)


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


def _run_extraction_script(script: str, file_path: str, original_text: str) -> str:
    """Run a user-defined extraction script in a subprocess.

    The script receives the file path as sys.argv[1] and must write the
    transformed text to stdout.  Falls back to original_text on any error.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-c", script, file_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
        logger.warning(
            "Extraction script exited %d: %s", result.returncode, result.stderr[:500]
        )
    except subprocess.TimeoutExpired:
        logger.warning("Extraction script timed out after 60 s — using standard output")
    except Exception as exc:
        logger.warning("Extraction script error: %s", exc)
    return original_text


async def parse_document(state: ProcessingState) -> ProcessingState:
    """Extract and clean text from the uploaded document."""
    doc_id = state["document_id"]
    file_path = state["file_path"]
    mime_type = state["mime_type"]
    profile_id = state.get("extraction_profile_id")

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

    # Run extraction profile script if one is configured
    prompt_additions = state.get("prompt_additions", "")
    doc_type_override = state.get("doc_type_override")
    if profile_id is not None:
        async with async_session() as db:
            result = await db.execute(
                select(ExtractionProfile).where(ExtractionProfile.id == profile_id)
            )
            profile = result.scalar_one_or_none()
        if profile and profile.script and profile.script.strip():
            await set_step(doc_id, f"Running extraction profile '{profile.name}'...")
            raw_text = await asyncio.to_thread(
                _run_extraction_script, profile.script, file_path, raw_text
            )
            raw_text = _clean_text(raw_text)
            page_texts = [{"page": 1, "text": raw_text}]
            logger.info("Extraction profile '%s' applied; %d chars", profile.name, len(raw_text))
        if profile and profile.prompt_additions:
            prompt_additions = profile.prompt_additions
        if profile and profile.doc_type_override:
            doc_type_override = profile.doc_type_override

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

    return {**state, "raw_text": raw_text, "page_texts": page_texts, "prompt_additions": prompt_additions, "doc_type_override": doc_type_override}
