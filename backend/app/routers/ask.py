from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..prompts import load_prompt
from ..schemas.ask import AskRequest
from ..services.model_provider import get_provider

router = APIRouter(prefix="/api")

QA_SYSTEM = load_prompt("qa").format()

_QA_SYSTEM_LEGACY = """\
You are a knowledgeable assistant answering questions from a personal knowledge base.
You will be given a question and a set of notes from the knowledge base.

Your answer MUST follow this exact format (Obsidian-compatible markdown):

1. Start with a `> [!summary]` callout (2-3 sentences summarizing the answer)

2. `## Answer` section with detailed subsections using `###`
   - Use `[[Note Title]]` wiki-link syntax when referencing notes (use the EXACT title from the provided notes)
   - Use LaTeX math where appropriate: inline `$...$` and display `$$...$$` (display math must have $$ on its own line)
   - Use `> [!definition]`, `> [!theorem]`, `> [!example]`, `> [!warning]`, `> [!tip]` callouts where appropriate
   - Be specific and cite note titles

3. `## Source Notes` section — a markdown table:
   | Note | Relevance |
   |------|-----------|
   | [[Note Title]] | Why this note was used |

4. `## Related Concepts` — bullet list of `[[Note Title]]` links to notes that are related but not directly cited

5. `## Follow-Up Questions` — 3-5 follow-up questions the user might ask next

Rules:
- Only reference notes that exist in the provided list
- Be detailed and thorough — this answer should be a standalone reference document
- Use the same academic but accessible tone as the source notes
- Format display math on separate lines ($$\\n...\\n$$)
"""


@router.post("/ask")
async def ask_knowledge_base(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.question:
        raise HTTPException(status_code=400, detail="No question provided")

    # Keyword-based retrieval
    words = [w for w in body.question.lower().split() if len(w) > 3]
    relevant_notes = []

    if words:
        conditions = []
        for w in words:
            conditions.append(Note.title.like(f"%{w}%"))
            conditions.append(Note.content.like(f"%{w}%"))
        result = await db.execute(select(Note).where(or_(*conditions)).limit(15))
        relevant_notes = result.scalars().all()

    if not relevant_notes:
        result = await db.execute(select(Note).limit(20))
        relevant_notes = result.scalars().all()

    # Build context with note titles (for wiki-linking)
    notes_context = "\n---\n".join(
        f'[Note: "{n.title}" (ID: {n.id})]\n{n.content[:1500]}'
        for n in relevant_notes
    )

    # List available note titles for the model to use in [[links]]
    all_result = await db.execute(select(Note.title))
    all_titles = [t for (t,) in all_result.all()]
    titles_list = "\n".join(f"- {t}" for t in all_titles[:80])

    messages = [
        {
            "role": "user",
            "content": (
                f"Available note titles for [[wiki-links]]:\n{titles_list}\n\n"
                f"Source notes:\n\n{notes_context}\n\n"
                f"Question: {body.question}"
            ),
        }
    ]

    provider = await get_provider(db)

    async def generate():
        async for chunk in provider.stream(
            messages=messages,
            system=QA_SYSTEM,
            max_tokens=8192,
            tier="advanced",
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


class SaveAnswerRequest(BaseModel):
    question: str
    answer: str
    tags: list[str] = []


@router.post("/ask/save")
async def save_answer(
    body: SaveAnswerRequest,
    db: AsyncSession = Depends(get_db),
):
    """Save a Q&A answer as a note in the knowledge base."""
    title = f"Q: {body.question}"
    slug_base = slugify(title, lowercase=True)

    # Ensure unique slug
    existing = await db.execute(select(Note.slug))
    existing_slugs = set(existing.scalars().all())
    slug = slug_base
    counter = 1
    while slug in existing_slugs:
        slug = f"{slug_base}-{counter}"
        counter += 1

    # Build tags
    tags = ["type/qa"] + [f"topic/{t}" for t in body.tags if t]
    now = datetime.now(timezone.utc)

    # Build frontmatter
    frontmatter_lines = [
        "---",
        f'title: "{title}"',
        "tags:",
    ]
    for tag in tags:
        frontmatter_lines.append(f"  - {tag}")
    frontmatter_lines.append(f"date_asked: {now.strftime('%Y-%m-%d')}")
    frontmatter_lines.append("---")
    frontmatter = "\n".join(frontmatter_lines)

    full_content = f"{frontmatter}\n\n# {body.question}\n\n{body.answer}"

    note = Note(
        document_id=None,
        parent_id=None,
        title=title,
        content=full_content,
        slug=slug,
        tags=json.dumps(tags),
        level=1,
        created_at=now.isoformat(),
        source="Q&A",
        summary=body.answer[:300] if body.answer else None,
    )

    db.add(note)
    await db.commit()
    await db.refresh(note)

    return {"id": note.id, "slug": note.slug, "title": note.title}
