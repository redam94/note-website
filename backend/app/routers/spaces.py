"""Space (vault) management endpoints."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models.note import Note
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

    now = datetime.now(timezone.utc)
    space = Space(
        name=body.name,
        slug=slug,
        description=body.description,
        created_at=now.isoformat(),
    )
    db.add(space)
    await db.commit()
    await db.refresh(space)

    # Create default root index and Questions folder for the new space
    existing_slugs_result = await db.execute(select(Note.slug))
    existing_slugs = set(existing_slugs_result.scalars().all())

    for title, summary, content_body in [
        (
            "Index: Root",
            f"Root index for {body.name}",
            f"# {body.name}\n\n> This space is empty. Upload a document or run reindex to populate it.",
        ),
        (
            "Index: Questions",
            "Saved Q&A answers",
            "# Questions\n\n> Saved Q&A answers from the knowledge base.",
        ),
    ]:
        note_slug = slugify(title, lowercase=True) + f"-{slug}"
        counter = 1
        base = note_slug
        while note_slug in existing_slugs:
            note_slug = f"{base}-{counter}"
            counter += 1
        existing_slugs.add(note_slug)

        frontmatter = (
            f"---\n"
            f'title: "{title}"\n'
            f"tags:\n  - type/index\n"
            f"date_updated: {now.strftime('%Y-%m-%d')}\n"
            f"---"
        )

        root_note = Note(
            document_id=None,
            parent_id=None,
            title=title,
            content=f"{frontmatter}\n\n{content_body}",
            slug=note_slug,
            tags=json.dumps(["type/index"]),
            level=0,
            created_at=now.isoformat(),
            summary=summary,
            space_id=space.id,
        )
        db.add(root_note)

    # Commit both index notes, then parent Questions under Root
    await db.commit()

    root_result = await db.execute(
        select(Note).where(Note.title == "Index: Root").where(Note.space_id == space.id)
    )
    root_note = root_result.scalar_one_or_none()
    if root_note:
        q_result = await db.execute(
            select(Note).where(Note.title == "Index: Questions").where(Note.space_id == space.id)
        )
        q_note = q_result.scalar_one_or_none()
        if q_note:
            q_note.parent_id = root_note.id
            await db.commit()

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
