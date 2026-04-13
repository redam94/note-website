from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..dependencies import get_current_space
from ..models.graph_edge import GraphEdge
from ..models.note import Note
from ..models.space import Space
from ..prompts import load_prompt
from ..schemas.ask import LinkRequest
from ..services.model_provider import get_provider

router = APIRouter(prefix="/api")

LINK_SYSTEM_PROMPT = load_prompt("link_detect").format()

_LINK_SYSTEM_PROMPT_LEGACY = (
    "You are a knowledge graph analyst. Given two notes, determine if they are semantically related.\n"
    "If related, classify the relationship as one of: supports, contradicts, defines, example_of, part_of, references.\n"
    "Return JSON: {\"related\": true, \"relationship\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"...\"}\n"
    "If not related, return: {\"related\": false}\n"
    "Return ONLY valid JSON, no other text."
)


async def detect_link(provider, note_a, note_b) -> dict | None:
    prompt = (
        f'Note A: "{note_a.title}"\n{note_a.content}\n\n'
        f'Note B: "{note_b.title}"\n{note_b.content}'
    )
    try:
        response = await provider.complete(
            messages=[{"role": "user", "content": prompt}],
            system=LINK_SYSTEM_PROMPT,
            max_tokens=512,
            tier="simple",
        )
        result = json.loads(response)
        if result.get("related"):
            return result
    except (json.JSONDecodeError, KeyError):
        pass
    return None


@router.post("/link", dependencies=[Depends(require_admin)])
async def detect_links(
    body: LinkRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
):
    result = await db.execute(select(Note).where(Note.id == body.noteId).where(Note.space_id == current_space.id))
    note = result.scalar_one_or_none()
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")

    all_result = await db.execute(select(Note).where(Note.id != body.noteId).where(Note.space_id == current_space.id))
    other_notes = all_result.scalars().all()

    provider = await get_provider(db)
    links = []
    inserted = []

    # Process in batches of 5
    for i in range(0, len(other_notes), 5):
        batch = other_notes[i : i + 5]
        for other in batch:
            link_result = await detect_link(provider, note, other)
            if link_result:
                links.append(
                    {
                        "sourceId": note.id,
                        "targetId": other.id,
                        "relationship": link_result["relationship"],
                        "confidence": link_result["confidence"],
                    }
                )

    now = datetime.now(timezone.utc).isoformat()
    for link in links:
        # Check if edge already exists
        existing = await db.execute(
            select(GraphEdge).where(
                and_(
                    GraphEdge.source_id == link["sourceId"],
                    GraphEdge.target_id == link["targetId"],
                )
            )
        )
        if existing.scalar_one_or_none():
            continue

        edge = GraphEdge(
            source_id=link["sourceId"],
            target_id=link["targetId"],
            relationship_type=link["relationship"],
            confidence=link["confidence"],
            created_by="llm",
            created_at=now,
        )
        db.add(edge)
        inserted.append(edge)

    await db.commit()

    return {
        "message": f"Found {len(links)} links, inserted {len(inserted)} new edges",
        "edges": [
            {
                "sourceId": e.source_id,
                "targetId": e.target_id,
                "relationshipType": e.relationship_type,
                "confidence": e.confidence,
                "createdBy": e.created_by,
                "createdAt": e.created_at,
            }
            for e in inserted
        ],
    }
