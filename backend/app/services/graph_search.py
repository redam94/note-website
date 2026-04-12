"""Graph traversal and text search tools for agent use.

These functions provide a simple toolkit that an LLM agent or lightweight model
can call to navigate and search the knowledge graph without loading the entire
database into context.
"""

from __future__ import annotations

import json
import re
from collections import deque
from typing import Literal

from sqlalchemy import or_, select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.graph_edge import GraphEdge
from ..models.note import Note


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        return json.loads(raw) if isinstance(raw, str) else raw
    except (json.JSONDecodeError, TypeError):
        return []


def _note_summary(note: Note) -> dict:
    """Compact representation of a note for agent context windows."""
    return {
        "id": note.id,
        "title": note.title,
        "slug": note.slug,
        "level": note.level,
        "tags": _parse_tags(note.tags),
        "summary": note.summary or note.content[:200],
        "source": note.source,
        "chapter": note.chapter,
        "page": note.page,
    }


def _edge_dict(edge: GraphEdge) -> dict:
    return {
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "relationship": edge.relationship_type,
        "confidence": edge.confidence,
    }


# ---------------------------------------------------------------------------
# 1.  Get neighbours (1-hop)
# ---------------------------------------------------------------------------

async def get_neighbors(
    db: AsyncSession,
    note_id: int,
    direction: Literal["out", "in", "both"] = "both",
    relationship: str | None = None,
) -> dict:
    """Return immediate neighbours of a note.

    Args:
        note_id: The note to start from.
        direction: "out" (outlinks), "in" (backlinks), or "both".
        relationship: Optional filter for relationship type.

    Returns:
        {"note": {...}, "neighbors": [...], "edges": [...]}
    """
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        return {"error": f"Note {note_id} not found"}

    conditions = []
    if direction in ("out", "both"):
        conditions.append(GraphEdge.source_id == note_id)
    if direction in ("in", "both"):
        conditions.append(GraphEdge.target_id == note_id)

    edge_q = select(GraphEdge).where(or_(*conditions))
    if relationship:
        edge_q = edge_q.where(GraphEdge.relationship_type == relationship)

    edges_result = await db.execute(edge_q)
    edges = edges_result.scalars().all()

    neighbor_ids = set()
    for e in edges:
        if e.source_id != note_id:
            neighbor_ids.add(e.source_id)
        if e.target_id != note_id:
            neighbor_ids.add(e.target_id)

    # Include parent/children
    if note.parent_id and direction in ("in", "both"):
        neighbor_ids.add(note.parent_id)
    children_result = await db.execute(
        select(Note).where(Note.parent_id == note_id)
    )
    if direction in ("out", "both"):
        for child in children_result.scalars().all():
            neighbor_ids.add(child.id)

    notes_result = await db.execute(
        select(Note).where(Note.id.in_(neighbor_ids))
    )
    neighbors = [_note_summary(n) for n in notes_result.scalars().all()]

    return {
        "note": _note_summary(note),
        "neighbors": neighbors,
        "edges": [_edge_dict(e) for e in edges],
    }


# ---------------------------------------------------------------------------
# 2.  BFS / n-hop traversal
# ---------------------------------------------------------------------------

async def bfs_traverse(
    db: AsyncSession,
    start_id: int,
    max_depth: int = 2,
    max_nodes: int = 50,
    relationship: str | None = None,
) -> dict:
    """Breadth-first traversal from a starting note.

    Returns all reachable notes within `max_depth` hops.
    """
    visited: dict[int, int] = {}  # note_id -> depth
    queue: deque[tuple[int, int]] = deque([(start_id, 0)])

    while queue and len(visited) < max_nodes:
        nid, depth = queue.popleft()
        if nid in visited:
            continue
        visited[nid] = depth
        if depth >= max_depth:
            continue

        # Get edges
        edge_q = select(GraphEdge).where(
            or_(GraphEdge.source_id == nid, GraphEdge.target_id == nid)
        )
        if relationship:
            edge_q = edge_q.where(GraphEdge.relationship_type == relationship)
        edges_result = await db.execute(edge_q)

        for e in edges_result.scalars().all():
            next_id = e.target_id if e.source_id == nid else e.source_id
            if next_id not in visited:
                queue.append((next_id, depth + 1))

        # Parent/children
        note_result = await db.execute(select(Note).where(Note.id == nid))
        note = note_result.scalar_one_or_none()
        if note and note.parent_id and note.parent_id not in visited:
            queue.append((note.parent_id, depth + 1))
        children = await db.execute(select(Note.id).where(Note.parent_id == nid))
        for (cid,) in children.all():
            if cid not in visited:
                queue.append((cid, depth + 1))

    # Fetch full note summaries
    notes_result = await db.execute(
        select(Note).where(Note.id.in_(visited.keys()))
    )
    nodes = []
    for n in notes_result.scalars().all():
        info = _note_summary(n)
        info["depth"] = visited[n.id]
        nodes.append(info)

    nodes.sort(key=lambda x: x["depth"])

    return {"start_id": start_id, "max_depth": max_depth, "nodes": nodes}


# ---------------------------------------------------------------------------
# 3.  Find shortest path between two notes
# ---------------------------------------------------------------------------

async def find_path(
    db: AsyncSession,
    from_id: int,
    to_id: int,
    max_depth: int = 6,
) -> dict:
    """BFS shortest path between two notes through the graph."""
    parent_map: dict[int, int | None] = {from_id: None}
    queue: deque[tuple[int, int]] = deque([(from_id, 0)])

    while queue:
        nid, depth = queue.popleft()
        if nid == to_id:
            # Reconstruct path
            path_ids = []
            cur = to_id
            while cur is not None:
                path_ids.append(cur)
                cur = parent_map[cur]
            path_ids.reverse()

            notes_result = await db.execute(
                select(Note).where(Note.id.in_(path_ids))
            )
            note_map = {n.id: _note_summary(n) for n in notes_result.scalars().all()}
            path = [note_map[pid] for pid in path_ids if pid in note_map]
            return {"found": True, "path": path, "length": len(path) - 1}

        if depth >= max_depth:
            continue

        edges_result = await db.execute(
            select(GraphEdge).where(
                or_(GraphEdge.source_id == nid, GraphEdge.target_id == nid)
            )
        )
        for e in edges_result.scalars().all():
            next_id = e.target_id if e.source_id == nid else e.source_id
            if next_id not in parent_map:
                parent_map[next_id] = nid
                queue.append((next_id, depth + 1))

        # Parent/children edges
        note_result = await db.execute(select(Note).where(Note.id == nid))
        note = note_result.scalar_one_or_none()
        if note and note.parent_id and note.parent_id not in parent_map:
            parent_map[note.parent_id] = nid
            queue.append((note.parent_id, depth + 1))
        children = await db.execute(select(Note.id).where(Note.parent_id == nid))
        for (cid,) in children.all():
            if cid not in parent_map:
                parent_map[cid] = nid
                queue.append((cid, depth + 1))

    return {"found": False, "path": [], "length": -1}


# ---------------------------------------------------------------------------
# 4.  Find notes by tag
# ---------------------------------------------------------------------------

async def find_by_tag(
    db: AsyncSession,
    tag: str,
    limit: int = 30,
) -> list[dict]:
    """Find notes whose tags JSON array contains the given tag."""
    pattern = f'%"{tag}"%'
    result = await db.execute(
        select(Note).where(Note.tags.like(pattern)).limit(limit)
    )
    return [_note_summary(n) for n in result.scalars().all()]


# ---------------------------------------------------------------------------
# 5.  Grep / content search
# ---------------------------------------------------------------------------

async def grep_notes(
    db: AsyncSession,
    pattern: str,
    case_sensitive: bool = False,
    limit: int = 20,
) -> list[dict]:
    """Search note content and titles for a text pattern.

    Returns matching notes with the matched excerpt.
    """
    if case_sensitive:
        query = select(Note).where(
            or_(Note.title.contains(pattern), Note.content.contains(pattern))
        ).limit(limit)
    else:
        like_pat = f"%{pattern}%"
        query = select(Note).where(
            or_(Note.title.like(like_pat), Note.content.like(like_pat))
        ).limit(limit)

    result = await db.execute(query)
    matches = []
    pat_lower = pattern.lower()

    for n in result.scalars().all():
        content = n.content
        search_in = content if case_sensitive else content.lower()
        search_pat = pattern if case_sensitive else pat_lower
        idx = search_in.find(search_pat)

        if idx >= 0:
            start = max(0, idx - 80)
            end = min(len(content), idx + len(pattern) + 80)
            excerpt = (
                ("..." if start > 0 else "")
                + content[start:end]
                + ("..." if end < len(content) else "")
            )
        else:
            excerpt = content[:150] + "..."

        info = _note_summary(n)
        info["excerpt"] = excerpt
        info["match_in_title"] = pat_lower in n.title.lower()
        matches.append(info)

    matches.sort(key=lambda x: x["match_in_title"], reverse=True)
    return matches


# ---------------------------------------------------------------------------
# 6.  Regex search across notes
# ---------------------------------------------------------------------------

async def regex_search(
    db: AsyncSession,
    regex: str,
    limit: int = 20,
) -> list[dict]:
    """Search note content using a Python regex pattern.

    Falls back to plain grep if the regex is invalid.
    """
    try:
        compiled = re.compile(regex, re.IGNORECASE)
    except re.error:
        return await grep_notes(db, regex, limit=limit)

    # Load all notes (for regex we need Python-side matching)
    result = await db.execute(select(Note))
    matches = []

    for n in result.scalars().all():
        m = compiled.search(n.content) or compiled.search(n.title)
        if m:
            start = max(0, m.start() - 60)
            end = min(len(n.content), m.end() + 60)
            info = _note_summary(n)
            info["excerpt"] = n.content[start:end]
            info["match"] = m.group()
            matches.append(info)
            if len(matches) >= limit:
                break

    return matches


# ---------------------------------------------------------------------------
# 7.  Get note context (full surrounding info for an agent)
# ---------------------------------------------------------------------------

async def get_note_context(
    db: AsyncSession,
    note_id: int,
) -> dict:
    """Get comprehensive context around a note for agent use.

    Returns the note itself, its parent chain, children, siblings,
    and all directly connected notes with their relationship types.
    """
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        return {"error": f"Note {note_id} not found"}

    # Parent chain (walk up)
    parent_chain = []
    pid = note.parent_id
    while pid:
        p_result = await db.execute(select(Note).where(Note.id == pid))
        parent = p_result.scalar_one_or_none()
        if not parent:
            break
        parent_chain.append(_note_summary(parent))
        pid = parent.parent_id

    # Children
    children_result = await db.execute(
        select(Note).where(Note.parent_id == note_id)
    )
    children = [_note_summary(c) for c in children_result.scalars().all()]

    # Siblings (same parent)
    siblings = []
    if note.parent_id:
        sib_result = await db.execute(
            select(Note).where(
                and_(Note.parent_id == note.parent_id, Note.id != note_id)
            )
        )
        siblings = [_note_summary(s) for s in sib_result.scalars().all()]

    # Connected via edges
    edges_result = await db.execute(
        select(GraphEdge).where(
            or_(GraphEdge.source_id == note_id, GraphEdge.target_id == note_id)
        )
    )
    edges = edges_result.scalars().all()
    connected_ids = set()
    edge_list = []
    for e in edges:
        connected_ids.add(e.source_id if e.source_id != note_id else e.target_id)
        edge_list.append(_edge_dict(e))

    connected_result = await db.execute(
        select(Note).where(Note.id.in_(connected_ids))
    )
    connected = [_note_summary(n) for n in connected_result.scalars().all()]

    return {
        "note": {**_note_summary(note), "content": note.content},
        "parent_chain": parent_chain,
        "children": children,
        "siblings": siblings,
        "connected": connected,
        "edges": edge_list,
    }


# ---------------------------------------------------------------------------
# 8.  List all tags in the knowledge base
# ---------------------------------------------------------------------------

async def list_all_tags(db: AsyncSession) -> list[dict]:
    """Return all unique tags with their usage count."""
    result = await db.execute(select(Note.tags))
    tag_counts: dict[str, int] = {}
    for (tags_raw,) in result.all():
        for tag in _parse_tags(tags_raw):
            tag_counts[tag] = tag_counts.get(tag, 0) + 1
    return sorted(
        [{"tag": t, "count": c} for t, c in tag_counts.items()],
        key=lambda x: x["count"],
        reverse=True,
    )


# ---------------------------------------------------------------------------
# 9.  Find related notes (multi-hop, by shared tags + edges)
# ---------------------------------------------------------------------------

async def find_related(
    db: AsyncSession,
    note_id: int,
    limit: int = 10,
) -> list[dict]:
    """Find notes related to a given note by shared tags and graph proximity.

    Scores notes by: direct edge (3pts) + shared tag (1pt each) + 2-hop edge (1pt).
    """
    result = await db.execute(select(Note).where(Note.id == note_id))
    note = result.scalar_one_or_none()
    if not note:
        return []

    note_tags = set(_parse_tags(note.tags))
    scores: dict[int, float] = {}

    # Direct edges: 3 points
    edges_result = await db.execute(
        select(GraphEdge).where(
            or_(GraphEdge.source_id == note_id, GraphEdge.target_id == note_id)
        )
    )
    one_hop = set()
    for e in edges_result.scalars().all():
        other = e.target_id if e.source_id == note_id else e.source_id
        scores[other] = scores.get(other, 0) + 3 * e.confidence
        one_hop.add(other)

    # 2-hop edges: 1 point
    for hop1 in one_hop:
        edges2 = await db.execute(
            select(GraphEdge).where(
                or_(GraphEdge.source_id == hop1, GraphEdge.target_id == hop1)
            )
        )
        for e2 in edges2.scalars().all():
            other = e2.target_id if e2.source_id == hop1 else e2.source_id
            if other != note_id and other not in one_hop:
                scores[other] = scores.get(other, 0) + 1 * e2.confidence

    # Shared tags: 1 point per shared tag
    if note_tags:
        all_notes = await db.execute(select(Note).where(Note.id != note_id))
        for n in all_notes.scalars().all():
            shared = note_tags & set(_parse_tags(n.tags))
            if shared:
                scores[n.id] = scores.get(n.id, 0) + len(shared)

    # Fetch top results
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    if not ranked:
        return []

    ids = [r[0] for r in ranked]
    notes_result = await db.execute(select(Note).where(Note.id.in_(ids)))
    note_map = {n.id: n for n in notes_result.scalars().all()}

    results = []
    for nid, score in ranked:
        if nid in note_map:
            info = _note_summary(note_map[nid])
            info["relevance_score"] = round(score, 2)
            results.append(info)

    return results
