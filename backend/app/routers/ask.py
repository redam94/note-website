from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from slugify import slugify
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthUser, require_admin, require_user_with_key
from ..database import get_db
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..prompts import load_prompt
from ..schemas.ask import AskRequest
from ..services.graph_search import (
    find_by_tag,
    find_related,
    get_neighbors,
    grep_notes,
)
from ..services.model_provider import get_provider

router = APIRouter(prefix="/api")

QA_SYSTEM = load_prompt("qa").format()
RETRIEVAL_SYSTEM = load_prompt("retrieval").format()

_STATUS_PREFIX = "<<STATUS>>"


# ── Graph-search-based retrieval ──────────────────────────────────────


async def _retrieve_notes(
    db: AsyncSession,
    question: str,
    provider,
    status_fn,
) -> tuple[list, list[str]]:
    """Use Haiku + graph search tools to find the most relevant notes.

    Returns (relevant_notes, all_titles).
    """
    # Load all notes for catalog + lookup
    all_result = await db.execute(select(Note))
    all_notes = all_result.scalars().all()
    if not all_notes:
        return [], []

    note_map = {n.id: n for n in all_notes}
    all_titles = [n.title for n in all_notes]

    # Build compact catalog for Haiku
    catalog_lines = []
    for n in all_notes:
        tags_raw = n.tags or "[]"
        try:
            tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        except (json.JSONDecodeError, TypeError):
            tags = []
        summary = (n.summary or n.content[:120]).replace("\n", " ")[:100]
        catalog_lines.append(f"ID:{n.id} | {n.title} | tags:{','.join(tags[:3])} | {summary}")

    # ── Haiku plans retrieval strategy ────────────────────────────────
    await status_fn("Planning retrieval strategy...")

    search_terms = []
    relevant_tags = []
    seed_ids = []

    try:
        plan_response = await provider.complete(
            messages=[{
                "role": "user",
                "content": f"Question: {question}\n\nNote catalog ({len(all_notes)} notes):\n" + "\n".join(catalog_lines),
            }],
            system=RETRIEVAL_SYSTEM,
            max_tokens=1024,
            tier="simple",
        )
        json_match = re.search(r"\{.*\}", plan_response, re.DOTALL)
        if json_match:
            plan = json.loads(json_match.group())
            search_terms = plan.get("search_terms", [])
            relevant_tags = plan.get("relevant_tags", [])
            seed_ids = plan.get("seed_note_ids", [])
            reasoning = plan.get("reasoning", "")
            if reasoning:
                await status_fn(f"Strategy: {reasoning[:80]}")
    except Exception:
        search_terms = [w for w in question.lower().split() if len(w) > 3]

    # ── Execute graph search tools ────────────────────────────────────
    await status_fn("Searching with graph tools...")
    collected_ids: set[int] = set()

    # Grep for each search term
    for term in search_terms[:4]:
        results = await grep_notes(db, term, limit=8)
        for r in results:
            collected_ids.add(r["id"])

    # Tag search
    for tag in relevant_tags[:3]:
        results = await find_by_tag(db, tag, limit=6)
        for r in results:
            collected_ids.add(r["id"])

    # Graph expansion: 1-hop neighbors of seed notes
    for sid in seed_ids[:3]:
        if sid in note_map:
            collected_ids.add(sid)
            nbrs = await get_neighbors(db, sid, direction="both")
            if "neighbors" in nbrs:
                for n in nbrs["neighbors"]:
                    collected_ids.add(n["id"])

    # 2-hop related notes for top seeds
    for sid in seed_ids[:2]:
        if sid in note_map:
            related = await find_related(db, sid, limit=5)
            for r in related:
                collected_ids.add(r["id"])

    # Fallback if too few results
    if len(collected_ids) < 5:
        for w in [w for w in question.lower().split() if len(w) > 3][:3]:
            result = await db.execute(
                select(Note).where(
                    or_(Note.title.like(f"%{w}%"), Note.content.like(f"%{w}%"))
                ).limit(6)
            )
            for n in result.scalars().all():
                collected_ids.add(n.id)

    # Build sorted list
    relevant = [note_map[nid] for nid in collected_ids if nid in note_map]

    def score(note):
        s = 10 if note.id in seed_ids else 0
        tl = note.title.lower()
        cl = (note.content or "")[:2000].lower()
        for term in search_terms:
            if term.lower() in tl: s += 3
            if term.lower() in cl: s += 1
        return s

    relevant.sort(key=score, reverse=True)
    return relevant[:15], all_titles


# ── Ask endpoint ──────────────────────────────────────────────────────


@router.post("/ask", dependencies=[Depends(require_user_with_key)])
async def ask_knowledge_base(
    body: AskRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.question:
        raise HTTPException(status_code=400, detail="No question provided")

    async def generate():
        yield f"{_STATUS_PREFIX}Scanning knowledge base...\n"

        provider = await get_provider(db)

        async def status_fn(msg: str):
            pass  # Can't yield from nested async — status updates come from yield below

        # Run retrieval (we yield status updates inline)
        all_result = await db.execute(select(Note))
        all_notes = all_result.scalars().all()

        if not all_notes:
            yield "No notes found in the knowledge base. Upload some documents first."
            return

        note_map = {n.id: n for n in all_notes}
        all_titles = [n.title for n in all_notes]

        # Build catalog
        catalog_lines = []
        for n in all_notes:
            tags_raw = n.tags or "[]"
            try:
                tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
            except (json.JSONDecodeError, TypeError):
                tags = []
            summary = (n.summary or n.content[:120]).replace("\n", " ")[:100]
            catalog_lines.append(f"ID:{n.id} | {n.title} | tags:{','.join(tags[:3])} | {summary}")

        # Haiku plans retrieval
        yield f"{_STATUS_PREFIX}Planning retrieval strategy...\n"

        search_terms = []
        seed_ids = []

        try:
            plan_response = await provider.complete(
                messages=[{
                    "role": "user",
                    "content": f"Question: {body.question}\n\nNote catalog ({len(all_notes)} notes):\n" + "\n".join(catalog_lines),
                }],
                system=RETRIEVAL_SYSTEM,
                max_tokens=1024,
                tier="simple",
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
                relevant_tags = []
        except Exception:
            search_terms = [w for w in body.question.lower().split() if len(w) > 3]
            relevant_tags = []

        # Execute graph search
        yield f"{_STATUS_PREFIX}Searching with graph tools...\n"
        collected_ids: set[int] = set()

        for term in search_terms[:4]:
            results = await grep_notes(db, term, limit=8)
            for r in results:
                collected_ids.add(r["id"])

        for tag in relevant_tags[:3]:
            results = await find_by_tag(db, tag, limit=6)
            for r in results:
                collected_ids.add(r["id"])

        for sid in seed_ids[:3]:
            if sid in note_map:
                collected_ids.add(sid)
                nbrs = await get_neighbors(db, sid, direction="both")
                if "neighbors" in nbrs:
                    for n in nbrs["neighbors"]:
                        collected_ids.add(n["id"])

        for sid in seed_ids[:2]:
            if sid in note_map:
                related = await find_related(db, sid, limit=5)
                for r in related:
                    collected_ids.add(r["id"])

        if len(collected_ids) < 5:
            for w in [w for w in body.question.lower().split() if len(w) > 3][:3]:
                result = await db.execute(
                    select(Note).where(
                        or_(Note.title.like(f"%{w}%"), Note.content.like(f"%{w}%"))
                    ).limit(6)
                )
                for n in result.scalars().all():
                    collected_ids.add(n.id)

        relevant_notes = [note_map[nid] for nid in collected_ids if nid in note_map]

        def score(note):
            s = 10 if note.id in seed_ids else 0
            tl = note.title.lower()
            cl = (note.content or "")[:2000].lower()
            for term in search_terms:
                if term.lower() in tl: s += 3
                if term.lower() in cl: s += 1
            return s

        relevant_notes.sort(key=score, reverse=True)
        relevant_notes = relevant_notes[:15]

        source_titles = [n.title for n in relevant_notes[:5]]
        yield f"{_STATUS_PREFIX}Found {len(relevant_notes)} relevant notes\n"
        yield f"{_STATUS_PREFIX}Sources: {', '.join(source_titles)}\n"

        # Build context and stream
        yield f"{_STATUS_PREFIX}Generating answer...\n"

        notes_context = "\n---\n".join(
            f'[Note: "{n.title}" (ID: {n.id})]\n{n.content[:1500]}'
            for n in relevant_notes
        )
        titles_list = "\n".join(f"- {t}" for t in all_titles[:80])

        messages = [{
            "role": "user",
            "content": (
                f"Available note titles for [[wiki-links]]:\n{titles_list}\n\n"
                f"Source notes:\n\n{notes_context}\n\n"
                f"Question: {body.question}"
            ),
        }]

        async for chunk in provider.stream(
            messages=messages,
            system=QA_SYSTEM,
            max_tokens=8192,
            tier="advanced",
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

    tags = ["type/qa"] + [f"topic/{t}" for t in body.tags if t]
    now = datetime.now(timezone.utc)

    frontmatter_lines = ["---", f'title: "{title}"', "tags:"]
    for tag in tags:
        frontmatter_lines.append(f"  - {tag}")
    frontmatter_lines.append(f"date_asked: {now.strftime('%Y-%m-%d')}")
    frontmatter_lines.append("---")

    full_content = f"{chr(10).join(frontmatter_lines)}\n\n# {body.question}\n\n{body.answer}"

    note = Note(
        document_id=None, parent_id=None,
        title=title, content=full_content, slug=slug,
        tags=json.dumps(tags), level=1,
        created_at=now.isoformat(), source="Q&A",
        summary=body.answer[:300] if body.answer else None,
    )

    db.add(note)
    await db.commit()
    await db.refresh(note)

    wiki_targets = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", body.answer)
    if wiki_targets:
        title_result = await db.execute(select(Note.id, Note.title))
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
