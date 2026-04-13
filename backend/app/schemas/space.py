from __future__ import annotations

from pydantic import BaseModel


class SpaceCreate(BaseModel):
    name: str
    description: str | None = None


class SpaceResponse(BaseModel):
    id: int
    name: str
    slug: str
    description: str | None
    createdAt: str
