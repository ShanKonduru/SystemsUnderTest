"""Google Gemini through the ``google-genai`` SDK, on the Gemini API (API key) or Vertex AI (GCP project).

Vertex AI is used when ``GOOGLE_GENAI_USE_VERTEXAI`` is true, or when a GCP project is set and no API key is;
credentials then come from Application Default Credentials. Tool calls map to function declarations (JSON
Schema parameters); structured output uses a JSON response schema.
"""

from __future__ import annotations

import itertools
from typing import Any

from harbor_llm.providers.normalize import Turn, normalize, tool_names
from harbor_llm.types import (
    LLMResponse,
    StructuredOutputError,
    T,
    TextBlock,
    ToolUseBlock,
    Usage,
    get,
    parse_json_model,
    strict_schema,
)

STOP = {
    "STOP": "end_turn",
    "MAX_TOKENS": "max_tokens",
    "SAFETY": "refusal",
    "RECITATION": "refusal",
    "BLOCKLIST": "refusal",
    "PROHIBITED_CONTENT": "refusal",
    "SPII": "refusal",
}


def to_contents(turns: list[Turn]) -> list[dict[str, Any]]:
    names = tool_names(turns)
    out: list[dict[str, Any]] = []
    for turn in turns:
        parts: list[dict[str, Any]] = []
        for p in turn.parts:
            if p.kind == "text":
                parts.append({"text": p.text})
            elif p.kind == "tool_use":
                parts.append({"function_call": {"name": p.name, "args": p.input}})
            else:
                key = "error" if p.is_error else "result"
                parts.append(
                    {"function_response": {"name": names.get(p.id, p.id), "response": {key: p.text}}}
                )
        out.append({"role": "model" if turn.role == "assistant" else "user", "parts": parts})
    return out


def to_function_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "function_declarations": [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters_json_schema": t["input_schema"],
                }
                for t in tools
            ]
        }
    ]


def _enum_name(value: Any) -> str:
    return str(getattr(value, "name", value) or "STOP")


def from_gemini(resp: Any, model: str, ids: Any) -> LLMResponse:
    blocks: list[Any] = []
    candidates = get(resp, "candidates") or []
    finish = "STOP"
    if candidates:
        cand = candidates[0]
        finish = _enum_name(get(cand, "finish_reason"))
        for part in get(get(cand, "content"), "parts") or []:
            call = get(part, "function_call")
            if call is not None:
                blocks.append(
                    ToolUseBlock(
                        id=get(call, "id") or f"call_{next(ids)}",
                        name=get(call, "name"),
                        input=dict(get(call, "args") or {}),
                    )
                )
            elif get(part, "text"):
                blocks.append(TextBlock(get(part, "text")))
    elif get(get(resp, "prompt_feedback"), "block_reason"):
        finish = "SAFETY"
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else STOP.get(finish, "end_turn")
    meta = get(resp, "usage_metadata")
    usage = Usage(get(meta, "prompt_token_count", 0) or 0, get(meta, "candidates_token_count", 0) or 0)
    return LLMResponse(blocks, stop, usage, model)


class GeminiLLM:
    def __init__(
        self,
        *,
        model: str,
        client: Any | None = None,
        api_key: str | None = None,
        vertex: bool = False,
        project: str | None = None,
        location: str | None = None,
        temperature: float | None = None,
        timeout: float = 120.0,
    ) -> None:
        self.provider = "VERTEX" if vertex else "GEMINI"
        if client is None:
            try:
                from google import genai
                from google.genai import types
            except ImportError as exc:  # the provider's package is an optional extra
                raise ImportError("provider GEMINI needs: pip install 'harbor-llm[gemini]'") from exc

            http = types.HttpOptions(timeout=int(timeout * 1000))  # milliseconds
            client = (
                genai.Client(vertexai=True, project=project, location=location, http_options=http)
                if vertex
                else genai.Client(api_key=api_key, http_options=http)
            )
        self.client: Any = client
        self.model = model
        self.temperature = temperature
        self._ids = itertools.count(1)

    def _config(self, max_tokens: int, system: str | None) -> dict[str, Any]:
        cfg: dict[str, Any] = {"max_output_tokens": max_tokens}
        if system:
            cfg["system_instruction"] = system
        if self.temperature is not None:
            cfg["temperature"] = self.temperature
        return cfg

    async def create(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        **_ignored: Any,
    ) -> LLMResponse:
        config = self._config(min(max_tokens, 16000), system)
        if tools:
            config["tools"] = to_function_tools(tools)
        resp = await self.client.aio.models.generate_content(
            model=self.model, contents=to_contents(normalize(messages)), config=config
        )
        return from_gemini(resp, self.model, self._ids)

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T:
        config = self._config(4000, system)
        config["response_mime_type"] = "application/json"
        config["response_json_schema"] = strict_schema(schema)
        resp = await self.client.aio.models.generate_content(
            model=self.model, contents=[{"role": "user", "parts": [{"text": user}]}], config=config
        )
        out = from_gemini(resp, self.model, self._ids)
        if out.stop_reason == "refusal":
            raise StructuredOutputError("Request was blocked by a safety filter")
        if out.stop_reason == "max_tokens":
            raise StructuredOutputError("Structured output was truncated")
        return parse_json_model("".join(b.text for b in out.content if b.type == "text"), schema)
