from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthUser, note_visibility_filter, require_user_with_key
from ..database import get_db
from ..dependencies import get_current_space
from ..models.document import Document
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.space import Space
from ..prompts import load_prompt
from ..schemas.ask import AskRequest
from ..services.model_provider import get_provider, get_setting
from ..services.retrieval import execute_retrieval

# Raw-source excerpt window (chars). ~3000 chars ≈ one PDF page.
_RAW_PAGE_CHARS = 3000
_RAW_MAX_PER_NOTE = 6000
_RAW_MAX_NOTES = 5  # only pull raw material for the top-N most relevant notes

router = APIRouter(prefix="/api")

QA_SYSTEM = load_prompt("qa").format()
RETRIEVAL_SYSTEM = load_prompt("retrieval").format()

_STATUS_PREFIX = "<<STATUS>>"


# ── Ask endpoint ──────────────────────────────────────────────────────


@router.post("/ask")
async def ask_knowledge_base(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
    user: AuthUser = Depends(require_user_with_key),
):
    if not body.question:
        raise HTTPException(status_code=400, detail="No question provided")

    is_admin = user.is_admin
    vis = note_visibility_filter(is_admin)

    async def generate():
        yield f"{_STATUS_PREFIX}Scanning knowledge base...\n"

        provider = await get_provider(db)
        ask_model = await get_setting(db, "model_ask")
        planner_model = await get_setting(db, "model_outline")

        all_result = await db.execute(
            select(Note).where(Note.space_id == current_space.id).where(vis)
        )
        all_notes = all_result.scalars().all()

        if not all_notes:
            yield "No notes found in the knowledge base. Upload some documents first."
            return

        note_map = {n.id: n for n in all_notes}
        all_titles = [n.title for n in all_notes]

        # Build compact catalog for Haiku planner
        catalog_lines = []
        for n in all_notes:
            tags_raw = n.tags or "[]"
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            except (json.JSONDecodeError, TypeError):
                tags = []
            summary = (n.summary or n.content[:120]).replace("\n", " ")[:100]
            catalog_lines.append(f"ID:{n.id} | {n.title} | tags:{','.join(tags[:3])} | {summary}")

        # Haiku plans retrieval strategy
        yield f"{_STATUS_PREFIX}Planning retrieval strategy...\n"

        search_terms: list[str] = []
        relevant_tags: list[str] = []
        seed_ids: list[int] = []

        try:
            plan_response = await provider.complete(
                messages=[{
                    "role": "user",
                    "content": f"Question: {body.question}\n\nNote catalog ({len(all_notes)} notes):\n" + "\n".join(catalog_lines),
                }],
                system=RETRIEVAL_SYSTEM,
                max_tokens=1024,
                model=planner_model,
            )
            json_match = re.search(r"\{.*\}", plan_response, re.DOTALL)
            if json_match:
                plan = json.loads(json_match.group())
                search_terms = plan.get("search_terms", [])
                relevant_tags = plan.get("relevant_tags", [])
                seed_ids = plan.get("seed_note_ids", [])
                reasoning = plan.get("reasoning", "")
                if reasoning:
                    yield f"{_STATUS_PREFIX}Strategy: {reasoning[:80]}\n"
            else:
                search_terms = [w for w in body.question.lower().split() if len(w) > 3]
        except Exception:
            search_terms = [w for w in body.question.lower().split() if len(w) > 3]

        yield f"{_STATUS_PREFIX}Searching with graph tools...\n"
        relevant_notes = await execute_retrieval(
            db=db,
            space_id=current_space.id,
            search_terms=search_terms,
            relevant_tags=relevant_tags,
            seed_ids=seed_ids,
            question=body.question,
            note_map=note_map,
            top_k=15,
            is_admin=is_admin,
        )

        source_titles = [n.title for n in relevant_notes[:5]]
        yield f"{_STATUS_PREFIX}Found {len(relevant_notes)} relevant notes\n"
        yield f"{_STATUS_PREFIX}Sources: {', '.join(source_titles)}\n"

        # ── Pull raw source excerpts from the documents behind the top notes ──
        top_notes_for_raw = [n for n in relevant_notes[:_RAW_MAX_NOTES] if n.document_id]
        doc_ids = list({n.document_id for n in top_notes_for_raw})
        doc_map: dict[int, Document] = {}
        if doc_ids:
            yield f"{_STATUS_PREFIX}Loading raw source material...\n"
            docs_result = await db.execute(
                select(Document).where(
                    Document.id.in_(doc_ids),
                    Document.space_id == current_space.id,
                )
            )
            doc_map = {d.id: d for d in docs_result.scalars().all()}

        raw_excerpts: list[str] = []
        for n in top_notes_for_raw:
            doc = doc_map.get(n.document_id)
            if not doc or not doc.content_raw:
                continue
            page = n.page or 1
            start = max(0, (page - 2) * _RAW_PAGE_CHARS)
            end = min(len(doc.content_raw), (page + 2) * _RAW_PAGE_CHARS)
            excerpt = doc.content_raw[start:end] or doc.content_raw[:_RAW_MAX_PER_NOTE]
            if len(excerpt) > _RAW_MAX_PER_NOTE:
                excerpt = excerpt[:_RAW_MAX_PER_NOTE]
            location = f"p. {page}" if n.page else "start"
            raw_excerpts.append(
                f'[Raw source: "{doc.original_name}" ({location}), cited by note "{n.title}"]\n{excerpt}'
            )

        raw_context = "\n\n---\n\n".join(raw_excerpts)

        # Build context and stream
        yield f"{_STATUS_PREFIX}Generating answer...\n"

        notes_context = "\n---\n".join(
            f'[Note: "{n.title}" (ID: {n.id})]\n{n.content[:1500]}'
            for n in relevant_notes
        )
        titles_list = "\n".join(f"- {t}" for t in all_titles[:80])

        user_content_parts = [
            f"Available note titles for [[wiki-links]]:\n{titles_list}",
            f"Source notes:\n\n{notes_context}",
        ]
        if raw_context:
            user_content_parts.append(
                f"Raw source document excerpts (the underlying material the notes were extracted from):\n\n{raw_context}"
            )
        user_content_parts.append(f"Question: {body.question}")

        messages = [{
            "role": "user",
            "content": "\n\n".join(user_content_parts),
        }]

        async for chunk in provider.stream(
            messages=messages,
            system=QA_SYSTEM,
            max_tokens=8192,
            model=ask_model,
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")


# ── Save Q&A ──────────────────────────────────────────────────────────


class SaveAnswerRequest(BaseModel):
    question: str
    answer: str
    tags: list[str] = []


@router.post("/ask/save", dependencies=[Depends(require_user_with_key)])
async def save_answer(
    body: SaveAnswerRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    title = f"Q: {body.question}"
    slug_base = slugify(title, lowercase=True)

    existing = await db.execute(select(Note.slug))
    existing_slugs = set(existing.scalars().all())
    slug = slug_base
    counter = 1
    while slug in existing_slugs:
        slug = f"{slug_base}-{counter}"
        counter += 1

    tags = [
        "source/ingested",
        "source/question",
        "type/qa",
    ] + [f"topic/{t}" for t in body.tags if t]
    now = datetime.now(timezone.utc)

    frontmatter_lines = ["---", f'title: "{title}"', "tags:"]
    for tag in tags:
        frontmatter_lines.append(f"  - {tag}")
    frontmatter_lines.append(f"date_asked: {now.strftime('%Y-%m-%d')}")
    frontmatter_lines.append("---")

    full_content = f"{chr(10).join(frontmatter_lines)}\n\n# {body.question}\n\n{body.answer}"

    # Place under "Questions" folder if it exists
    q_folder_result = await db.execute(
        select(Note).where(Note.title == "Index: Questions").where(Note.space_id == current_space.id)
    )
    q_folder = q_folder_result.scalar_one_or_none()
    q_parent_id = q_folder.id if q_folder else None

    note = Note(
        document_id=None, parent_id=q_parent_id,
        title=title, content=full_content, slug=slug,
        tags=json.dumps(tags), level=1,
        created_at=now.isoformat(), source="Q&A",
        summary=body.answer[:300] if body.answer else None,
        space_id=current_space.id,
    )

    db.add(note)
    await db.commit()
    await db.refresh(note)

    wiki_targets = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", body.answer)
    if wiki_targets:
        title_result = await db.execute(select(Note.id, Note.title).where(Note.space_id == current_space.id))
        title_to_id = {row.title: row.id for row in title_result.all()}
        for target_title in set(wiki_targets):
            if target_title.startswith("raw/"):
                continue
            target_id = title_to_id.get(target_title)
            if not target_id or target_id == note.id:
                continue
            existing_edge = await db.execute(
                select(GraphEdge).where(and_(
                    GraphEdge.source_id == note.id,
                    GraphEdge.target_id == target_id,
                ))
            )
            if not existing_edge.scalar_one_or_none():
                db.add(GraphEdge(
                    source_id=note.id, target_id=target_id,
                    relationship_type="references", confidence=0.8,
                    created_by="llm", created_at=now.isoformat(),
                ))
        await db.commit()

    return {"id": note.id, "slug": note.slug, "title": note.title}
