import fitz  # pymupdf


def parse_pdf(file_path: str) -> tuple[str, list[dict]]:
    """Parse a PDF file and return (full_text, page_texts).

    page_texts is a list of {"page": int, "text": str} dicts.
    """
    doc = fitz.open(file_path)
    page_texts = []
    full_parts = []

    for page_num in range(len(doc)):
        page = doc[page_num]
        text = page.get_text()
        page_texts.append({"page": page_num + 1, "text": text})
        full_parts.append(text)

    doc.close()
    return "\n".join(full_parts), page_texts
