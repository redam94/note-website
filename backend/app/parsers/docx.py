from docx import Document


def parse_docx(file_path: str) -> tuple[str, list[dict]]:
    """Parse a DOCX file and return (full_text, page_texts).

    DOCX doesn't have page-level text, so page_texts is a single entry.
    """
    doc = Document(file_path)
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    full_text = "\n".join(paragraphs)
    return full_text, [{"page": 1, "text": full_text}]
