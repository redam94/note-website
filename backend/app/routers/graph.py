import json
import re
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

    # Build title -> id map for wiki-link resolution
    title_to_id: dict[str, int] = {}
    for n in all_notes:
        title_to_id[n.title] = n.id

    # Collect all edges: DB edges + parent-child + wiki-links
    edge_set: set[tuple[int, int, str]] = set()  # (source, target, relationship)
    edge_list: list[GraphEdgeData] = []
    degree_map: dict[int, int] = defaultdict(int)

    def add_edge(source: int, target: int, relationship: str, confidence: float = 0.8):
        key = (source, target, relationship)
        if key not in edge_set and source != target:
            edge_set.add(key)
            edge_list.append(GraphEdgeData(
                source=source, target=target,
                relationship=relationship, confidence=confidence,
            ))
            degree_map[source] += 1
            degree_map[target] += 1

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
        # Find all [[Target]] and [[Target|Display]] wiki-links
        wiki_targets = re.findall(r"\[\[([^\]|]+?)(?:\|[^\]]+?)?\]\]", content)
        for target_title in wiki_targets:
            # Skip raw file links
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

        # Extract depends_on links
        dep_section = re.search(r"depends_on:(.*?)(?=\n\w|\Z)", fm, re.DOTALL)
        if dep_section:
            dep_titles = re.findall(r"\[\[([^\]]+)\]\]", dep_section.group(1))
            for dep_title in dep_titles:
                dep_id = title_to_id.get(dep_title)
                if dep_id:
                    add_edge(note.id, dep_id, "depends_on", 0.9)

        # Extract used_by links
        used_section = re.search(r"used_by:(.*?)(?=\n\w|\Z)", fm, re.DOTALL)
        if used_section:
            used_titles = re.findall(r"\[\[([^\]]+)\]\]", used_section.group(1))
            for used_title in used_titles:
                used_id = title_to_id.get(used_title)
                if used_id:
                    add_edge(used_id, note.id, "depends_on", 0.9)

    # Build nodes
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

    return GraphData(nodes=nodes, edges=edge_list)
