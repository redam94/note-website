import json
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..schemas.graph import GraphData, GraphEdgeData, GraphNode

router = APIRouter(prefix="/api")


@router.get("/graph")
async def get_graph(db: AsyncSession = Depends(get_db)) -> GraphData:
    notes_result = await db.execute(select(Note))
    all_notes = notes_result.scalars().all()

    edges_result = await db.execute(select(GraphEdge))
    all_edges = edges_result.scalars().all()

    degree_map: dict[int, int] = defaultdict(int)
    for edge in all_edges:
        degree_map[edge.source_id] += 1
        degree_map[edge.target_id] += 1

    # Add parent-child edges
    parent_child_edges: list[GraphEdgeData] = []
    for note in all_notes:
        if note.parent_id:
            parent_child_edges.append(
                GraphEdgeData(
                    source=note.parent_id,
                    target=note.id,
                    relationship="part_of",
                    confidence=1.0,
                )
            )
            degree_map[note.parent_id] += 1
            degree_map[note.id] += 1

    nodes: list[GraphNode] = []
    for n in all_notes:
        tags_raw = n.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        nodes.append(
            GraphNode(
                id=n.id,
                title=n.title,
                slug=n.slug,
                level=n.level,
                tags=tags,
                degree=degree_map.get(n.id, 0),
                documentId=n.document_id,
            )
        )

    edge_data: list[GraphEdgeData] = [
        GraphEdgeData(
            source=e.source_id,
            target=e.target_id,
            relationship=e.relationship_type,
            confidence=e.confidence,
        )
        for e in all_edges
    ] + parent_child_edges

    return GraphData(nodes=nodes, edges=edge_data)
