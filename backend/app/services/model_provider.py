from __future__ import annotations

from collections.abc import AsyncGenerator

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.settings import Settings

DEFAULTS = {
    "anthropic_api_key": "",
    "openai_api_key": "",
    "google_api_key": "",
    "lmstudio_base_url": "http://localhost:1234/v1",
    "model_outline":   "claude-haiku-4-5",
    "model_plan":      "claude-sonnet-4-6",
    "model_create":    "claude-haiku-4-5",
    "model_links":     "claude-haiku-4-5",
    "model_crosslink": "claude-haiku-4-5",
    "model_index":     "claude-haiku-4-5",
    "model_community": "claude-haiku-4-5",
    "model_ask":       "claude-sonnet-4-6",
}


async def get_setting(db: AsyncSession, key: str) -> str:
    result = await db.execute(select(Settings).where(Settings.key == key))
    row = result.scalar_one_or_none()
    return row.value if row else DEFAULTS.get(key, "")


class ModelProvider:
    def __init__(
        self,
        anthropic_api_key: str,
        openai_api_key: str,
        google_api_key: str,
        lmstudio_base_url: str,
    ):
        self.anthropic_api_key = anthropic_api_key
        self.openai_api_key = openai_api_key
        self.google_api_key = google_api_key
        self.lmstudio_base_url = lmstudio_base_url

    # ── Provider detection ────────────────────────────────────────────

    def _is_anthropic(self, model: str) -> bool:
        return model.startswith("claude-")

    def _is_openai_native(self, model: str) -> bool:
        return model.startswith("gpt-") or model.startswith(("o1", "o3", "o4-"))

    def _is_google(self, model: str) -> bool:
        return model.startswith("gemini-")

    def _resolve_openai_client(self, model: str):
        from openai import AsyncOpenAI

        if self._is_openai_native(model):
            return AsyncOpenAI(api_key=self.openai_api_key)
        if self._is_google(model):
            return AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                api_key=self.google_api_key,
            )
        # LMStudio / custom local endpoint
        return AsyncOpenAI(base_url=self.lmstudio_base_url, api_key="not-needed")

    # ── Public API ───────────────────────────────────────────────────

    async def complete(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
        model: str,
        json_mode: bool = False,
    ) -> str:
        if self._is_anthropic(model):
            return await self._anthropic_complete(model, messages, system, max_tokens)
        return await self._openai_complete(model, messages, system, max_tokens, json_mode=json_mode)

    async def stream(
        self,
        messages: list[dict],
        system: str,
        max_tokens: int,
        model: str,
    ) -> AsyncGenerator[str, None]:
        if self._is_anthropic(model):
            async for chunk in self._anthropic_stream(model, messages, system, max_tokens):
                yield chunk
        else:
            async for chunk in self._openai_stream(model, messages, system, max_tokens):
                yield chunk

    async def complete_structured(
        self,
        schema: type[BaseModel],
        messages: list[dict],
        system: str,
        max_tokens: int,
        model: str,
    ) -> BaseModel:
        """Return a validated Pydantic instance using provider-native structured output."""
        if self._is_anthropic(model):
            return await self._anthropic_structured(model, schema, messages, system, max_tokens)
        return await self._openai_structured(model, schema, messages, system, max_tokens)

    # ── Anthropic ────────────────────────────────────────────────────

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

    async def _anthropic_structured(
        self,
        model: str,
        schema: type[BaseModel],
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> BaseModel:
        import anthropic

        raw_schema = schema.model_json_schema()
        raw_schema.pop("title", None)
        tool_name = schema.__name__

        client = anthropic.AsyncAnthropic(api_key=self.anthropic_api_key)
        response = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=[{
                "name": tool_name,
                "description": f"Structured output matching {tool_name} schema",
                "input_schema": raw_schema,
            }],
            tool_choice={"type": "tool", "name": tool_name},
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == tool_name:
                return schema.model_validate(block.input)
        raise ValueError(f"No tool_use block in Anthropic response for {tool_name}")

    async def _openai_structured(
        self,
        model: str,
        schema: type[BaseModel],
        messages: list[dict],
        system: str,
        max_tokens: int,
    ) -> BaseModel:
        """Use json_object mode + Pydantic validation for OpenAI / Google / LMStudio."""
        import json
        import re

        # Pass max_tokens=None so the model is never cut off mid-JSON.
        # Truncated JSON is always invalid; it's better to let the model finish.
        content = await self._openai_complete(model, messages, system, None, json_mode=True)

        if not content.strip():
            raise ValueError("Model returned empty content")

        # Strip markdown fences if the model wrapped the JSON
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)

        try:
            return schema.model_validate_json(stripped)
        except Exception:
            # Last resort: find the first {...} block in the response
            m = re.search(r"\{.*\}", stripped, re.DOTALL)
            if m:
                return schema.model_validate_json(m.group())
            raise ValueError(f"Could not parse JSON from model response ({len(content)} chars)")

    # ── OpenAI-compatible (OpenAI, Google, LMStudio) ─────────────────

    def _is_reasoning_model(self, model: str) -> bool:
        """o1/o3/o4 reasoning models use max_completion_tokens and no system role."""
        return model.startswith(("o1", "o3", "o4-"))

    async def _openai_complete(
        self, model: str, messages: list[dict], system: str, max_tokens: int | None, json_mode: bool = False
    ) -> str:
        client = self._resolve_openai_client(model)
        is_reasoning = self._is_reasoning_model(model)
        wants_json = json_mode and not is_reasoning and (self._is_openai_native(model) or self._is_google(model))

        # OpenAI requires the word "json" in the messages when using json_object mode
        effective_system = (system + "\nRespond with valid JSON.") if wants_json else system

        if is_reasoning:
            # Reasoning models don't support a system role — prepend system as a user turn
            oai_messages = [{"role": "user", "content": f"[Instructions]\n{effective_system}\n\n[Task]\n{messages[0]['content']}"}]
            oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages[1:]]
        else:
            oai_messages = [{"role": "system", "content": effective_system}] + [
                {"role": m["role"], "content": m["content"]} for m in messages
            ]

        kwargs: dict = {}
        if not is_reasoning and max_tokens is not None:
            # OpenAI native uses max_completion_tokens (max_tokens was deprecated).
            # Google and LMStudio OpenAI-compat endpoints still use max_tokens.
            if self._is_openai_native(model):
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
        if wants_json:
            kwargs["response_format"] = {"type": "json_object"}

        response = await client.chat.completions.create(
            model=model,
            messages=oai_messages,
            **kwargs,
        )
        choice = response.choices[0]
        content = choice.message.content or ""
        if not content.strip():
            finish = choice.finish_reason or "unknown"
            raise ValueError(f"Model returned empty content (finish_reason={finish})")
        return content

    async def _openai_stream(
        self, model: str, messages: list[dict], system: str, max_tokens: int
    ) -> AsyncGenerator[str, None]:
        client = self._resolve_openai_client(model)
        is_reasoning = self._is_reasoning_model(model)

        if is_reasoning:
            oai_messages = [{"role": "user", "content": f"[Instructions]\n{system}\n\n[Task]\n{messages[0]['content']}"}]
            oai_messages += [{"role": m["role"], "content": m["content"]} for m in messages[1:]]
            token_kwargs: dict = {}
        else:
            oai_messages = [{"role": "system", "content": system}] + [
                {"role": m["role"], "content": m["content"]} for m in messages
            ]
            if self._is_openai_native(model):
                token_kwargs = {"max_completion_tokens": max_tokens}
            else:
                token_kwargs = {"max_tokens": max_tokens}

        stream = await client.chat.completions.create(
            model=model,
            messages=oai_messages,
            stream=True,
            **token_kwargs,
        )
        async for chunk in stream:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


async def get_provider(db: AsyncSession) -> ModelProvider:
    return ModelProvider(
        anthropic_api_key=await get_setting(db, "anthropic_api_key"),
        openai_api_key=await get_setting(db, "openai_api_key"),
        google_api_key=await get_setting(db, "google_api_key"),
        lmstudio_base_url=await get_setting(db, "lmstudio_base_url"),
    )
