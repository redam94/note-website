import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.get("/graph")
async def get_graph(
    db: AsyncSession = Depends(get_db),
    include_clusters: bool = Query(False),
    current_space: Space = Depends(get_current_space),
) -> GraphData:
    graph = await build_full_graph(db, space_id=current_space.id)

    edge_list = [
        GraphEdgeData(
            source=e.source, target=e.target,
            relationship=e.relationship, confidence=e.confidence,
        )
        for e in graph.edges
    ]

    nodes: list[GraphNode] = []
    for n in graph.notes:
        tags_raw = n.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
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
) -> GraphData:
    """Return only the 1-hop neighborhood of a note (the note + its direct neighbors).

    Much cheaper than /graph — avoids scanning all note content; used by the
    per-note sidebar graph so pages don't have to load the entire knowledge base.
    """
    # Verify the note exists in this space
    note_res = await db.execute(
        select(Note).where(Note.id == note_id, Note.space_id == current_space.id)
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

    # 2. Fetch all notes in the neighborhood
    notes_res = await db.execute(
        select(Note).where(Note.id.in_(neighbor_ids), Note.space_id == current_space.id)
    )
    neighbor_notes = {n.id: n for n in notes_res.scalars().all()}

    # 3. Add parent-child edges (parent → note and note → children)
    all_neighbor_ids = set(neighbor_notes.keys())
    children_res = await db.execute(
        select(Note).where(Note.parent_id == note_id, Note.space_id == current_space.id)
    )
    for child in children_res.scalars().all():
        all_neighbor_ids.add(child.id)
        neighbor_notes[child.id] = child

    # Also include the note's own parent if present
    focus_note = neighbor_notes.get(note_id)
    if focus_note and focus_note.parent_id:
        parent_res = await db.execute(
            select(Note).where(Note.id == focus_note.parent_id, Note.space_id == current_space.id)
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
    for n in neighbor_notes.values():
        tags_raw = n.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        nodes.append(GraphNode(
            id=n.id,
            title=n.title,
            slug=n.slug,
            level=n.level,
            tags=tags,
            degree=degree_map.get(n.id, 0),
            documentId=n.document_id,
        ))

    return GraphData(nodes=nodes, edges=edge_list)
