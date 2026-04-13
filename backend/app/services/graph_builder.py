"""Shared graph-building logic.

Assembles the full knowledge graph from all 4 edge sources:
1. DB graph_edges (LLM cross-links)
2. Parent-child hierarchy
3. Wikilinks in note content
4. Frontmatter depends_on / used_by
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.graph_edge import GraphEdge
from ..models.note import Note


class EdgeTuple(NamedTuple):
    source: int
    target: int
    relationship: str
    confidence: float


@dataclass
class FullGraph:
    notes: list[Note]
    edges: list[EdgeTuple]
    degree_map: dict[int, int]
    co_reference: dict[tuple[int, int], int]  # (min_id, max_id) -> count


async def build_full_graph(db: AsyncSession, space_id: int | None = None) -> FullGraph:
    """Build the complete graph from all edge sources.

    Args:
        space_id: If provided, only include notes from this space.

    Returns a FullGraph with notes, deduplicated edges, degree map,
    and bidirectional co-reference counts for each node pair.
    """
    note_query = select(Note)
    if space_id is not None:
        note_query = note_query.where(Note.space_id == space_id)
    notes_result = await db.execute(note_query)
    all_notes = notes_result.scalars().all()

    note_ids = {n.id for n in all_notes}

    edges_result = await db.execute(select(GraphEdge))
    # Filter edges to only include those between notes in this space
    all_edges = [
        e for e in edges_result.scalars().all()
        if e.source_id in note_ids and e.target_id in note_ids
    ] if space_id is not None else edges_result.scalars().all()

    # Build title -> id map for wiki-link resolution
    title_to_id: dict[str, int] = {}
    for n in all_notes:
        title_to_id[n.title] = n.id

    # Collect all edges: DB edges + parent-child + wiki-links + frontmatter
    edge_set: set[tuple[int, int, str]] = set()
    edge_list: list[EdgeTuple] = []
    degree_map: dict[int, int] = defaultdict(int)
    # Bidirectional co-reference: counts directed references between pairs
    _directed_refs: dict[tuple[int, int], int] = defaultdict(int)

    def add_edge(source: int, target: int, relationship: str, confidence: float = 0.8):
        key = (source, target, relationship)
        if key not in edge_set and source != target:
            edge_set.add(key)
            edge_list.append(EdgeTuple(source, target, relationship, confidence))
            degree_map[source] += 1
            degree_map[target] += 1
            _directed_refs[(source, target)] += 1

    # 1. DB edges (from cross_link detection)
    for edge in all_edges:
        add_edge(edge.source_id, edge.target_id, edge.relationship_type, edge.confidence)

    # 2. Parent-child edges
    for note in all_notes:
        if note.parent_id:
            add_edge(note.parent_id, note.id, "part_of", 1.0)

    # 3. Wiki-link edges extracted from note content
    for note in all_notes:
        content = note.content or ""
        wiki_targets = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", content)
        for target_title in wiki_targets:
            if target_title.startswith("raw/"):
                continue
            target_id = title_to_id.get(target_title)
            if target_id and target_id != note.id:
                add_edge(note.id, target_id, "references", 0.7)

    # 4. depends_on / used_by from frontmatter
    for note in all_notes:
        content = note.content or ""
        fm_match = re.match(r"^---\n([\s\S]*?)\n---", content)
        if not fm_match:
            continue
        fm = fm_match.group(1)

        dep_section = re.search(r"depends_on:(.*?)(?=\n\w|\Z)", fm, re.DOTALL)
        if dep_section:
            dep_titles = re.findall(r"\[\[([^\]]+)\]\]", dep_section.group(1))
            for dep_title in dep_titles:
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    add_edge(note.id, dep_id, "depends_on", 0.9)

        used_section = re.search(r"used_by:(.*?)(?=\n\w|\Z)", fm, re.DOTALL)
        if used_section:
            used_titles = re.findall(r"\[\[([^\]]+)\]\]", used_section.group(1))
            for used_title in used_titles:
                used_id = title_to_id.get(used_title)
                if used_id:
                    add_edge(used_id, note.id, "depends_on", 0.9)

    # Compute bidirectional co-reference: for each pair (A,B), sum A->B + B->A
    co_reference: dict[tuple[int, int], int] = {}
    seen_pairs: set[tuple[int, int]] = set()
    for (src, tgt), count in _directed_refs.items():
        pair = (min(src, tgt), max(src, tgt))
        if pair not in seen_pairs:
            seen_pairs.add(pair)
            reverse_count = _directed_refs.get((tgt, src), 0)
            co_reference[pair] = count + reverse_count

    return FullGraph(
        notes=list(all_notes),
        edges=edge_list,
        degree_map=dict(degree_map),
        co_reference=co_reference,
    )
