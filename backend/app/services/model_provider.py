from collections.abc import AsyncGenerator
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import Settings

DEFAULTS = {
    "simple_model": "claude-haiku-4-5",
    "advanced_model": "claude-sonnet-4-6",
    "anthropic_api_key": "",
    "lmstudio_base_url": "http://localhost:1234/v1",
}


async def _get_setting(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULTS.get(key, "")


class ModelProvider:
    def __init__(
        self,
        simple_model: str,
        advanced_model: str,
        anthropic_api_key: str,
        lmstudio_base_url: str,
    ):
        self.simple_model = simple_model
        self.advanced_model = advanced_model
        self.anthropic_api_key = anthropic_api_key
        self.lmstudio_base_url = lmstudio_base_url

    def _get_model(self, tier: Literal["simple", "advanced"]) -> str:
        return self.simple_model if tier == "simple" else self.advanced_model

    def _is_anthropic(self, model: str) -> bool:
        return model.startswith("claude-")

    async def complete(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
        tier: Literal["simple", "advanced"] = "advanced",
    ) -> str:
        model = self._get_model(tier)
        if self._is_anthropic(model):
            return await self._anthropic_complete(model, messages, system, max_tokens)
        return await self._openai_complete(model, messages, system, max_tokens)

    async def stream(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
        tier: Literal["simple", "advanced"] = "advanced",
    ) -> AsyncGenerator[str, None]:
        model = self._get_model(tier)
        if self._is_anthropic(model):
            async for chunk in self._anthropic_stream(model, messages, system, max_tokens):
                yield chunk
        else:
            async for chunk in self._openai_stream(model, messages, system, max_tokens):
                yield chunk

    async def _anthropic_complete(
        self, model: str, messages: list[dict], system: str, max_tokens: int
    ) -> str:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        return response.content[0].text

    async def _anthropic_stream(
        self, model: str, messages: list[dict], system: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)
        async with client.messages.stream(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            async for text in stream.text_stream:
                yield text

    async def _openai_complete(
        self, model: str, messages: list[dict], system: str, max_tokens: int
    ) -> str:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self.lmstudio_base_url, api_key="not-needed")
        oai_messages = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        response = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=oai_messages,
        )
        return response.choices[0].message.content or ""

    async def _openai_stream(
        self, model: str, messages: list[dict], system: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(base_url=self.lmstudio_base_url, api_key="not-needed")
        oai_messages = [{"role": "system", "content": system}] + [
            {"role": m["role"], "content": m["content"]} for m in messages
        ]
        stream = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=oai_messages,
            stream=True,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


async def get_provider(db: AsyncSession) -> ModelProvider:
    return ModelProvider(
        simple_model=await _get_setting(db, "simple_model"),
        advanced_model=await _get_setting(db, "advanced_model"),
        anthropic_api_key=await _get_setting(db, "anthropic_api_key"),
        lmstudio_base_url=await _get_setting(db, "lmstudio_base_url"),
    )
