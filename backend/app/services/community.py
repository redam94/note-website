"""Community detection and subgraph compression.

Uses Louvain dendrogram for hierarchical community detection —
produces nested clusters that map to folder paths in the topic tree.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.note import Note
from ..models.subgraph_edge import SubgraphEdge
from ..models.subgraph_node import SubgraphNode
from ..prompts import load_prompt
from ..services.graph_builder import build_full_graph
from ..services.model_provider import ModelProvider, get_provider

logger = logging.getLogger(__name__)

LABEL_SYSTEM = load_prompt("community_label").format()
_CONCURRENCY = 6


# ---------------------------------------------------------------------------
# 1. Build networkx graph
# ---------------------------------------------------------------------------

def _build_networkx_graph(edges: list, co_reference: dict[tuple[int, int], int]):
    """Build a networkx Graph with co-reference weights."""
    import networkx as nx

    G = nx.Graph()
    for e in edges:
        pair = (min(e.source, e.target), max(e.source, e.target))
        weight = co_reference.get(pair, 1)
        if G.has_edge(e.source, e.target):
            G[e.source][e.target]["weight"] += weight
        else:
            G.add_edge(e.source, e.target, weight=weight)
    return G


# ---------------------------------------------------------------------------
# 2. Hierarchical community detection via dendrogram
# ---------------------------------------------------------------------------

def detect_hierarchy(G) -> list[dict[int, int]]:
    """Run Louvain and return the full dendrogram as a list of partitions.

    Returns a list of partitions from finest (leaf) to coarsest (root).
    Each partition is a dict mapping node_id -> community_id at that level.
    """
    if G.number_of_nodes() == 0:
        return []

    try:
        import community as community_louvain
        dendrogram = community_louvain.generate_dendrogram(G, weight="weight")
        # dendrogram[0] is the finest partition (most communities)
        # dendrogram[-1] is the coarsest (fewest communities)
        # Build partition at each level
        partitions = []
        for level in range(len(dendrogram)):
            partition = community_louvain.partition_at_level(dendrogram, level)
            partitions.append(partition)
        return partitions
    except (ImportError, Exception) as e:
        logger.warning("Louvain dendrogram failed (%s), falling back to flat detection", e)

    # Fallback: single-level greedy modularity
    try:
        import networkx as nx
        communities = nx.community.greedy_modularity_communities(G, weight="weight")
        partition = {}
        for cid, members in enumerate(communities):
            for node_id in members:
                partition[node_id] = cid
        return [partition]
    except Exception as e:
        logger.error("Greedy modularity also failed: %s", e)
        return []


# ---------------------------------------------------------------------------
# 3. Generate topic label via LLM
# ---------------------------------------------------------------------------

async def generate_topic_label(
    provider: ModelProvider,
    member_notes: list[Note],
) -> tuple[str, str]:
    """Generate a short topic label and summary for a cluster."""
    note_lines = []
    for n in member_notes[:20]:
        first_line = (n.content or "").split("\n")[0][:200]
        note_lines.append(f"- {n.title}: {first_line}")

    prompt = f"Cluster of {len(member_notes)} notes:\n" + "\n".join(note_lines)

    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=LABEL_SYSTEM,
            max_tokens=256,
            tier="simple",
        )
        json_match = re.search(r"\{.*\}", response, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else json.loads(response)
        label = data.get("label", "Unlabeled Cluster")
        summary = data.get("summary", "")
        return label, summary
    except (json.JSONDecodeError, Exception) as e:
        logger.warning("Topic labeling failed: %s", e)
        fallback_label = member_notes[0].title if member_notes else "Unlabeled Cluster"
        return fallback_label, ""


# ---------------------------------------------------------------------------
# 4. Build hierarchical clusters from dendrogram
# ---------------------------------------------------------------------------

def _build_hierarchy(
    partitions: list[dict[int, int]],
    note_map: dict[int, Note],
) -> list[dict]:
    """Convert a list of partitions into hierarchical cluster records.

    Returns a list of cluster dicts with:
      - level: 0 (coarsest) to N (finest)
      - community_key: tuple identifying this cluster at its level
      - member_ids: note IDs in this cluster (at the finest level only)
      - parent_key: the parent cluster's key at the next coarser level
    """
    if not partitions:
        return []

    # If only one level, treat it as flat
    if len(partitions) == 1:
        clusters = []
        for cid, members in _group_partition(partitions[0]).items():
            if len(members) >= 2:
                clusters.append({
                    "level": 0,
                    "key": (0, cid),
                    "parent_key": None,
                    "member_ids": members,
                })
        return clusters

    # Multiple levels: coarsest is last partition, finest is first
    # We reverse so level 0 = coarsest (top of tree)
    reversed_partitions = list(reversed(partitions))

    clusters = []
    # Track which fine-grained communities belong to which coarse ones
    for depth, partition in enumerate(reversed_partitions):
        grouped = _group_partition(partition)
        for cid, members in grouped.items():
            valid_members = [m for m in members if m in note_map]
            if len(valid_members) < 2:
                continue

            # Find parent: this community's members at the coarser level (depth-1)
            parent_key = None
            if depth > 0:
                coarser = reversed_partitions[depth - 1]
                # All members of this community map to the same coarser community
                representative = valid_members[0]
                if representative in coarser:
                    parent_key = (depth - 1, coarser[representative])

            clusters.append({
                "level": depth,
                "key": (depth, cid),
                "parent_key": parent_key,
                "member_ids": valid_members,
            })

    return clusters


def _group_partition(partition: dict[int, int]) -> dict[int, list[int]]:
    """Group a partition dict into {community_id: [node_ids]}."""
    groups: dict[int, list[int]] = defaultdict(list)
    for node_id, cid in partition.items():
        groups[cid].append(node_id)
    return groups


# ---------------------------------------------------------------------------
# 5. Full hierarchical community detection pipeline
# ---------------------------------------------------------------------------

async def run_community_detection(db: AsyncSession) -> list[dict]:
    """Run hierarchical community detection: detect, label, persist.

    Returns list of created cluster dicts with id, label, path, member_count.
    """
    graph = await build_full_graph(db)

    if not graph.edges:
        return []

    G = _build_networkx_graph(graph.edges, graph.co_reference)
    partitions = detect_hierarchy(G)

    if not partitions:
        return []

    note_map = {n.id: n for n in graph.notes}
    hierarchy = _build_hierarchy(partitions, note_map)

    if not hierarchy:
        return []

    provider = await get_provider(db)
    now = datetime.now(timezone.utc).isoformat()

    # Clear existing subgraph data
    await db.execute(delete(SubgraphEdge))
    await db.execute(delete(SubgraphNode))
    await db.execute(update(Note).values(cluster_id=None))
    await db.flush()

    # Label each cluster with concurrency control
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _label_cluster(cluster: dict) -> dict:
        async with sem:
            member_notes = [note_map[mid] for mid in cluster["member_ids"] if mid in note_map]
            label, summary = await generate_topic_label(provider, member_notes)
            return {**cluster, "label": label, "summary": summary}

    results = await asyncio.gather(
        *[_label_cluster(c) for c in hierarchy],
        return_exceptions=True,
    )

    # Persist clusters, building parent relationships
    key_to_db_id: dict[tuple, int] = {}
    created_clusters = []

    # Process in level order (coarsest first) so parents exist before children
    labeled = [r for r in results if not isinstance(r, BaseException)]
    labeled.sort(key=lambda r: r["level"])

    for r in labeled:
        parent_db_id = None
        if r["parent_key"] and r["parent_key"] in key_to_db_id:
            parent_db_id = key_to_db_id[r["parent_key"]]

        # Build hierarchical path from parent
        parent_path = ""
        if parent_db_id:
            parent_result = await db.execute(
                select(SubgraphNode).where(SubgraphNode.id == parent_db_id)
            )
            parent_node = parent_result.scalar_one_or_none()
            if parent_node and parent_node.path:
                parent_path = parent_node.path

        path = f"{parent_path}/{r['label']}" if parent_path else r["label"]

        cluster = SubgraphNode(
            parent_cluster_id=parent_db_id,
            label=r["label"],
            path=path,
            level=r["level"],
            member_node_ids=json.dumps(r["member_ids"]),
            summary=r["summary"],
            created_at=now,
            updated_at=now,
        )
        db.add(cluster)
        await db.flush()

        key_to_db_id[r["key"]] = cluster.id

        # Assign notes to their finest-level (deepest) cluster
        # Only assign if this is a finer level than what they already have
        for note_id in r["member_ids"]:
            await db.execute(
                update(Note).where(Note.id == note_id).values(cluster_id=cluster.id)
            )

        created_clusters.append({
            "id": cluster.id,
            "label": r["label"],
            "path": path,
            "level": r["level"],
            "summary": r["summary"],
            "member_count": len(r["member_ids"]),
            "parent_id": parent_db_id,
        })

    # Compute cross-cluster edges (between same-level clusters)
    node_to_cluster: dict[int, int] = {}
    for r in labeled:
        cluster_id = key_to_db_id.get(r["key"])
        if cluster_id:
            for nid in r["member_ids"]:
                node_to_cluster[nid] = cluster_id

    cross_edges: dict[tuple[int, int], int] = defaultdict(int)
    for e in graph.edges:
        src_cluster = node_to_cluster.get(e.source)
        tgt_cluster = node_to_cluster.get(e.target)
        if src_cluster and tgt_cluster and src_cluster != tgt_cluster:
            pair = (min(src_cluster, tgt_cluster), max(src_cluster, tgt_cluster))
            cross_edges[pair] += 1

    for (src_c, tgt_c), count in cross_edges.items():
        ce = SubgraphEdge(
            source_cluster_id=src_c,
            target_cluster_id=tgt_c,
            weight=float(count),
            cross_edge_count=count,
            created_at=now,
        )
        db.add(ce)

    await db.commit()
    return created_clusters


# ---------------------------------------------------------------------------
# 6. Incremental update for new notes
# ---------------------------------------------------------------------------

async def incremental_update(
    db: AsyncSession,
    new_note_ids: list[int],
    threshold: int = 3,
) -> list[dict]:
    """Incrementally update clusters when new notes are added."""
    if not new_note_ids:
        return []

    from ..models.graph_edge import GraphEdge

    clusters_result = await db.execute(select(SubgraphNode))
    clusters = clusters_result.scalars().all()

    if not clusters:
        return await run_community_detection(db)

    cluster_members: dict[int, set[int]] = {}
    for c in clusters:
        member_ids = json.loads(c.member_node_ids) if c.member_node_ids else []
        cluster_members[c.id] = set(member_ids)

    note_to_cluster: dict[int, int] = {}
    for cid, members in cluster_members.items():
        for nid in members:
            note_to_cluster[nid] = cid

    absorbed = []
    unabsorbed = []
    now = datetime.now(timezone.utc).isoformat()

    for new_id in new_note_ids:
        if new_id in note_to_cluster:
            continue

        edges_result = await db.execute(
            select(GraphEdge).where(GraphEdge.target_id == new_id)
        )
        inbound = edges_result.scalars().all()

        cluster_counts: dict[int, int] = defaultdict(int)
        for edge in inbound:
            src_cluster = note_to_cluster.get(edge.source_id)
            if src_cluster:
                cluster_counts[src_cluster] += 1

        if cluster_counts:
            best_cluster = max(cluster_counts, key=cluster_counts.get)
            if cluster_counts[best_cluster] >= threshold:
                cluster_members[best_cluster].add(new_id)
                note_to_cluster[new_id] = best_cluster

                await db.execute(
                    update(Note).where(Note.id == new_id).values(cluster_id=best_cluster)
                )
                await db.execute(
                    update(SubgraphNode)
                    .where(SubgraphNode.id == best_cluster)
                    .values(
                        member_node_ids=json.dumps(sorted(cluster_members[best_cluster])),
                        updated_at=now,
                    )
                )
                absorbed.append({"note_id": new_id, "cluster_id": best_cluster})
                continue

        unabsorbed.append(new_id)

    if absorbed:
        affected_cluster_ids = {a["cluster_id"] for a in absorbed}
        provider = await get_provider(db)

        for cid in affected_cluster_ids:
            member_ids = cluster_members[cid]
            notes_result = await db.execute(
                select(Note).where(Note.id.in_(member_ids))
            )
            member_notes = notes_result.scalars().all()
            label, summary = await generate_topic_label(provider, list(member_notes))

            await db.execute(
                update(SubgraphNode)
                .where(SubgraphNode.id == cid)
                .values(label=label, summary=summary, updated_at=now)
            )

    if unabsorbed:
        await db.commit()
        return await run_community_detection(db)

    await db.commit()
    return [{"absorbed": absorbed}]
