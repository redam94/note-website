"""Shared FastAPI dependencies."""

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .database import get_db
from .models.space import Space


async def get_current_space(
    space: str = Query("default"),
    db: AsyncSession = Depends(get_db),
) -> Space:
    """Resolve current space from ?space= query param. Defaults to 'default'."""
    result = await db.execute(select(Space).where(Space.slug == space))
    space_obj = result.scalar_one_or_none()
    if not space_obj:
        raise HTTPException(status_code=404, detail=f"Space '{space}' not found")
    return space_obj
