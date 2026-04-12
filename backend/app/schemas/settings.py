from __future__ import annotations

from pydantic import BaseModel


class SettingsResponse(BaseModel):
    simple_model: str
    advanced_model: str
    anthropic_api_key: str  # Masked
    lmstudio_base_url: str


class SettingsUpdate(BaseModel):
    simple_model: str | None = None
    advanced_model: str | None = None
    anthropic_api_key: str | None = None
    lmstudio_base_url: str | None = None


class TestConnectionRequest(BaseModel):
    provider: str  # "anthropic" or "lmstudio"


class TestConnectionResponse(BaseModel):
    success: bool
    message: str


class ModelsResponse(BaseModel):
    anthropic: list[str]
    lmstudio: list[str]
