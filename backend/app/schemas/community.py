from __future__ import annotations

from pydantic import BaseModel


class ClusterResponse(BaseModel):
    id: int
    label: str
    path: str | None = None
    level: int = 0
    summary: str | None
    member_count: int
    parent_id: int | None = None


class ClusterDetailResponse(BaseModel):
    id: int
    label: str
    summary: str | None
    member_node_ids: list[int]
    members: list[ClusterMemberNote]


class ClusterMemberNote(BaseModel):
    id: int
    title: str
    slug: str
    level: int


# Rebuild model to resolve forward ref
ClusterDetailResponse.model_rebuild()


class CommunityDetectionResponse(BaseModel):
    clusters: list[ClusterResponse]
    message: str


class IncrementalUpdateRequest(BaseModel):
    note_ids: list[int]


class IncrementalUpdateResponse(BaseModel):
    message: str
    details: list[dict]
