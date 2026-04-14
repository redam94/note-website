from __future__ import annotations

import json
import re

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_user_with_key
from ..database import get_db
from ..dependencies import get_current_space
from ..models.note import Note
from ..models.space import Space
from ..models.subgraph_node import SubgraphNode
from ..prompts import load_prompt
from ..schemas.search import SearchResult, SmartSearchResponse, SmartSearchResult
from ..services.graph_search import _parse_tags
from ..services.model_provider import get_provider, get_setting

router = APIRouter(prefix="/api")

SMART_SEARCH_SYSTEM = load_prompt("smart_search").format()

_SMART_SEARCH_SYSTEM_LEGACY = """\
You are a knowledge base search agent. Given a user query and a catalog of notes, \
find the most relevant notes and explain why each is relevant.

You have access to a catalog of notes with their titles, summaries, and tags. \
Your job is to:

1. Interpret what the user is actually looking for (they might use vague language)
2. Rank the notes by relevance to the query
3. Explain WHY each note is relevant in 1 sentence
4. Suggest 2-3 follow-up queries the user might want to try

Return ONLY valid JSON:
{
  "interpretation": "What the user is looking for in precise terms",
  "results": [
    {"id": 1, "relevance": "Why this note matches the query", "score": 0.95}
  ],
  "suggested_queries": ["follow-up query 1", "follow-up query 2"]
}

Rules:
- Return at most 10 results, ranked by relevance
- Score from 0.0 to 1.0 (1.0 = perfect match)
- Only include notes with score >= 0.3
- The relevance explanation should be specific, not generic
- Suggested queries should explore related angles the user hasn't asked about
"""


@router.get("/search")
async def search_notes(
    q: str = Query(default=""),
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[SearchResult]:
    """Keyword search — fast, no LLM."""
    if not q:
        return []

    pattern = f"%{q}%"
    result = await db.execute(
        select(Note)
        .where(Note.space_id == current_space.id)
        .where(or_(Note.title.like(pattern), Note.content.like(pattern)))
        .limit(20)
    )
    rows = result.scalars().all()

    query_lower = q.lower()
    results: list[SearchResult] = []

    for n in rows:
        content_lower = n.content.lower()
        idx = content_lower.find(query_lower)
        if idx >= 0:
            start = max(0, idx - 60)
            end = min(len(n.content), idx + len(q) + 60)
            excerpt = (
                ("..." if start > 0 else "")
                + n.content[start:end]
                + ("..." if end < len(n.content) else "")
            )
        else:
            excerpt = n.content[:120] + "..."

        tags = _parse_tags(n.tags)
        results.append(
            SearchResult(
                id=n.id,
                title=n.title,
                slug=n.slug,
                excerpt=excerpt,
                tags=tags,
                matchType="keyword",
                score=1.0 if query_lower in n.title.lower() else 0.5,
            )
        )

    # Boost results from matching clusters
    cluster_results = await db.execute(
        select(SubgraphNode).where(SubgraphNode.space_id == current_space.id).where(
            or_(SubgraphNode.label.like(pattern), SubgraphNode.summary.like(pattern))
        )
    )
    matched_cluster_ids = set()
    for c in cluster_results.scalars().all():
        member_ids = json.loads(c.member_node_ids) if c.member_node_ids else []
        matched_cluster_ids.update(member_ids)

    existing_ids = {r.id for r in results}
    if matched_cluster_ids:
        # Boost existing matches that are in matching clusters
        for r in results:
            if r.id in matched_cluster_ids:
                r.score = min(r.score + 0.3, 1.0)

        # Add cluster members not yet in results
        missing_ids = matched_cluster_ids - existing_ids
        if missing_ids:
            extra = await db.execute(
                select(Note).where(Note.id.in_(missing_ids)).limit(10)
            )
            for n in extra.scalars().all():
                tags = _parse_tags(n.tags)
                results.append(
                    SearchResult(
                        id=n.id,
                        title=n.title,
                        slug=n.slug,
                        excerpt=n.content[:120] + "...",
                        tags=tags,
                        matchType="cluster",
                        score=0.6,
                    )
                )

    results.sort(key=lambda r: r.score, reverse=True)
    return results


@router.get("/search/enhanced")
async def enhanced_search(
    q: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> SmartSearchResponse:
    """Graph-powered search without LLM — available to all users."""
    from ..services.graph_search import find_by_tag, find_by_cluster, find_related, grep_notes, list_all_tags

    # Tokenize query
    stop_words = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for", "of", "and", "or", "with", "how", "what", "why", "when", "does", "can", "should"}
    terms = [w for w in q.lower().split() if len(w) > 2 and w not in stop_words]

    if not terms:
        return SmartSearchResponse(query=q, interpretation=q, results=[], suggested_queries=[])

    # 1. Grep search for each term
    scored: dict[int, float] = {}
    note_data: dict[int, dict] = {}

    for term in terms[:5]:
        matches = await grep_notes(db, term, limit=10, space_id=current_space.id)
        for m in matches:
            nid = m["id"]
            note_data[nid] = m
            pts = 3.0 if m.get("match_in_title") else 1.0
            scored[nid] = scored.get(nid, 0) + pts

    # 2. Tag search — check if any term matches a known tag
    all_tags = await list_all_tags(db, space_id=current_space.id)
    tag_names = {t["tag"].lower(): t["tag"] for t in all_tags}
    matched_tags = []
    for term in terms:
        for tname_lower, tname in tag_names.items():
            if term in tname_lower:
                matched_tags.append(tname)
                break

    for tag in matched_tags[:3]:
        tag_results = await find_by_tag(db, tag, limit=8, space_id=current_space.id)
        for r in tag_results:
            nid = r["id"]
            note_data.setdefault(nid, r)
            scored[nid] = scored.get(nid, 0) + 2.0

    # 2b. Cluster boost — if a note matches, boost its cluster siblings
    boosted_from_cluster: set[int] = set()
    for nid in list(scored.keys())[:5]:
        note_result = await db.execute(select(Note).where(Note.id == nid))
        note = note_result.scalar_one_or_none()
        if note and note.cluster_id and note.cluster_id not in boosted_from_cluster:
            boosted_from_cluster.add(note.cluster_id)
            cluster_notes = await find_by_cluster(db, note.cluster_id)
            for cn in cluster_notes:
                cnid = cn["id"]
                note_data.setdefault(cnid, cn)
                scored[cnid] = scored.get(cnid, 0) + 1.5

    # 3. Graph expansion — find related notes for top 3 results
    top_ids = sorted(scored, key=scored.get, reverse=True)[:3]
    for seed_id in top_ids:
        related = await find_related(db, seed_id, limit=5, space_id=current_space.id)
        for r in related:
            nid = r["id"]
            note_data.setdefault(nid, r)
            rel_score = r.get("relevance_score", 1.0)
            scored[nid] = scored.get(nid, 0) + min(rel_score, 2.0)

    # 4. Build results sorted by score
    max_score = max(scored.values()) if scored else 1.0
    smart_results: list[SmartSearchResult] = []

    for nid in sorted(scored, key=scored.get, reverse=True)[:10]:
        info = note_data.get(nid, {})
        norm_score = round(scored[nid] / max_score, 2)
        if norm_score < 0.15:
            continue

        # Build relevance reason from what matched
        reasons = []
        title = info.get("title", "")
        for term in terms:
            if term in title.lower():
                reasons.append(f"title matches '{term}'")
        if not reasons:
            reasons.append("content matches search terms")
        if nid in [r["id"] for tag in matched_tags[:1] for r in (await find_by_tag(db, tag, limit=20, space_id=current_space.id))]:
            reasons.append(f"tagged with matching topic")

        smart_results.append(SmartSearchResult(
            id=nid,
            title=title,
            slug=info.get("slug", ""),
            summary=info.get("summary"),
            tags=info.get("tags", []),
            relevance="; ".join(reasons[:2]),
            score=norm_score,
            source=info.get("source"),
            chapter=info.get("chapter"),
            page=info.get("page"),
            level=info.get("level", 1),
        ))

    # 5. Generate suggested queries from tags
    suggested = []
    for tag in matched_tags[:2]:
        suggested.append(f"notes tagged with {tag}")
    if len(terms) > 1:
        suggested.append(terms[0])  # suggest individual terms
    if not suggested:
        for t in all_tags[:3]:
            suggested.append(f"notes about {t['tag']}")

    interpretation = f"Searching for: {', '.join(terms)}"
    if matched_tags:
        interpretation += f" (matched tags: {', '.join(matched_tags[:3])})"

    return SmartSearchResponse(
        query=q,
        interpretation=interpretation,
        results=smart_results,
        suggested_queries=suggested[:3],
    )


@router.get("/search/smart", dependencies=[Depends(require_user_with_key)])
async def smart_search(
    q: str = Query(...),
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> SmartSearchResponse:
    """LLM-powered semantic search that understands intent."""
    # Load all note summaries as a compact catalog
    result = await db.execute(select(Note).where(Note.space_id == current_space.id))
    all_notes = result.scalars().all()

    catalog_entries = []
    note_map: dict[int, Note] = {}
    for n in all_notes:
        tags = _parse_tags(n.tags)
        summary = n.summary or n.content[:200]
        catalog_entries.append(
            f"ID:{n.id} | {n.title} | tags: {', '.join(tags)} | {summary}"
        )
        note_map[n.id] = n

    catalog_text = "\n".join(catalog_entries)

    # Include cluster context
    clusters_result = await db.execute(select(SubgraphNode).where(SubgraphNode.space_id == current_space.id))
    cluster_entries = []
    for c in clusters_result.scalars().all():
        member_ids = json.loads(c.member_node_ids) if c.member_node_ids else []
        cluster_entries.append(
            f"CLUSTER:{c.id} | {c.label} | {c.summary or ''} | members: {member_ids[:10]}"
        )
    cluster_text = "\n".join(cluster_entries) if cluster_entries else "No clusters detected yet."

    prompt = (
        f"User query: {q}\n\n"
        f"Note catalog ({len(all_notes)} notes):\n{catalog_text}\n\n"
        f"Topic clusters:\n{cluster_text}"
    )

    provider = await get_provider(db)
    model = await get_setting(db, "model_ask")

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=SMART_SEARCH_SYSTEM,
            max_tokens=2048,
            model=model,
        )

        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
        else:
            data = json.loads(response)
    except (json.JSONDecodeError, Exception):
        # Fallback to keyword search
        keyword_results = await search_notes(q, db, current_space)
        return SmartSearchResponse(
            query=q,
            interpretation=q,
            results=[
                SmartSearchResult(
                    id=r.id,
                    title=r.title,
                    slug=r.slug,
                    summary=r.excerpt,
                    tags=r.tags,
                    relevance="Keyword match",
                    score=r.score,
                    source=None,
                    chapter=None,
                    page=None,
                    level=1,
                )
                for r in keyword_results[:10]
            ],
            suggested_queries=[],
        )

    # Build results from LLM response
    smart_results: list[SmartSearchResult] = []
    for item in data.get("results", []):
        note_id = item.get("id")
        note = note_map.get(note_id)
        if not note:
            continue
        tags = _parse_tags(note.tags)
        smart_results.append(
            SmartSearchResult(
                id=note.id,
                title=note.title,
                slug=note.slug,
                summary=note.summary or note.content[:200],
                tags=tags,
                relevance=item.get("relevance", ""),
                score=item.get("score", 0.5),
                source=note.source,
                chapter=note.chapter,
                page=note.page,
                level=note.level,
            )
        )

    return SmartSearchResponse(
        query=q,
        interpretation=data.get("interpretation", q),
        results=smart_results,
        suggested_queries=data.get("suggested_queries", []),
    )
