"""Anthropic Claude through the official ``anthropic`` SDK.

Responses are returned as the SDK's own message objects (not converted), so every content block, including
thinking blocks, goes back to the API unchanged on the next turn of a tool loop. Current Claude models reject
``temperature`` and forced ``tool_choice``; neither is sent. Structured output uses ``output_config.format``.
Server-side refusal fallback (``fallbacks: "default"``) is on by default for the models that support it on the
Claude API; set ``ANTHROPIC_FALLBACKS=0`` if your account rejects it.
"""

from __future__ import annotations

from typing import Any

from harbor_llm.types import StructuredOutputError, T, parse_json_model, strict_schema

FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"})
DEFAULT_MODEL = "claude-opus-5-5"


class AnthropicLLM:
    provider = "ANTHROPIC"

    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        use_fallbacks: bool = True,
        client: Any | None = None,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as exc:  # pragma: no cover - depends on installed extras
                raise ImportError("provider ANTHROPIC needs: pip install 'harbor-llm[anthropic]'") from exc
            limits: dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
            client = (
                anthropic.AsyncAnthropic(api_key=api_key, **limits)
                if api_key
                else anthropic.AsyncAnthropic(**limits)
            )
        self.client = client
        self.model = model
        self.use_fallbacks = use_fallbacks and model in FALLBACK_MODELS

    def _common(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model}
        if self.use_fallbacks:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    async def create(self, **kwargs: Any) -> Any:
        kwargs.pop("temperature", None)  # rejected by current models
        return await self.client.beta.messages.create(**self._common(), **kwargs)

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T:
        response = await self.create(
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={
                "effort": effort,
                "format": {"type": "json_schema", "schema": strict_schema(schema)},
            },
        )
        if response.stop_reason == "refusal":
            raise StructuredOutputError("Model declined the request")
        if response.stop_reason == "max_tokens":
            raise StructuredOutputError("Structured output was truncated (max_tokens)")
        text = next((b.text for b in response.content if b.type == "text"), "")
        return parse_json_model(text, schema)
