import json
import re

from ...database import async_session
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

_system_prompt = load_prompt("outline")


def _extract_header_hints(text: str) -> str:
    lines = text.split("\n")
    hints = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if re.match(r"^#{1,4}\s+", stripped):
            hints.append(f"Line {i + 1}: {stripped}")
        elif stripped.isupper() and 3 < len(stripped) < 100:
            hints.append(f"Line {i + 1}: {stripped}")
        elif re.match(r"^\d+(\.\d+)*\.?\s+[A-Z]", stripped):
            hints.append(f"Line {i + 1}: {stripped}")
    return "\n".join(hints[:50])


async def detect_outline(state: ProcessingState) -> ProcessingState:
    raw_text = state["raw_text"]
    page_texts = state["page_texts"]

    await set_step(state["document_id"], "Detecting document structure...")

    text_preview = raw_text[:3000]
    header_hints = _extract_header_hints(raw_text)

    prompt = (
        f"Document: {state['original_name']}\n\n"
        f"Detected potential headers:\n{header_hints}\n\n"
        f"Document preview (first ~3000 chars):\n{text_preview}\n\n"
        f"Total pages: {len(page_texts)}\n\n"
        "Analyze this document and return a structured outline as JSON."
    )

    async with async_session() as db:
        provider = await get_provider(db)

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=_system_prompt.format(),
            max_tokens=4096,
            tier="simple",
        )
        json_match = re.search(r"\[.*\]", response, re.DOTALL)
        outline = json.loads(json_match.group()) if json_match else json.loads(response)
    except (json.JSONDecodeError, Exception):
        outline = []
        for hint in header_hints.split("\n"):
            if hint:
                match = re.match(r"Line (\d+): (.+)", hint)
                if match:
                    outline.append({"title": match.group(2).strip(), "level": 1, "page_start": 1, "snippet": ""})

    await set_step(state["document_id"], f"Found {len(outline)} sections in outline")
    return {**state, "outline": outline}
