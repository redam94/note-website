"""Space (vault) management endpoints."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models.space import Space
from ..schemas.space import SpaceCreate, SpaceResponse

router = APIRouter(prefix="/api/spaces")


def _to_response(s: Space) -> SpaceResponse:
    return SpaceResponse(
        id=s.id, name=s.name, slug=s.slug,
        description=s.description, createdAt=s.created_at,
    )


@router.get("")
async def list_spaces(db: AsyncSession = Depends(get_db)) -> list[SpaceResponse]:
    result = await db.execute(select(Space))
    return [_to_response(s) for s in result.scalars().all()]


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
async def create_space(
    body: SpaceCreate,
    db: AsyncSession = Depends(get_db),
) -> SpaceResponse:
    slug = slugify(body.name, lowercase=True)

    # Check for collision
    existing = await db.execute(select(Space).where(Space.slug == slug))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail=f"Space '{slug}' already exists")

    space = Space(
        name=body.name,
        slug=slug,
        description=body.description,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(space)
    await db.commit()
    await db.refresh(space)
    return _to_response(space)


@router.delete("/{slug}", dependencies=[Depends(require_admin)])
async def delete_space(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    if slug == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default space")

    result = await db.execute(select(Space).where(Space.slug == slug))
    space = result.scalar_one_or_none()
    if not space:
        raise HTTPException(status_code=404, detail="Space not found")

    await db.delete(space)
    await db.commit()
    return {"deleted": slug}
