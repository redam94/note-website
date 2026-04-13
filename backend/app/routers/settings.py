import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..database import get_db
from ..models.settings import Settings
from ..schemas.settings import (
    ModelsResponse,
    SettingsResponse,
    SettingsUpdate,
    TestConnectionRequest,
    TestConnectionResponse,
)

router = APIRouter(prefix="/api")

DEFAULTS = {
    "simple_model": "claude-haiku-4-5",
    "advanced_model": "claude-sonnet-4-6",
    "anthropic_api_key": "",
    "lmstudio_base_url": "http://localhost:1234/v1",
}

ANTHROPIC_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return "****" + key[-4:]


async def get_setting(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULTS.get(key, "")


async def set_setting(db: AsyncSession, key: str, value: str):
    result = await db.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(Settings(key=key, value=value))


@router.get("/settings", dependencies=[Depends(require_admin)])
async def get_settings(db: AsyncSession = Depends(get_db)) -> SettingsResponse:
    return SettingsResponse(
        simple_model=await get_setting(db, "simple_model"),
        advanced_model=await get_setting(db, "advanced_model"),
        anthropic_api_key=mask_key(await get_setting(db, "anthropic_api_key")),
        lmstudio_base_url=await get_setting(db, "lmstudio_base_url"),
    )


@router.put("/settings", dependencies=[Depends(require_admin)])
async def update_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    if body.simple_model is not None:
        await set_setting(db, "simple_model", body.simple_model)
    if body.advanced_model is not None:
        await set_setting(db, "advanced_model", body.advanced_model)
    if body.anthropic_api_key is not None and not body.anthropic_api_key.startswith("****"):
        await set_setting(db, "anthropic_api_key", body.anthropic_api_key)
    if body.lmstudio_base_url is not None:
        await set_setting(db, "lmstudio_base_url", body.lmstudio_base_url)

    await db.commit()
    return await get_settings(db)


@router.post("/settings/test")
async def test_connection(
    body: TestConnectionRequest,
    db: AsyncSession = Depends(get_db),
) -> TestConnectionResponse:
    if body.provider == "anthropic":
        api_key = await get_setting(db, "anthropic_api_key")
        if not api_key:
            return TestConnectionResponse(success=False, message="No API key configured")
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            await client.messages.create(
                model="claude-haiku-4-5",
                max_tokens=10,
                messages=[{"role": "user", "content": "Say hi"}],
            )
            return TestConnectionResponse(success=True, message="Connected to Anthropic")
        except Exception as e:
            return TestConnectionResponse(success=False, message=str(e))

    elif body.provider == "lmstudio":
        base_url = await get_setting(db, "lmstudio_base_url")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/models")
                resp.raise_for_status()
            return TestConnectionResponse(success=True, message="Connected to LMStudio")
        except Exception as e:
            return TestConnectionResponse(success=False, message=str(e))

    return TestConnectionResponse(success=False, message=f"Unknown provider: {body.provider}")


@router.get("/settings/models")
async def list_models(db: AsyncSession = Depends(get_db)) -> ModelsResponse:
    lmstudio_models: list[str] = []
    base_url = await get_setting(db, "lmstudio_base_url")
    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/models")
            resp.raise_for_status()
            data = resp.json()
            lmstudio_models = [m["id"] for m in data.get("data", [])]
    except Exception:
        pass

    return ModelsResponse(anthropic=ANTHROPIC_MODELS, lmstudio=lmstudio_models)
