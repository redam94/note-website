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
from ..services.model_provider import DEFAULTS, get_setting

router = APIRouter(prefix="/api")

ANTHROPIC_MODELS = [
    "claude-haiku-4-5",
    "claude-sonnet-4-6",
    "claude-opus-4-6",
]

OPENAI_MODELS = [
    "gpt-4o",
    "gpt-4o-mini",
    "o3-mini",
    "o4-mini",
    "gpt-5-nano",
    "gpt-5-mini"
]

GOOGLE_MODELS = [
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.0-flash",
]


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return "****" + key[-4:]


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
        anthropic_api_key=mask_key(await get_setting(db, "anthropic_api_key")),
        openai_api_key=mask_key(await get_setting(db, "openai_api_key")),
        google_api_key=mask_key(await get_setting(db, "google_api_key")),
        lmstudio_base_url=await get_setting(db, "lmstudio_base_url"),
        model_outline=await get_setting(db, "model_outline"),
        model_plan=await get_setting(db, "model_plan"),
        model_create=await get_setting(db, "model_create"),
        model_links=await get_setting(db, "model_links"),
        model_crosslink=await get_setting(db, "model_crosslink"),
        model_index=await get_setting(db, "model_index"),
        model_community=await get_setting(db, "model_community"),
        model_ask=await get_setting(db, "model_ask"),
    )


@router.put("/settings", dependencies=[Depends(require_admin)])
async def update_settings(
    body: SettingsUpdate,
    db: AsyncSession = Depends(get_db),
) -> SettingsResponse:
    # API keys — skip if the client sent back the masked placeholder
    if body.anthropic_api_key is not None and not body.anthropic_api_key.startswith("****"):
        await set_setting(db, "anthropic_api_key", body.anthropic_api_key)
    if body.openai_api_key is not None and not body.openai_api_key.startswith("****"):
        await set_setting(db, "openai_api_key", body.openai_api_key)
    if body.google_api_key is not None and not body.google_api_key.startswith("****"):
        await set_setting(db, "google_api_key", body.google_api_key)

    # Non-secret settings
    if body.lmstudio_base_url is not None:
        await set_setting(db, "lmstudio_base_url", body.lmstudio_base_url)

    # Per-task model assignments
    for key in (
        "model_outline", "model_plan", "model_create", "model_links",
        "model_crosslink", "model_index", "model_community", "model_ask",
    ):
        val = getattr(body, key)
        if val is not None:
            await set_setting(db, key, val)

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
            return TestConnectionResponse(success=False, message="No Anthropic API key configured")
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

    elif body.provider == "openai":
        api_key = await get_setting(db, "openai_api_key")
        if not api_key:
            return TestConnectionResponse(success=False, message="No OpenAI API key configured")
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)
            await client.chat.completions.create(
                model="gpt-4o-mini",
                max_tokens=10,
                messages=[{"role": "user", "content": "Say hi"}],
            )
            return TestConnectionResponse(success=True, message="Connected to OpenAI")
        except Exception as e:
            return TestConnectionResponse(success=False, message=str(e))

    elif body.provider == "google":
        api_key = await get_setting(db, "google_api_key")
        if not api_key:
            return TestConnectionResponse(success=False, message="No Google API key configured")
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=api_key,
            )
            await client.chat.completions.create(
                model="gemini-2.0-flash",
                max_tokens=10,
                messages=[{"role": "user", "content": "Say hi"}],
            )
            return TestConnectionResponse(success=True, message="Connected to Google AI")
        except Exception as e:
            return TestConnectionResponse(success=False, message=str(e))

    elif body.provider == "lmstudio":
        base_url = await get_setting(db, "lmstudio_base_url")
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{base_url}/models")
                resp.raise_for_status()
            return TestConnectionResponse(success=True, message="Connected to Custom / Local endpoint")
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

    return ModelsResponse(
        anthropic=ANTHROPIC_MODELS,
        openai=OPENAI_MODELS,
        google=GOOGLE_MODELS,
        lmstudio=lmstudio_models,
    )
