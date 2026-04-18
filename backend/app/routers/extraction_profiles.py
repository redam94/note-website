"""CRUD endpoints for ExtractionProfile plus a /test endpoint."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..dependencies import get_current_space
from ..extraction_presets import PRESETS, preset_by_key
from ..models.extraction_profile import ExtractionProfile
from ..models.space import Space

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/extraction-profiles", tags=["extraction-profiles"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class ProfileIn(BaseModel):
    name: str
    description: str = ""
    extensions: list[str] = []
    mime_types: list[str] = []
    doc_type_override: str | None = None
    script: str = ""
    prompt_additions: str = ""


class ProfileOut(BaseModel):
    id: int
    name: str
    description: str
    extensions: list[str]
    mime_types: list[str]
    doc_type_override: str | None
    script: str
    prompt_additions: str
    created_at: str
    space_id: int

    @classmethod
    def from_row(cls, row: ExtractionProfile) -> "ProfileOut":
        return cls(
            id=row.id,
            name=row.name,
            description=row.description or "",
            extensions=json.loads(row.extensions or "[]"),
            mime_types=json.loads(row.mime_types or "[]"),
            doc_type_override=row.doc_type_override,
            script=row.script or "",
            prompt_additions=row.prompt_additions or "",
            created_at=row.created_at,
            space_id=row.space_id,
        )


class TestRequest(BaseModel):
    script: str
    sample_text: str


class TestResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int
    success: bool


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_profile_or_404(row: ExtractionProfile | None) -> ExtractionProfile:
    if row is None:
        raise HTTPException(status_code=404, detail="Extraction profile not found")
    return row


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("", dependencies=[Depends(require_admin)])
async def list_profiles(
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[ProfileOut]:
    result = await db.execute(
        select(ExtractionProfile).where(ExtractionProfile.space_id == current_space.id)
    )
    return [ProfileOut.from_row(r) for r in result.scalars().all()]


@router.post("", status_code=201, dependencies=[Depends(require_admin)])
async def create_profile(
    body: ProfileIn,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> ProfileOut:
    now = datetime.now(timezone.utc).isoformat()
    profile = ExtractionProfile(
        name=body.name,
        description=body.description,
        extensions=json.dumps(body.extensions),
        mime_types=json.dumps(body.mime_types),
        doc_type_override=body.doc_type_override or None,
        script=body.script,
        prompt_additions=body.prompt_additions,
        created_at=now,
        space_id=current_space.id,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return ProfileOut.from_row(profile)


class PresetSummary(BaseModel):
    key: str
    name: str
    description: str
    extensions: list[str]
    doc_type_override: str | None
    installed: bool


class InstallPresetsRequest(BaseModel):
    keys: list[str]


@router.get("/presets", dependencies=[Depends(require_admin)])
async def list_presets(
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[PresetSummary]:
    """List available built-in presets, flagging which are already installed
    in the current space (matched by preset name)."""
    existing = await db.execute(
        select(ExtractionProfile.name).where(ExtractionProfile.space_id == current_space.id)
    )
    installed_names = {n for (n,) in existing.all()}
    return [
        PresetSummary(
            key=p["key"],
            name=p["name"],
            description=p["description"],
            extensions=p["extensions"],
            doc_type_override=p["doc_type_override"],
            installed=p["name"] in installed_names,
        )
        for p in PRESETS
    ]


@router.post("/presets/install", status_code=201, dependencies=[Depends(require_admin)])
async def install_presets(
    body: InstallPresetsRequest,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> list[ProfileOut]:
    """Install selected presets into the current space. Skips any whose name
    already exists (idempotent)."""
    if not body.keys:
        raise HTTPException(status_code=422, detail="No preset keys provided")

    existing_result = await db.execute(
        select(ExtractionProfile.name).where(ExtractionProfile.space_id == current_space.id)
    )
    existing_names = {n for (n,) in existing_result.all()}

    now = datetime.now(timezone.utc).isoformat()
    created: list[ExtractionProfile] = []
    for key in body.keys:
        preset = preset_by_key(key)
        if preset is None:
            raise HTTPException(status_code=404, detail=f"Unknown preset '{key}'")
        if preset["name"] in existing_names:
            continue
        profile = ExtractionProfile(
            name=preset["name"],
            description=preset["description"],
            extensions=json.dumps(preset["extensions"]),
            mime_types=json.dumps(preset["mime_types"]),
            doc_type_override=preset["doc_type_override"] or None,
            script=preset["script"],
            prompt_additions=preset["prompt_additions"],
            created_at=now,
            space_id=current_space.id,
        )
        db.add(profile)
        created.append(profile)
    await db.commit()
    for p in created:
        await db.refresh(p)
    return [ProfileOut.from_row(p) for p in created]


@router.get("/{profile_id}", dependencies=[Depends(require_admin)])
async def get_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> ProfileOut:
    result = await db.execute(
        select(ExtractionProfile).where(
            ExtractionProfile.id == profile_id,
            ExtractionProfile.space_id == current_space.id,
        )
    )
    profile = _get_profile_or_404(result.scalar_one_or_none())
    return ProfileOut.from_row(profile)


@router.patch("/{profile_id}", dependencies=[Depends(require_admin)])
async def update_profile(
    profile_id: int,
    body: ProfileIn,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> ProfileOut:
    result = await db.execute(
        select(ExtractionProfile).where(
            ExtractionProfile.id == profile_id,
            ExtractionProfile.space_id == current_space.id,
        )
    )
    profile = _get_profile_or_404(result.scalar_one_or_none())
    profile.name = body.name
    profile.description = body.description
    profile.extensions = json.dumps(body.extensions)
    profile.mime_types = json.dumps(body.mime_types)
    profile.doc_type_override = body.doc_type_override or None
    profile.script = body.script
    profile.prompt_additions = body.prompt_additions
    await db.commit()
    await db.refresh(profile)
    return ProfileOut.from_row(profile)


@router.delete("/{profile_id}", status_code=204, dependencies=[Depends(require_admin)])
async def delete_profile(
    profile_id: int,
    db: AsyncSession = Depends(get_db),
    current_space: Space = Depends(get_current_space),
) -> None:
    result = await db.execute(
        select(ExtractionProfile).where(
            ExtractionProfile.id == profile_id,
            ExtractionProfile.space_id == current_space.id,
        )
    )
    profile = _get_profile_or_404(result.scalar_one_or_none())
    await db.delete(profile)
    await db.commit()


@router.post("/test", dependencies=[Depends(require_admin)])
async def test_script(body: TestRequest) -> TestResponse:
    """Run the extraction script against sample text and return stdout/stderr.

    The script receives the path to a temp file containing sample_text as
    sys.argv[1] — same contract as the real pipeline.
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write(body.sample_text)
        tmp_path = f.name

    try:
        result = subprocess.run(
            [sys.executable, "-c", body.script, tmp_path],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return TestResponse(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            success=result.returncode == 0,
        )
    except subprocess.TimeoutExpired:
        return TestResponse(
            stdout="",
            stderr="Script timed out after 30 seconds",
            exit_code=-1,
            success=False,
        )
    except Exception as exc:
        return TestResponse(
            stdout="",
            stderr=str(exc),
            exit_code=-1,
            success=False,
        )
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
