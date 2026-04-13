"""Pipeline node: ensure the topic folder tree exists as index notes in the DB.

Runs between plan and create_notes. For every unique folder path in the note
plan, creates (or finds) an index note with proper parent_id linkage. Returns
a folder_id_map so create_notes can set parent_id on content notes.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from slugify import slugify
from sqlalchemy import select

from ...database import async_session
from ...models.note import Note
from ..progress import set_step
from ..state import ProcessingState


async def ensure_tree(state: ProcessingState) -> ProcessingState:
    """Materialize folder paths as index notes with parent_id chains."""
    note_plan = state.get("note_plan", [])
    await set_step(state["document_id"], "Building topic tree...")

    if not note_plan:
        return {**state, "folder_id_map": {}}

    # Collect all unique folder paths from the plan
    all_folders: set[str] = set()
    for entry in note_plan:
        folder = entry.get("folder", "").strip()
        if folder:
            # Also ensure all ancestor paths exist
            parts = folder.split("/")
            for i in range(len(parts)):
                all_folders.add("/".join(parts[: i + 1]))

    if not all_folders:
        return {**state, "folder_id_map": {}}

    # Query existing index notes
    async with async_session() as db:
        result = await db.execute(
            select(Note).where(Note.title.like("Index: %")).where(Note.space_id == state["space_id"])
        )
        existing_indexes = result.scalars().all()

        slugs_result = await db.execute(select(Note.slug))
        existing_slugs = set(slugs_result.scalars().all())

    # Build path -> note_id lookup from existing indexes
    path_to_id: dict[str, int] = {}
    for n in existing_indexes:
        path = n.title.removeprefix("Index: ").strip()
        if path:
            path_to_id[path] = n.id

    # Sort folders by depth (shortest first) so parents are created before children
    sorted_folders = sorted(all_folders, key=lambda p: (p.count("/"), p))

    now = datetime.now(timezone.utc).isoformat()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for folder_path in sorted_folders:
        if folder_path in path_to_id:
            continue  # Already exists

        # Determine parent
        parts = folder_path.split("/")
        parent_path = "/".join(parts[:-1]) if len(parts) > 1 else None
        parent_id = path_to_id.get(parent_path) if parent_path else None

        leaf_name = parts[-1]
        index_title = f"Index: {folder_path}"

        # Build placeholder content
        frontmatter = (
            f"---\n"
            f'title: "{index_title}"\n'
            f"tags:\n"
            f"  - type/index\n"
            f"date_updated: {today}\n"
            f"---"
        )
        content = f"{frontmatter}\n\n# {leaf_name}\n\n> This index will be updated with routing information."

        slug = slugify(index_title, lowercase=True)
        counter = 1
        base_slug = slug
        while slug in existing_slugs:
            slug = f"{base_slug}-{counter}"
            counter += 1
        existing_slugs.add(slug)

        index_note = Note(
            document_id=None,  # Global, not tied to a document
            parent_id=parent_id,
            title=index_title,
            content=content,
            slug=slug,
            tags=json.dumps(["type/index"]),
            level=0,
            created_at=now,
            summary=f"Topic index for {folder_path}",
            space_id=state["space_id"],
        )

        async with async_session() as db:
            db.add(index_note)
            await db.commit()
            await db.refresh(index_note)

        path_to_id[folder_path] = index_note.id

    # Build the final folder_id_map (all paths, including pre-existing)
    folder_id_map = {path: path_to_id[path] for path in all_folders if path in path_to_id}

    return {**state, "folder_id_map": folder_id_map}
