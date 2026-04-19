"""API endpoints exposing graph traversal and search tools.

These endpoints are designed to be called by agents/models to navigate
the knowledge graph efficiently.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthUser, get_current_user
from ..database import get_db
from ..services.graph_search import (
    bfs_traverse,
    find_by_tag,
    find_path,
    find_related,
    get_neighbors,
    get_note_context,
    grep_notes,
    list_all_tags,
    regex_search,
)

router = APIRouter(prefix="/api/tools")


@router.get("/neighbors/{note_id}")
async def api_get_neighbors(
    note_id: int,
    direction: str = Query(default="both", pattern="^(out|in|both)$"),
    relationship: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Get immediate neighbors of a note. Direction: out, in, or both."""
    return await get_neighbors(db, note_id, direction=direction, relationship=relationship, is_admin=user.is_admin)


@router.get("/traverse/{note_id}")
async def api_bfs_traverse(
    note_id: int,
    depth: int = Query(default=2, ge=1, le=5),
    max_nodes: int = Query(default=50, ge=1, le=200),
    relationship: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """BFS traversal from a note up to `depth` hops."""
    return await bfs_traverse(db, note_id, max_depth=depth, max_nodes=max_nodes, relationship=relationship, is_admin=user.is_admin)


@router.get("/path")
async def api_find_path(
    from_id: int = Query(...),
    to_id: int = Query(...),
    max_depth: int = Query(default=6, ge=1, le=10),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Find shortest path between two notes."""
    return await find_path(db, from_id, to_id, max_depth=max_depth, is_admin=user.is_admin)


@router.get("/context/{note_id}")
async def api_get_context(
    note_id: int,
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Get full context around a note: parents, children, siblings, connected notes."""
    return await get_note_context(db, note_id, is_admin=user.is_admin)


@router.get("/by-tag")
async def api_find_by_tag(
    tag: str = Query(...),
    limit: int = Query(default=30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Find notes by tag."""
    return await find_by_tag(db, tag, limit=limit, is_admin=user.is_admin)


@router.get("/grep")
async def api_grep(
    q: str = Query(...),
    case_sensitive: bool = Query(default=False),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Search note content and titles for a text pattern."""
    return await grep_notes(db, q, case_sensitive=case_sensitive, limit=limit, is_admin=user.is_admin)


@router.get("/regex")
async def api_regex_search(
    pattern: str = Query(...),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Search note content using a regex pattern."""
    return await regex_search(db, pattern, limit=limit, is_admin=user.is_admin)


@router.get("/tags")
async def api_list_tags(
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """List all unique tags with usage counts."""
    return await list_all_tags(db, is_admin=user.is_admin)


@router.get("/related/{note_id}")
async def api_find_related(
    note_id: int,
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user: AuthUser = Depends(get_current_user),
):
    """Find notes related to a given note by shared tags and graph proximity."""
    return await find_related(db, note_id, limit=limit, is_admin=user.is_admin)
