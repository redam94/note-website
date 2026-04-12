from __future__ import annotations

from pydantic import BaseModel


class SearchResult(BaseModel):
    id: int
    title: str
    slug: str
    excerpt: str
    tags: list[str]
    matchType: str
    score: float


class SmartSearchResult(BaseModel):
    id: int
    title: str
    slug: str
    summary: str | None
    tags: list[str]
    relevance: str  # LLM-generated explanation of why this is relevant
    score: float
    source: str | None
    chapter: str | None
    page: int | None
    level: int


class SmartSearchResponse(BaseModel):
    query: str
    interpretation: str  # LLM's interpretation of what user is looking for
    results: list[SmartSearchResult]
    suggested_queries: list[str]  # follow-up queries the user might try
