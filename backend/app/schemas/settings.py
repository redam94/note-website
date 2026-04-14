from __future__ import annotations

from pydantic import BaseModel


class SettingsResponse(BaseModel):
    anthropic_api_key: str  # Masked
    openai_api_key: str     # Masked
    google_api_key: str     # Masked
    lmstudio_base_url: str
    model_outline: str
    model_plan: str
    model_create: str
    model_links: str
    model_crosslink: str
    model_index: str
    model_community: str
    model_ask: str


class SettingsUpdate(BaseModel):
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    google_api_key: str | None = None
    lmstudio_base_url: str | None = None
    model_outline: str | None = None
    model_plan: str | None = None
    model_create: str | None = None
    model_links: str | None = None
    model_crosslink: str | None = None
    model_index: str | None = None
    model_community: str | None = None
    model_ask: str | None = None


class TestConnectionRequest(BaseModel):
    provider: str  # "anthropic", "openai", "google", or "lmstudio"


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


class ModelsResponse(BaseModel):
    anthropic: list[str]
    openai: list[str]
    google: list[str]
    lmstudio: list[str]
