"""Community detection and cluster management endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models.note import Note
from ..models.subgraph_node import SubgraphNode
from ..schemas.community import (
    ClusterDetailResponse,
    ClusterMemberNote,
    ClusterResponse,
    CommunityDetectionResponse,
    IncrementalUpdateRequest,
    IncrementalUpdateResponse,
)
from ..services.community import incremental_update, run_community_detection

router = APIRouter(prefix="/api/community")


@router.post("/detect", dependencies=[Depends(require_admin)])
async def detect_communities_endpoint(
    db: AsyncSession = Depends(get_db),
) -> CommunityDetectionResponse:
    """Trigger full community detection. Clears and rebuilds all clusters."""
    clusters = await run_community_detection(db)
    return CommunityDetectionResponse(
        clusters=[
            ClusterResponse(
                id=c["id"],
                label=c["label"],
                path=c.get("path"),
                level=c.get("level", 0),
                summary=c.get("summary"),
                member_count=c["member_count"],
                parent_id=c.get("parent_id"),
            )
            for c in clusters
        ],
        message=f"Detected {len(clusters)} communities",
    )


@router.get("/clusters")
async def list_clusters(
    db: AsyncSession = Depends(get_db),
) -> list[ClusterResponse]:
    """List all subgraph clusters."""
    result = await db.execute(select(SubgraphNode))
    clusters = result.scalars().all()
    return [
        ClusterResponse(
            id=c.id,
            label=c.label,
            path=c.path,
            level=c.level,
            summary=c.summary,
            parent_id=c.parent_cluster_id,
            member_count=len(json.loads(c.member_node_ids)) if c.member_node_ids else 0,
        )
        for c in clusters
    ]


@router.get("/clusters/{cluster_id}")
async def get_cluster(
    cluster_id: int,
    db: AsyncSession = Depends(get_db),
) -> ClusterDetailResponse:
    """Get a single cluster with its member notes."""
    result = await db.execute(
        select(SubgraphNode).where(SubgraphNode.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Cluster not found")

    member_ids = json.loads(cluster.member_node_ids) if cluster.member_node_ids else []
    members_result = await db.execute(
        select(Note).where(Note.id.in_(member_ids))
    )
    members = [
        ClusterMemberNote(
            id=n.id, title=n.title, slug=n.slug, level=n.level,
        )
        for n in members_result.scalars().all()
    ]

    return ClusterDetailResponse(
        id=cluster.id,
        label=cluster.label,
        summary=cluster.summary,
        member_node_ids=member_ids,
        members=members,
    )


@router.post("/incremental", dependencies=[Depends(require_admin)])
async def incremental_update_endpoint(
    body: IncrementalUpdateRequest,
    db: AsyncSession = Depends(get_db),
) -> IncrementalUpdateResponse:
    """Incrementally update clusters for specified note IDs."""
    results = await incremental_update(db, body.note_ids)
    return IncrementalUpdateResponse(
        message=f"Processed {len(body.note_ids)} notes",
        details=results,
    )
