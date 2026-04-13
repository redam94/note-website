"""Pipeline node: update community clusters after cross-linking."""

from ...database import async_session
from ...services.community import incremental_update
from ..progress import set_step
from ..state import ProcessingState


async def update_communities(state: ProcessingState) -> ProcessingState:
    """Incrementally update clusters for newly created notes."""
    created_notes = state.get("created_notes", [])
    if not created_notes:
        return {**state, "community_updates": []}

    await set_step(state["document_id"], "Updating topic clusters...")

    new_ids = [n["id"] for n in created_notes if "id" in n]

    async with async_session() as db:
        results = await incremental_update(db, new_ids, space_id=state["space_id"])

    return {**state, "community_updates": results}
