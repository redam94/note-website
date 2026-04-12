from __future__ import annotations

import json

from pydantic import BaseModel


class LinkInfo(BaseModel):
    id: int
    title: str
    slug: str
    relationship: str


class NoteWithLinks(BaseModel):
    id: int
    title: str
    content: str
    slug: str
    tags: list[str]
    level: int
    documentId: int | None
    createdAt: str
    source: str | None = None
    chapter: str | None = None
    page: int | None = None
    summary: str | None = None
    backlinks: list[LinkInfo]
    outlinks: list[LinkInfo]

    @classmethod
    def from_row(cls, row, backlinks: list[LinkInfo], outlinks: list[LinkInfo]) -> "NoteWithLinks":
        tags_raw = row.tags or "[]"
        tags = json.loads(tags_raw) if isinstance(tags_raw, str) else tags_raw
        return cls(
            id=row.id,
            title=row.title,
            content=row.content,
            slug=row.slug,
            tags=tags,
            level=row.level,
            documentId=row.document_id,
            createdAt=row.created_at,
            source=getattr(row, "source", None),
            chapter=getattr(row, "chapter", None),
            page=getattr(row, "page", None),
            summary=getattr(row, "summary", None),
            backlinks=backlinks,
            outlinks=outlinks,
        )
