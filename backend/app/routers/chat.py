"""Streaming multi-turn research assistant chat endpoint.

POST /api/chat — accepts a question + conversation history, retrieves relevant
notes via graph search, and streams a formatted markdown response.

Streaming protocol:
  <<STATUS>>message text     — progress updates (same as ask.py)
  <<SOURCES>>[{...}, ...]   — JSON array of source note metadata (new)
  [raw markdown content]     — streamed answer text
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_user_with_key
from ..database import get_db
from ..dependencies import get_current_space
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.space import Space
from ..prompts import load_prompt
from ..services.graph_search import find_by_tag, find_related, get_neighbors, grep_notes
from ..services.model_provider import get_provider, get_setting

router = APIRouter(prefix="/api")

CHAT_SYSTEM = load_prompt("chat").format()

_STATUS = "<<STATUS>>"
_SOURCES = "<<SOURCES>>"
_GRAPH  = "<<GRAPH>>"


def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return []


class ChatMessage(BaseModel):
    role: str   # "user" | "assistant"
    content: str


class ChatRequest(BaseModel):
    question: str
    history: list[ChatMessage] = []


# ── Retrieval helpers ─────────────────────────────────────────────────


def _score_note(note: Note, search_terms: list[str], seed_ids: set[int]) -> float:
    s = 10.0 if note.id in seed_ids else 0.0
    tl = note.title.lower()
    cl = (note.content or "")[:3000].lower()
    for term in search_terms:
        t = term.lower()
        if t in tl:
            s += 3.0
        if t in cl:
            s += 1.0
    return s


async def _retrieve(
    db: AsyncSession,
    question: str,
    space_id: int,
) -> tuple[list[Note], list[str]]:
    """Retrieve and rank the most relevant notes for the question.

    Returns (ranked_notes, all_titles).
    """
    # All notes for title list + fallback lookup
    all_result = await db.execute(select(Note).where(Note.space_id == space_id))
    all_notes = all_result.scalars().all()
    if not all_notes:
        return [], []

    note_map: dict[int, Note] = {n.id: n for n in all_notes}
    all_titles = [n.title for n in all_notes]

    # Keyword extraction — words longer than 3 chars, deduplicated
    words = list(dict.fromkeys(
        w for w in re.sub(r"[^\w\s]", " ", question.lower()).split()
        if len(w) > 3
    ))
    search_terms = words[:5]

    # ── Parallel retrieval ────────────────────────────────────────────
    grep_tasks = [grep_notes(db, term, limit=8, space_id=space_id) for term in search_terms[:4]]
    grep_results = await asyncio.gather(*grep_tasks, return_exceptions=True)

    collected_ids: set[int] = set()
    for r in grep_results:
        if isinstance(r, list):
            for item in r:
                collected_ids.add(item["id"])

    # Infer tags from question words and search by tag
    inferred_tags = [f"topic/{w}" for w in search_terms[:3]]
    tag_tasks = [find_by_tag(db, tag, limit=6, space_id=space_id) for tag in inferred_tags]
    tag_results = await asyncio.gather(*tag_tasks, return_exceptions=True)
    for r in tag_results:
        if isinstance(r, list):
            for item in r:
                collected_ids.add(item["id"])

    # Fallback: LIKE search if too few results
    if len(collected_ids) < 5 and search_terms:
        for term in search_terms[:3]:
            like = f"%{term}%"
            fb_result = await db.execute(
                select(Note)
                .where(Note.space_id == space_id)
                .where(or_(Note.title.like(like), Note.content.like(like)))
                .limit(6)
            )
            for n in fb_result.scalars().all():
                collected_ids.add(n.id)

    # Initial ranking to find seeds
    candidates = [note_map[nid] for nid in collected_ids if nid in note_map]
    candidates.sort(key=lambda n: _score_note(n, search_terms, set()), reverse=True)
    seed_ids = {n.id for n in candidates[:3]}

    # ── Graph expansion: 1-hop neighbors of top seeds ─────────────────
    nbr_tasks = [get_neighbors(db, sid, direction="both") for sid in list(seed_ids)[:3]]
    nbr_results = await asyncio.gather(*nbr_tasks, return_exceptions=True)
    for r in nbr_results:
        if isinstance(r, dict) and "neighbors" in r:
            for nbr in r["neighbors"]:
                collected_ids.add(nbr["id"])

    # Final ranking
    final_candidates = [note_map[nid] for nid in collected_ids if nid in note_map]
    final_candidates.sort(
        key=lambda n: _score_note(n, search_terms, seed_ids),
        reverse=True,
    )
    return final_candidates[:20], all_titles


# ── Endpoint ──────────────────────────────────────────────────────────


@router.post("/chat", dependencies=[Depends(require_user_with_key)])
async def chat_knowledge_base(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    if not body.question.strip():
        return StreamingResponse(
            iter(["No question provided."]),
            media_type="text/plain; charset=utf-8",
        )

    async def generate():
        yield f"{_STATUS}Searching knowledge base...\n"

        provider = await get_provider(db)
        model = await get_setting(db, "model_ask")

        # ── Retrieval ─────────────────────────────────────────────────
        top_notes, all_titles = await _retrieve(db, body.question, current_space.id)

        if not top_notes:
            yield f"{_STATUS}No notes found in knowledge base.\n"
            yield (
                "> [!warning] No Notes Found\n"
                "> The knowledge base is empty or no notes match this topic. "
                "Upload relevant documents first.\n"
            )
            return

        source_titles_preview = ", ".join(n.title for n in top_notes[:4])
        yield f"{_STATUS}Found {len(top_notes)} relevant notes: {source_titles_preview}\n"

        # ── Emit sources payload ──────────────────────────────────────
        sources_payload = [
            {
                "id": n.id,
                "title": n.title,
                "slug": n.slug,
                "chapter": n.chapter,
                "page": n.page,
                "summary": n.summary,
            }
            for n in top_notes[:12]
        ]
        yield f"{_SOURCES}{json.dumps(sources_payload)}\n"

        # ── Emit graph subgraph ───────────────────────────────────────
        graph_note_ids = [n.id for n in top_notes[:15]]
        graph_id_set = set(graph_note_ids)
        edges_result = await db.execute(
            select(GraphEdge).where(
                and_(
                    GraphEdge.source_id.in_(graph_id_set),
                    GraphEdge.target_id.in_(graph_id_set),
                )
            )
        )
        graph_edges = edges_result.scalars().all()

        # Compute degree from edge set
        degree_map: dict[int, int] = defaultdict(int)
        for e in graph_edges:
            degree_map[e.source_id] += 1
            degree_map[e.target_id] += 1

        graph_payload = {
            "nodes": [
                {
                    "id": n.id,
                    "title": n.title,
                    "slug": n.slug,
                    "tags": _parse_tags(n.tags),
                    "degree": degree_map.get(n.id, 0),
                    "level": n.level,
                    "documentId": n.document_id,
                    "nodeType": "note",
                    "summary": n.summary,
                }
                for n in top_notes[:15]
            ],
            "edges": [
                {
                    "source": e.source_id,
                    "target": e.target_id,
                    "relationship": e.relationship_type,
                    "confidence": e.confidence,
                }
                for e in graph_edges
            ],
        }
        yield f"{_GRAPH}{json.dumps(graph_payload)}\n"

        # ── Build LLM context ─────────────────────────────────────────
        notes_context = "\n---\n".join(
            f'[Note: "{n.title}" (slug: {n.slug})]\n{(n.content or "")[:2000]}'
            for n in top_notes
        )
        all_titles_list = "\n".join(f"- {t}" for t in all_titles[:100])

        # ── Build multi-turn messages ─────────────────────────────────
        # Include last 8 history turns (4 exchanges) to stay within context
        history_turns = body.history[-8:]
        messages: list[dict] = []

        for turn in history_turns:
            # Strip <<STATUS>> and <<SOURCES>> lines from prior assistant turns
            content = turn.content
            if turn.role == "assistant":
                content = "\n".join(
                    line for line in content.split("\n")
                    if not line.startswith(_STATUS)
                    and not line.startswith(_SOURCES)
                    and not line.startswith(_GRAPH)
                )
            messages.append({"role": turn.role, "content": content.strip()})

        # Current user turn — inject retrieved context
        messages.append({
            "role": "user",
            "content": (
                f"Available note titles for [[wiki-links]]:\n{all_titles_list}\n\n"
                f"Retrieved source notes:\n\n{notes_context}\n\n"
                f"Question: {body.question}"
            ),
        })

        yield f"{_STATUS}Generating answer...\n"

        async for chunk in provider.stream(
            messages=messages,
            system=CHAT_SYSTEM,
            max_tokens=8192,
            model=model,
        ):
            yield chunk

    return StreamingResponse(generate(), media_type="text/plain; charset=utf-8")
