"""Graph hygiene: edge pruning, link prediction, consolidation candidates.

Classical graph-theoretic checks run against the assembled knowledge graph.
Input is a FullGraph from services.graph_builder (single source of truth for
edge assembly); outputs are structured reports — never direct DB writes.

Protected edges (depends_on, part_of) are never pruned — those carry
user-authored semantics and hierarchy invariants.

Node2Vec-based embeddings are opt-in: lazy-imported, gated on the
`graph_hygiene_use_embeddings` setting. Classical checks (disparity filter,
Adamic-Adar, neighborhood Jaccard + title TF-IDF) run regardless.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import networkx as nx
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.graph_edge import GraphEdge
from ..models.note import Note
from .graph_builder import FullGraph, build_full_graph

logger = logging.getLogger(__name__)

_PROTECTED_RELATIONSHIPS = frozenset({"part_of", "depends_on"})


@dataclass
class HygieneReport:
    edges_to_drop: list[dict] = field(default_factory=list)
    edges_to_suggest: list[dict] = field(default_factory=list)
    merge_candidates: list[dict] = field(default_factory=list)
    transitive_redundant_depends_on: list[dict] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


# ── Graph assembly ────────────────────────────────────────────────────


def _build_weighted_graph(graph: FullGraph) -> nx.Graph:
    G = nx.Graph()
    for n in graph.notes:
        G.add_node(n.id, title=n.title)
    for e in graph.edges:
        pair = (min(e.source, e.target), max(e.source, e.target))
        w = graph.co_reference.get(pair, 1)
        if G.has_edge(e.source, e.target):
            G[e.source][e.target]["weight"] += w
        else:
            G.add_edge(e.source, e.target, weight=w)
    return G


# ── 1. Disparity filter (Serrano, Boguñá & Vespignani, PNAS 2009) ───
#
# For each endpoint u of edge (u,v): p_uv = w_uv / s_u, where s_u is total
# incident weight. Under the null model of uniform weight distribution, the
# probability of seeing p_uv or more extreme is α_uv = (1 - p_uv)^(k_u - 1).
# The edge is kept if α_uv < α from EITHER endpoint. Lower α → stricter
# pruning. α=0.25 is a typical first-pass value for knowledge graphs.


def disparity_backbone(G: nx.Graph, alpha: float = 0.25) -> set[tuple[int, int]]:
    kept: set[tuple[int, int]] = set()
    for u in G.nodes():
        k = G.degree(u)
        if k < 2:
            for v in G.neighbors(u):
                kept.add((min(u, v), max(u, v)))
            continue
        s = sum(G[u][v]["weight"] for v in G.neighbors(u))
        if s <= 0:
            continue
        for v in G.neighbors(u):
            w = G[u][v]["weight"]
            p = w / s
            if (1.0 - p) ** (k - 1) < alpha:
                kept.add((min(u, v), max(u, v)))
    return kept


# ── 2. Transitive reduction on depends_on ────────────────────────────


def transitive_redundant_depends_on(graph: FullGraph) -> set[tuple[int, int]]:
    """Return (source, target) pairs that are redundant under transitive reduction.

    A→C is redundant if any longer path A→…→C already exists. Cycles (which
    shouldn't exist but do show up in LLM output) are broken by iteratively
    removing one edge per non-trivial strongly-connected component until the
    graph is a DAG. This is O((V+E) · iterations) and bounded; enumerating
    simple cycles (as the previous version did) is worst-case exponential.
    """
    D = nx.DiGraph()
    for e in graph.edges:
        if e.relationship == "depends_on":
            D.add_edge(e.source, e.target)
    if D.number_of_edges() == 0:
        return set()

    converged = False
    for _ in range(200):  # safety bound; usually converges in a handful of passes
        if nx.is_directed_acyclic_graph(D):
            converged = True
            break
        removed_any = False
        for scc in list(nx.strongly_connected_components(D)):
            if len(scc) < 2:
                continue
            sub = D.subgraph(scc)
            edge_iter = iter(sub.edges())
            try:
                u, v = next(edge_iter)
            except StopIteration:
                continue
            D.remove_edge(u, v)
            removed_any = True
        if not removed_any:
            break

    if not converged:
        logger.warning(
            "depends_on cycle-breaker exhausted %d iterations; skipping transitive reduction",
            200,
        )
        return set()

    try:
        reduced = nx.transitive_reduction(D)
    except Exception as e:
        logger.warning("transitive_reduction failed: %s", e)
        return set()

    return set(D.edges()) - set(reduced.edges())


# ── 3. Adamic-Adar link prediction ───────────────────────────────────


def adamic_adar_suggestions(
    G: nx.Graph,
    top_k_per_node: int = 5,
    min_score: float = 1.0,
) -> list[tuple[int, int, float]]:
    """Unordered (u, v, score) suggestions for high-value missing edges."""
    existing = {(min(u, v), max(u, v)) for u, v in G.edges()}
    pair_best: dict[tuple[int, int], float] = {}

    for u in G.nodes():
        if G.degree(u) < 2:
            continue
        u_neighbors = set(G.neighbors(u))
        two_hop: set[int] = set()
        for v in u_neighbors:
            for w in G.neighbors(v):
                if w != u and (min(u, w), max(u, w)) not in existing:
                    two_hop.add(w)
        if not two_hop:
            continue
        per_node: list[tuple[int, float]] = []
        for cand in two_hop:
            common = u_neighbors & set(G.neighbors(cand))
            if not common:
                continue
            score = sum(
                1.0 / math.log(G.degree(z))
                for z in common
                if G.degree(z) > 1
            )
            if score >= min_score:
                per_node.append((cand, score))
        per_node.sort(key=lambda t: -t[1])
        for cand, score in per_node[:top_k_per_node]:
            pair = (min(u, cand), max(u, cand))
            if score > pair_best.get(pair, 0):
                pair_best[pair] = score

    return [(u, v, s) for (u, v), s in pair_best.items()]


# ── 4. Merge candidates: neighborhood Jaccard × title TF-IDF ─────────


def _neighbor_jaccard(G: nx.Graph, u: int, v: int) -> float:
    if not (G.has_node(u) and G.has_node(v)):
        return 0.0
    nu = set(G.neighbors(u)) - {v}
    nv = set(G.neighbors(v)) - {u}
    if not nu and not nv:
        return 0.0
    union = nu | nv
    return len(nu & nv) / len(union) if union else 0.0


def _titles_tfidf_sparse(titles: list[str]):
    """Returns (sparse L2-normalized TF-IDF matrix, kind) where kind is
    'tfidf' (sklearn) or 'token-jaccard' (fallback producing dense rows)."""
    try:
        from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
        vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
        X = vec.fit_transform(titles)
        return X, "tfidf"
    except Exception:
        return None, "token-jaccard"


def merge_candidates(
    graph: FullGraph,
    G: nx.Graph,
    min_neighbor_jaccard: float = 0.5,
    min_title_similarity: float = 0.4,
    top_per_row: int = 10,
    cap_output: int = 500,
) -> list[dict]:
    """Row-by-row sparse cosine — never materializes a dense N×N matrix.

    For each note, computes one dense row of title similarities, picks the
    top-K candidates above `min_title_similarity`, then confirms via
    neighborhood Jaccard. Memory is O(nnz) of the sparse matrix plus O(N)
    per iteration — flat in N for large vaults.
    """
    import numpy as np
    if len(graph.notes) < 2:
        return []
    titles = [n.title for n in graph.notes]
    ids = [n.id for n in graph.notes]
    X, kind = _titles_tfidf_sparse(titles)

    # Precompute token sets for fallback
    token_sets: list[set[str]] | None = None
    if kind == "token-jaccard":
        token_sets = [set(t.lower().split()) for t in titles]

    candidates: list[dict] = []
    seen: set[tuple[int, int]] = set()
    n = len(titles)

    for i in range(n):
        # Compute similarity of row i to all rows — dense 1-D row only
        if kind == "tfidf" and X is not None:
            row = (X[i] @ X.T).toarray().ravel()
            row[i] = 0.0
        else:
            # Token Jaccard row
            assert token_sets is not None
            row = np.zeros(n)
            ti = token_sets[i]
            for j in range(n):
                if i == j or not ti or not token_sets[j]:
                    continue
                inter = len(ti & token_sets[j])
                union = len(ti | token_sets[j])
                if union:
                    row[j] = inter / union

        # Early out: only care about rows with any match above threshold
        if row.max() < min_title_similarity:
            continue

        top = np.argpartition(-row, min(top_per_row, n - 1))[:top_per_row]
        for j in top:
            j = int(j)
            if j <= i:
                continue
            t_sim = float(row[j])
            if t_sim < min_title_similarity:
                continue
            a_id, b_id = ids[i], ids[j]
            pair = (min(a_id, b_id), max(a_id, b_id))
            if pair in seen:
                continue
            nj = _neighbor_jaccard(G, a_id, b_id)
            if nj < min_neighbor_jaccard:
                continue
            seen.add(pair)
            candidates.append({
                "note_a_id": a_id,
                "note_a_title": titles[i],
                "note_b_id": b_id,
                "note_b_title": titles[j],
                "title_similarity": round(t_sim, 3),
                "neighbor_jaccard": round(nj, 3),
                "combined_score": round(0.5 * t_sim + 0.5 * nj, 3),
            })

    candidates.sort(key=lambda c: -c["combined_score"])
    return candidates[:cap_output]


# ── 5. Node2Vec embeddings (opt-in) ──────────────────────────────────


def node2vec_link_suggestions(
    G: nx.Graph,
    existing_pairs: set[tuple[int, int]],
    top_k: int = 10,
    min_cosine: float = 0.7,
    dimensions: int = 64,
    walk_length: int = 20,
    num_walks: int = 10,
    seed: int = 42,
) -> list[tuple[int, int, float]]:
    try:
        import numpy as np
        from node2vec import Node2Vec  # type: ignore
    except ImportError:
        logger.info("node2vec not installed — skipping embedding suggestions")
        return []
    if G.number_of_nodes() < 10 or G.number_of_edges() < G.number_of_nodes() // 2:
        return []

    try:
        n2v = Node2Vec(
            G,
            dimensions=dimensions,
            walk_length=walk_length,
            num_walks=num_walks,
            workers=1,
            quiet=True,
            seed=seed,
        )
        model = n2v.fit(window=5, min_count=1, batch_words=4, workers=1, seed=seed)
    except Exception as e:
        logger.warning("node2vec training failed: %s", e)
        return []

    import numpy as np
    nodes = list(G.nodes())
    embs = np.array([model.wv[str(n)] for n in nodes])
    norms = np.linalg.norm(embs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    embs_n = embs / norms
    sim_matrix = embs_n @ embs_n.T
    np.fill_diagonal(sim_matrix, -1.0)

    suggestions: dict[tuple[int, int], float] = {}
    probe_k = min(top_k * 2, len(nodes) - 1)
    for i, u in enumerate(nodes):
        top_idx = np.argpartition(-sim_matrix[i], probe_k)[:probe_k]
        for j_i in top_idx:
            j_i = int(j_i)
            v = nodes[j_i]
            if u == v:
                continue
            score = float(sim_matrix[i, j_i])
            if score < min_cosine:
                continue
            pair = (min(u, v), max(u, v))
            if pair in existing_pairs:
                continue
            if score > suggestions.get(pair, -1.0):
                suggestions[pair] = score

    out = [(u, v, s) for (u, v), s in suggestions.items()]
    out.sort(key=lambda t: -t[2])
    return out[:200]


# ── Analysis entry point ─────────────────────────────────────────────


async def analyze(
    db: AsyncSession,
    space_id: int,
    # α=0.5 is conservative: on small/young vaults where all edges have unit
    # weight, the disparity null model rejects everything at α=0.25. Users
    # who want more aggressive pruning lower α via the endpoint query param.
    disparity_alpha: float = 0.5,
    use_embeddings: bool = False,
    aa_top_k_per_node: int = 5,
    aa_min_score: float = 1.0,
) -> HygieneReport:
    """Run all hygiene checks. Pure analysis — no DB writes."""
    graph = await build_full_graph(db, space_id=space_id)
    G = _build_weighted_graph(graph)

    note_title = {n.id: n.title for n in graph.notes}

    # Pull persisted LLM edges — those are the only rows we can prune.
    note_ids_subq = select(Note.id).where(Note.space_id == space_id).scalar_subquery()
    db_edges_result = await db.execute(
        select(GraphEdge).where(
            GraphEdge.source_id.in_(note_ids_subq),
            GraphEdge.target_id.in_(note_ids_subq),
            GraphEdge.created_by == "llm",
        )
    )
    db_edges = db_edges_result.scalars().all()

    backbone = disparity_backbone(G, alpha=disparity_alpha)
    edges_to_drop: list[dict] = []
    for e in db_edges:
        if e.relationship_type in _PROTECTED_RELATIONSHIPS:
            continue
        pair = (min(e.source_id, e.target_id), max(e.source_id, e.target_id))
        if pair not in backbone:
            edges_to_drop.append({
                "edge_id": e.id,
                "source_id": e.source_id,
                "target_id": e.target_id,
                "source_title": note_title.get(e.source_id, "?"),
                "target_title": note_title.get(e.target_id, "?"),
                "relationship": e.relationship_type,
                "confidence": e.confidence,
                "reason": "below_disparity_threshold",
            })

    transitive_redundant = transitive_redundant_depends_on(graph)
    redundant_out: list[dict] = []
    for src, tgt in transitive_redundant:
        redundant_out.append({
            "source_id": src,
            "target_id": tgt,
            "source_title": note_title.get(src, "?"),
            "target_title": note_title.get(tgt, "?"),
            "reason": "implied_by_longer_path",
        })

    existing_pairs = {(min(u, v), max(u, v)) for u, v in G.edges()}
    aa_preds = adamic_adar_suggestions(
        G, top_k_per_node=aa_top_k_per_node, min_score=aa_min_score
    )
    edges_to_suggest: list[dict] = []
    for u, v, score in aa_preds:
        edges_to_suggest.append({
            "source_id": u, "target_id": v,
            "source_title": note_title.get(u, "?"),
            "target_title": note_title.get(v, "?"),
            "score": round(score, 3),
            "method": "adamic_adar",
        })

    if use_embeddings:
        n2v_preds = node2vec_link_suggestions(G, existing_pairs)
        for u, v, score in n2v_preds:
            edges_to_suggest.append({
                "source_id": u, "target_id": v,
                "source_title": note_title.get(u, "?"),
                "target_title": note_title.get(v, "?"),
                "score": round(score, 3),
                "method": "node2vec_cosine",
            })

    merges = merge_candidates(graph, G)

    stats = {
        "total_nodes": G.number_of_nodes(),
        "total_edges": G.number_of_edges(),
        "db_llm_edges_considered": len(db_edges),
        "backbone_edges": len(backbone),
        "edges_to_drop": len(edges_to_drop),
        "edges_to_suggest": len(edges_to_suggest),
        "merge_candidates": len(merges),
        "transitive_redundant_depends_on": len(redundant_out),
        "disparity_alpha": disparity_alpha,
        "embeddings_used": use_embeddings,
    }

    return HygieneReport(
        edges_to_drop=edges_to_drop,
        edges_to_suggest=edges_to_suggest,
        merge_candidates=merges,
        transitive_redundant_depends_on=redundant_out,
        stats=stats,
    )


# ── Apply: prune & promote ───────────────────────────────────────────


async def prune_edges(db: AsyncSession, edge_ids: list[int]) -> int:
    if not edge_ids:
        return 0
    # Defensive: refuse to delete edges in protected relationships even if the
    # caller passes their ids. analyze() already filters, but apply is a
    # separate entry point.
    protected_result = await db.execute(
        select(GraphEdge.id).where(
            and_(
                GraphEdge.id.in_(edge_ids),
                GraphEdge.relationship_type.in_(list(_PROTECTED_RELATIONSHIPS)),
            )
        )
    )
    protected = set(protected_result.scalars().all())
    prunable = [eid for eid in edge_ids if eid not in protected]
    if not prunable:
        return 0
    result = await db.execute(delete(GraphEdge).where(GraphEdge.id.in_(prunable)))
    await db.commit()
    return result.rowcount or 0


async def promote_suggestions(
    db: AsyncSession,
    suggestions: list[dict],
    relationship: str = "related",
    created_by: str = "hygiene",
) -> int:
    if not suggestions:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    inserted = 0
    for s in suggestions:
        src = s.get("source_id")
        tgt = s.get("target_id")
        if src is None or tgt is None or src == tgt:
            continue
        dup = await db.execute(
            select(GraphEdge.id).where(
                or_(
                    and_(GraphEdge.source_id == src, GraphEdge.target_id == tgt),
                    and_(GraphEdge.source_id == tgt, GraphEdge.target_id == src),
                )
            )
        )
        if dup.scalar_one_or_none() is not None:
            continue
        raw = float(s.get("score", 0.5))
        # AA scores are unbounded (>1 common) — squash; cosine is already 0..1.
        method = s.get("method", "")
        if method == "node2vec_cosine":
            confidence = max(0.0, min(1.0, raw))
        else:
            confidence = 1.0 - 1.0 / (1.0 + raw)
        db.add(GraphEdge(
            source_id=src,
            target_id=tgt,
            relationship_type=relationship,
            confidence=round(confidence, 3),
            created_by=created_by,
            created_at=now,
        ))
        inserted += 1
    await db.commit()
    return inserted
