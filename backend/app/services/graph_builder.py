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


async def build_full_graph(
    db: AsyncSession,
    space_id: int | None = None,
    is_admin: bool = True,
) -> FullGraph:
    """Build the complete graph from all edge sources.

    Args:
        space_id: If provided, only include notes from this space.
        is_admin: When False, hides notes with visibility='admin' and drops
            any edge whose endpoint isn't in the visible set.

    Returns a FullGraph with notes, deduplicated edges, degree map,
    and bidirectional co-reference counts for each node pair.
    """
    note_query = select(Note)
    if space_id is not None:
        note_query = note_query.where(Note.space_id == space_id)
    if not is_admin:
        note_query = note_query.where(Note.visibility == "public")
    notes_result = await db.execute(note_query)
    all_notes = notes_result.scalars().all()

    note_ids = {n.id for n in all_notes}

    if space_id is not None:
        visible_ids_subq = select(Note.id).where(Note.space_id == space_id)
        if not is_admin:
            visible_ids_subq = visible_ids_subq.where(Note.visibility == "public")
        visible_ids_subq = visible_ids_subq.scalar_subquery()
        edge_query = select(GraphEdge).where(
            GraphEdge.source_id.in_(visible_ids_subq),
            GraphEdge.target_id.in_(visible_ids_subq),
        )
    else:
        edge_query = select(GraphEdge)
        if not is_admin:
            visible_ids_subq = select(Note.id).where(Note.visibility == "public").scalar_subquery()
            edge_query = edge_query.where(
                GraphEdge.source_id.in_(visible_ids_subq),
                GraphEdge.target_id.in_(visible_ids_subq),
            )
    edges_result = await db.execute(edge_query)
    all_edges = edges_result.scalars().all()

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
        if key in edge_set or source == target:
            return
        # Drop edges that point outside the visible set (matters when non-admin
        # callers receive a subset of notes). DB-sourced edges were pre-filtered
        # by the subquery above, but parent-child / wikilink / frontmatter edges
        # are derived in-process and must be checked here.
        if source not in note_ids or target not in note_ids:
            return
        edge_set.add(key)
        edge_list.append(EdgeTuple(source, target, relationship, confidence))
        degree_map[source] += 1
        degree_map[target] += 1
        _directed_refs[(source, target)] += 1

    # 1. DB edges (from cross_link detection)
    # Skip "part_of" from graph_edges — hierarchy is authoritative via parent_id column;
    # LLM-generated part_of edges are often reversed and create cycles in the tree.
    for edge in all_edges:
        if edge.relationship_type == "part_of":
            continue
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

    # 5. GitHub issue / PR cross-references
    # Build (repo, number) → note_id lookup from GitHub-sourced notes so we
    # can resolve `#N` and `owner/repo#N` references in note content.
    _GH_REPO_IN_FM = re.compile(r"^repo:\s*(\S+)\s*$", re.MULTILINE)
    _GH_NUMBER_IN_FM = re.compile(r"^number:\s*(\d+)\s*$", re.MULTILINE)

    gh_ref_lookup: dict[tuple[str, int], int] = {}
    for note in all_notes:
        if not (note.source or "").startswith("github:"):
            continue
        content = note.content or ""
        fm_match = re.match(r"^---\n([\s\S]*?)\n---", content)
        if not fm_match:
            continue
        fm = fm_match.group(1)
        repo_m = _GH_REPO_IN_FM.search(fm)
        num_m = _GH_NUMBER_IN_FM.search(fm)
        if repo_m and num_m:
            try:
                gh_ref_lookup[(repo_m.group(1).strip(), int(num_m.group(1)))] = note.id
            except ValueError:
                continue

    if gh_ref_lookup:
        # Compiled once outside the loop
        _GH_TYPED = re.compile(
            r"\b(closes|fixes|resolves|close|fix|resolve|closed|fixed|resolved)"
            r"\s+(?:([A-Za-z0-9._-]+/[A-Za-z0-9._-]+))?#(\d+)\b",
            re.IGNORECASE,
        )
        _GH_QUALIFIED = re.compile(r"\b([A-Za-z0-9._-]+/[A-Za-z0-9._-]+)#(\d+)\b")
        _GH_BARE = re.compile(r"(?:^|[\s(\[,])#(\d+)\b")

        for note in all_notes:
            content = note.content or ""
            # Strip frontmatter before scanning so own-frontmatter fields
            # (title, html_url) don't create self-references.
            body = content
            fm_match = re.match(r"^---\n([\s\S]*?)\n---\n?", content)
            if fm_match:
                body = content[fm_match.end():]

            # Repo context for bare `#N` resolution — a note carries context
            # only if it *is* a GitHub note with a repo frontmatter field.
            my_repo: str | None = None
            if fm_match and (note.source or "").startswith("github:"):
                repo_m = _GH_REPO_IN_FM.search(fm_match.group(1))
                if repo_m:
                    my_repo = repo_m.group(1).strip()

            # Pass 1 — typed closing refs
            typed_pairs: set[tuple[int, int]] = set()
            for m in _GH_TYPED.finditer(body):
                qualified_repo = m.group(2)
                try:
                    number = int(m.group(3))
                except ValueError:
                    continue
                repo = qualified_repo or my_repo
                if not repo:
                    continue
                target_id = gh_ref_lookup.get((repo, number))
                if target_id and target_id != note.id:
                    add_edge(note.id, target_id, "closes", 0.9)
                    typed_pairs.add((note.id, target_id))

            # Pass 2 — qualified refs (owner/repo#N without closing verb)
            qualified_hits: set[tuple[int, int, int]] = set()
            for m in _GH_QUALIFIED.finditer(body):
                qualified_hits.add((m.start(), m.end(), int(m.group(2))))
                try:
                    number = int(m.group(2))
                except ValueError:
                    continue
                target_id = gh_ref_lookup.get((m.group(1), number))
                if not target_id or target_id == note.id:
                    continue
                if (note.id, target_id) in typed_pairs:
                    continue
                add_edge(note.id, target_id, "references", 0.7)

            # Pass 3 — bare `#N` (only within a repo-context note). Skip any
            # `#N` that was already consumed by a qualified `owner/repo#N`
            # match so we don't double-count.
            if my_repo:
                consumed_spans = {(s, e) for s, e, _ in qualified_hits}
                for m in _GH_BARE.finditer(body):
                    # Was this `#N` the tail of an already-matched qualified ref?
                    if any(s <= m.start() + 1 and m.end() <= e for s, e in consumed_spans):
                        continue
                    try:
                        number = int(m.group(1))
                    except ValueError:
                        continue
                    target_id = gh_ref_lookup.get((my_repo, number))
                    if not target_id or target_id == note.id:
                        continue
                    if (note.id, target_id) in typed_pairs:
                        continue
                    add_edge(note.id, target_id, "references", 0.6)

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
