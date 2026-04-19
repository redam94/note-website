import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthUser, get_current_user, note_visibility_filter
from ..database import get_db
from ..dependencies import get_current_space
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.space import Space
from ..models.subgraph_node import SubgraphNode
from ..models.subgraph_edge import SubgraphEdge
from ..schemas.graph import GraphData, GraphEdgeData, GraphNode
from ..services.graph_builder import build_full_graph

router = APIRouter(prefix="/api")


_TOPIC_ID_BASE = -2_000_000  # below cluster ids to avoid collisions


def _topic_display_name(tag: str) -> str:
    """Derive a display name from a `topic/<name>` tag."""
    name = tag[len("topic/") :] if tag.startswith("topic/") else tag
    return name.replace("-", " ").replace("_", " ").strip() or tag


@router.get("/graph")
async def get_graph(
    db: AsyncSession = Depends(get_db),
    include_clusters: bool = Query(False),
    include_topics: bool = Query(False),
    current_space: Space = Depends(get_current_space),
    user: AuthUser = Depends(get_current_user),
) -> GraphData:
    graph = await build_full_graph(db, space_id=current_space.id, is_admin=user.is_admin)

    edge_list = [
        GraphEdgeData(
            source=e.source, target=e.target,
            relationship=e.relationship, confidence=e.confidence,
        )
        for e in graph.edges
    ]

    note_tags: dict[int, list[str]] = {}
    nodes: list[GraphNode] = []
    for n in graph.notes:
        tags_raw = n.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        note_tags[n.id] = tags
        nodes.append(
            GraphNode(
                id=n.id,
                title=n.title,
                slug=n.slug,
                level=n.level,
                tags=tags,
                degree=graph.degree_map.get(n.id, 0),
                documentId=n.document_id,
            )
        )

    if include_topics:
        topic_members: dict[str, list[int]] = {}
        for note_id, tags in note_tags.items():
            for tag in tags:
                if isinstance(tag, str) and tag.startswith("topic/"):
                    topic_members.setdefault(tag, []).append(note_id)

        for idx, (tag, member_ids) in enumerate(sorted(topic_members.items())):
            topic_id = _TOPIC_ID_BASE - idx
            display_name = _topic_display_name(tag)
            nodes.append(
                GraphNode(
                    id=topic_id,
                    title=display_name,
                    slug=tag,  # slug carries the full `topic/<name>` for click routing
                    level=0,
                    tags=["type/topic"],
                    degree=len(member_ids),
                    documentId=None,
                    nodeType="topic",
                )
            )
            for member_id in member_ids:
                edge_list.append(
                    GraphEdgeData(
                        source=member_id,
                        target=topic_id,
                        relationship="topic_of",
                        confidence=1.0,
                    )
                )

    if include_clusters:
        clusters_result = await db.execute(
            select(SubgraphNode).where(SubgraphNode.space_id == current_space.id)
        )
        clusters = clusters_result.scalars().all()
        for c in clusters:
            member_ids = json.loads(c.member_node_ids) if c.member_node_ids else []
            nodes.append(
                GraphNode(
                    id=-c.id,  # negative IDs to avoid collision with note IDs
                    title=c.label,
                    slug=f"cluster-{c.id}",
                    level=0,
                    tags=[],
                    degree=len(member_ids),
                    documentId=None,
                    nodeType="subgraph",
                    summary=c.summary,
                    clusterLabel=c.label,
                )
            )

        cluster_edges_result = await db.execute(
            select(SubgraphEdge).where(SubgraphEdge.space_id == current_space.id)
        )
        for ce in cluster_edges_result.scalars().all():
            edge_list.append(
                GraphEdgeData(
                    source=-ce.source_cluster_id,
                    target=-ce.target_cluster_id,
                    relationship="cluster_link",
                    confidence=1.0,
                )
            )

    return GraphData(nodes=nodes, edges=edge_list)


@router.get("/graph/local/{note_id}")
async def get_local_graph(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
    user: AuthUser = Depends(get_current_user),
) -> GraphData:
    """Return only the 1-hop neighborhood of a note (the note + its direct neighbors).

    Much cheaper than /graph — avoids scanning all note content; used by the
    per-note sidebar graph so pages don't have to load the entire knowledge base.
    """
    vis = note_visibility_filter(user.is_admin)
    # Verify the note exists in this space and is visible to the caller
    note_res = await db.execute(
        select(Note).where(Note.id == note_id, Note.space_id == current_space.id).where(vis)
    )
    if not note_res.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Note not found")

    # 1. Fetch all DB cross-link edges that touch this note
    db_edges_res = await db.execute(
        select(GraphEdge).where(
            (GraphEdge.source_id == note_id) | (GraphEdge.target_id == note_id)
        )
    )
    db_edges = db_edges_res.scalars().all()
    neighbor_ids: set[int] = {note_id}
    for e in db_edges:
        neighbor_ids.add(e.source_id)
        neighbor_ids.add(e.target_id)

    # 2. Fetch all notes in the neighborhood (visibility-filtered)
    notes_res = await db.execute(
        select(Note).where(Note.id.in_(neighbor_ids), Note.space_id == current_space.id).where(vis)
    )
    neighbor_notes = {n.id: n for n in notes_res.scalars().all()}

    # 3. Add parent-child edges (parent → note and note → children)
    all_neighbor_ids = set(neighbor_notes.keys())
    children_res = await db.execute(
        select(Note).where(Note.parent_id == note_id, Note.space_id == current_space.id).where(vis)
    )
    for child in children_res.scalars().all():
        all_neighbor_ids.add(child.id)
        neighbor_notes[child.id] = child

    # Also include the note's own parent if present and visible
    focus_note = neighbor_notes.get(note_id)
    if focus_note and focus_note.parent_id:
        parent_res = await db.execute(
            select(Note)
            .where(Note.id == focus_note.parent_id, Note.space_id == current_space.id)
            .where(vis)
        )
        parent = parent_res.scalar_one_or_none()
        if parent:
            all_neighbor_ids.add(parent.id)
            neighbor_notes[parent.id] = parent

    # 4. Build edge list (only edges between nodes in the neighborhood)
    edge_list: list[GraphEdgeData] = []
    edge_set: set[tuple[int, int, str]] = set()

    def add_edge(src: int, tgt: int, rel: str, conf: float = 0.8):
        key = (src, tgt, rel)
        if key not in edge_set and src != tgt and src in all_neighbor_ids and tgt in all_neighbor_ids:
            edge_set.add(key)
            edge_list.append(GraphEdgeData(source=src, target=tgt, relationship=rel, confidence=conf))

    for e in db_edges:
        if e.relationship_type != "part_of":
            add_edge(e.source_id, e.target_id, e.relationship_type, e.confidence)

    for n in neighbor_notes.values():
        if n.parent_id:
            add_edge(n.parent_id, n.id, "part_of", 1.0)

    # 5. Build node list with degrees
    degree_map: dict[int, int] = {}
    for e in edge_list:
        degree_map[e.source] = degree_map.get(e.source, 0) + 1
        degree_map[e.target] = degree_map.get(e.target, 0) + 1

    nodes: list[GraphNode] = []
    note_tag_map: dict[int, list[str]] = {}
    for n in neighbor_notes.values():
        tags_raw = n.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        note_tag_map[n.id] = tags
        nodes.append(GraphNode(
            id=n.id,
            title=n.title,
            slug=n.slug,
            level=n.level,
            tags=tags,
            degree=degree_map.get(n.id, 0),
            documentId=n.document_id,
        ))

    # Add topic-tag nodes connected to the focus note
    focus_tags = note_tag_map.get(note_id, [])
    focus_topics = sorted({t for t in focus_tags if isinstance(t, str) and t.startswith("topic/")})
    for idx, tag in enumerate(focus_topics):
        topic_id = -2_000_000 - idx
        # Count members across the neighborhood (cheap; not global)
        members = [nid for nid, ts in note_tag_map.items() if tag in ts]
        nodes.append(GraphNode(
            id=topic_id,
            title=_topic_display_name(tag),
            slug=tag,
            level=0,
            tags=["type/topic"],
            degree=len(members),
            documentId=None,
            nodeType="topic",
        ))
        for m in members:
            edge_list.append(GraphEdgeData(
                source=m, target=topic_id, relationship="topic_of", confidence=1.0,
            ))

    return GraphData(nodes=nodes, edges=edge_list)
