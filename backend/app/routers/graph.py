import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..models.subgraph_node import SubgraphNode
from ..models.subgraph_edge import SubgraphEdge
from ..schemas.graph import GraphData, GraphEdgeData, GraphNode
from ..services.graph_builder import build_full_graph

router = APIRouter(prefix="/api")


@router.get("/graph")
async def get_graph(
    db: AsyncSession = Depends(get_db),
    include_clusters: bool = Query(False),
) -> GraphData:
    graph = await build_full_graph(db)

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
        clusters_result = await db.execute(select(SubgraphNode))
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

        cluster_edges_result = await db.execute(select(SubgraphEdge))
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
