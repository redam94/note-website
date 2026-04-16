"""Shared retrieval execution layer used by ask.py and chat.py.

The planning step (what search terms to use) lives in the caller; this module
handles the parallel graph-search execution, fallback, scoring, and ranking.
"""
from __future__ import annotations

import asyncio
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note
from .graph_search import find_by_tag, find_related, get_neighbors, grep_notes


def score_note(note: Note, search_terms: list[str], seed_ids: set[int]) -> float:
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


async def execute_retrieval(
    db: AsyncSession,
    space_id: int,
    search_terms: list[str],
    relevant_tags: list[str],
    seed_ids: list[int],
    question: str,
    note_map: dict[int, Note],
    top_k: int = 15,
) -> list[Note]:
    """Execute graph-search retrieval and return ranked notes.

    Args:
        db: async DB session
        space_id: restrict LIKE fallback to this space
        search_terms: search keywords (from planner or keyword extraction)
        relevant_tags: tag names to boost (from planner or inferred)
        seed_ids: note IDs to start graph expansion from
        question: original question (used for fallback keyword extraction)
        note_map: pre-loaded {id: Note} mapping for the space
        top_k: how many notes to return
    """
    collected_ids: set[int] = set()

    # 1. Parallel grep for each search term
    grep_tasks = [grep_notes(db, term, limit=8, space_id=space_id) for term in search_terms[:4]]
    grep_results = await asyncio.gather(*grep_tasks, return_exceptions=True)
    for r in grep_results:
        if isinstance(r, list):
            for item in r:
                collected_ids.add(item["id"])

    # 2. Parallel tag search
    tag_tasks = [find_by_tag(db, tag, limit=6, space_id=space_id) for tag in relevant_tags[:3]]
    tag_results = await asyncio.gather(*tag_tasks, return_exceptions=True)
    for r in tag_results:
        if isinstance(r, list):
            for item in r:
                collected_ids.add(item["id"])

    # 3. Graph expansion: 1-hop neighbors of seed notes
    for sid in seed_ids[:3]:
        if sid in note_map:
            collected_ids.add(sid)
    nbr_tasks = [get_neighbors(db, sid, direction="both") for sid in seed_ids[:3] if sid in note_map]
    nbr_results = await asyncio.gather(*nbr_tasks, return_exceptions=True)
    for r in nbr_results:
        if isinstance(r, dict) and "neighbors" in r:
            for nbr in r["neighbors"]:
                collected_ids.add(nbr["id"])

    # 4. Related notes for top seeds
    rel_tasks = [find_related(db, sid, limit=5) for sid in seed_ids[:2] if sid in note_map]
    rel_results = await asyncio.gather(*rel_tasks, return_exceptions=True)
    for r in rel_results:
        if isinstance(r, list):
            for item in r:
                collected_ids.add(item["id"])

    # 5. LIKE fallback if too few results
    if len(collected_ids) < 5:
        fallback_terms = [w for w in question.lower().split() if len(w) > 3][:3]
        for term in fallback_terms:
            like = f"%{term}%"
            fb = await db.execute(
                select(Note)
                .where(Note.space_id == space_id)
                .where(or_(Note.title.like(like), Note.content.like(like)))
                .limit(6)
            )
            for n in fb.scalars().all():
                collected_ids.add(n.id)

    # 6. Score and rank
    seed_set = set(seed_ids)
    candidates = [note_map[nid] for nid in collected_ids if nid in note_map]
    candidates.sort(key=lambda n: score_note(n, search_terms, seed_set), reverse=True)
    return candidates[:top_k]
