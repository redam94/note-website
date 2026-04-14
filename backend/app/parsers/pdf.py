import pymupdf4llm


def parse_pdf(file_path: str) -> tuple[str, list[dict]]:
    """Parse a PDF file and return (full_text, page_texts).

    Uses PyMuPDF4LLM for LLM-optimised output:
    - Multi-column layout correctly reconstructed
    - Headings emitted as # / ## / ### Markdown
    - Tables emitted as Markdown table syntax
    - Inline and display LaTeX preserved where detected
    - Headers / footers stripped automatically

    page_texts is a list of {"page": int, "text": str} dicts (1-based page numbers).
    """
    chunks: list[dict] = pymupdf4llm.to_markdown(
        file_path,
        page_chunks=True,
        show_progress=False,
        write_images=False,
        embed_images=False,
    )

    page_texts: list[dict] = []
    full_parts: list[str] = []

    for chunk in chunks:
        # page_number is 1-based in the layout backend
        page_num: int = chunk["metadata"].get("page_number") or (len(page_texts) + 1)
        text: str = chunk.get("text") or ""
        if text.strip():
            page_texts.append({"page": page_num, "text": text})
            full_parts.append(text)

    return "\n\n".join(full_parts), page_texts
