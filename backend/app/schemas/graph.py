from __future__ import annotations

import json

from pydantic import BaseModel


class GraphNode(BaseModel):
    id: int
    title: str
    slug: str
    level: int
    tags: list[str]
    degree: int
    documentId: int | None
    nodeType: str = "note"
    summary: str | None = None
    clusterLabel: str | None = None


class GraphEdgeData(BaseModel):
    source: int
    target: int
    relationship: str
    confidence: float


class GraphData(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdgeData]


def note_to_graph_node(row, degree: int) -> GraphNode:
    tags_raw = row.tags or "[]"
    tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
    return GraphNode(
        id=row.id,
        title=row.title,
        slug=row.slug,
        level=row.level,
        tags=tags,
        degree=degree,
        documentId=row.document_id,
    )
