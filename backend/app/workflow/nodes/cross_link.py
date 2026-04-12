import json
import re
from datetime import datetime, timezone

from sqlalchemy import and_, select

from ...database import async_session
from ...models.graph_edge import GraphEdge
from ...models.note import Note
from ...prompts import load_prompt
from ...services.model_provider import get_provider
from ..progress import set_step
from ..state import ProcessingState

CANDIDATE_SYSTEM = load_prompt("cross_link_candidates").format()
CLASSIFY_SYSTEM = load_prompt("cross_link_classify").format()

_CANDIDATE_SYSTEM_LEGACY = (
    "You are a knowledge graph analyst. Given a list of note titles and summaries,\n"
    "identify pairs that are likely semantically related.\n"
    "Return ONLY valid JSON array of pairs:\n"
    '[{"note_a_id": 1, "note_b_id": 2}]\n'
    "Only include pairs that have a meaningful relationship. Be selective."
)

_CLASSIFY_SYSTEM_LEGACY = (
    "You are a knowledge graph analyst. Given two notes, classify their relationship.\n"
    "Relationship types: supports, contradicts, defines, example_of, part_of, references\n"
    "Return ONLY valid JSON:\n"
    '{"relationship": "...", "confidence": 0.0-1.0}\n'
    "If not meaningfully related, return: {\"relationship\": null}"
)


async def detect_cross_links(state: ProcessingState) -> ProcessingState:
    """Detect cross-links between new and existing notes."""
    created_notes = state["created_notes"]
    await set_step(state["document_id"], "Detecting cross-links between notes...")

    if not created_notes:
        return {**state, "cross_links": []}

    new_ids = {n["id"] for n in created_notes}

    async with async_session() as db:
        provider = await get_provider(db)

        # Get all notes
        result = await db.execute(select(Note))
        all_notes = result.scalars().all()

    existing_notes = [n for n in all_notes if n.id not in new_ids]
    new_notes = [n for n in all_notes if n.id in new_ids]

    if not existing_notes:
        # Only detect links among new notes
        candidates = []
        for i, a in enumerate(new_notes):
            for b in new_notes[i + 1 :]:
                # Skip parent-child pairs
                if a.parent_id == b.id or b.parent_id == a.id:
                    continue
                candidates.append((a, b))
    else:
        # Use simple model to find candidates between new and existing
        note_summaries = []
        for n in new_notes + existing_notes[:50]:
            summary = getattr(n, "summary", None) or n.content[:150]
            note_summaries.append(f"ID {n.id}: {n.title} - {summary}")

        prompt = (
            "Notes:\n" + "\n".join(note_summaries) + "\n\n"
            "Identify pairs of notes that are semantically related. "
            "Focus on connections between new notes (IDs: "
            + ", ".join(str(i) for i in new_ids)
            + ") and existing notes."
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=CANDIDATE_SYSTEM,
                max_tokens=4096,
                tier="simple",
            )
            json_match = re.search(r"\[.*\]", response, re.DOTALL)
            if json_match:
                candidate_pairs = json.loads(json_match.group())
            else:
                candidate_pairs = json.loads(response)
        except (json.JSONDecodeError, Exception):
            candidate_pairs = []

        note_map = {n.id: n for n in all_notes}
        candidates = []
        for pair in candidate_pairs:
            a_id = pair.get("note_a_id")
            b_id = pair.get("note_b_id")
            if a_id in note_map and b_id in note_map:
                candidates.append((note_map[a_id], note_map[b_id]))

    # Classify each candidate pair
    cross_links = []
    for note_a, note_b in candidates[:30]:  # Limit to 30 pairs
        prompt = (
            f'Note A: "{note_a.title}"\n{note_a.content[:500]}\n\n'
            f'Note B: "{note_b.title}"\n{note_b.content[:500]}'
        )

        try:
            response = await provider.complete(
                messages=[{"role": "user", "content": prompt}],
                system=CLASSIFY_SYSTEM,
                max_tokens=256,
                tier="simple",
            )
            result = json.loads(response)
            if result.get("relationship"):
                cross_links.append(
                    {
                        "source_id": note_a.id,
                        "target_id": note_b.id,
                        "relationship": result["relationship"],
                        "confidence": result.get("confidence", 0.5),
                    }
                )
        except (json.JSONDecodeError, Exception):
            continue

    # Insert edges into DB
    now = datetime.now(timezone.utc).isoformat()
    async with async_session() as db:
        for link in cross_links:
            # Check for duplicates
            existing = await db.execute(
                select(GraphEdge).where(
                    and_(
                        GraphEdge.source_id == link["source_id"],
                        GraphEdge.target_id == link["target_id"],
                    )
                )
            )
            if existing.scalar_one_or_none():
                continue

            edge = GraphEdge(
                source_id=link["source_id"],
                target_id=link["target_id"],
                relationship_type=link["relationship"],
                confidence=link["confidence"],
                created_by="llm",
                created_at=now,
            )
            db.add(edge)
        await db.commit()

    return {**state, "cross_links": cross_links}
