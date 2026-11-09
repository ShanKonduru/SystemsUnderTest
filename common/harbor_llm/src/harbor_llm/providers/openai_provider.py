"""OpenAI chat completions: OpenAI, Azure OpenAI, Ollama and any OpenAI-compatible endpoint.

Converts the apps' message format (text / tool_use / tool_result blocks, system turns) to chat completions and
the response back to blocks, so the apps work unchanged. Structured output asks for a strict ``json_schema``
response format; servers that don't support it (some local models) get one retry with ``json_object`` and the
schema in the prompt.
"""

from __future__ import annotations

import json
from typing import Any

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

FINISH_TO_STOP = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
    "function_call": "tool_use",
}


def to_openai_messages(system: str | None, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "\n".join(get(b, "text", "") for b in content if get(b, "type") == "text").strip()
            calls = [
                {
                    "id": get(b, "id"),
                    "type": "function",
                    "function": {"name": get(b, "name"), "arguments": json.dumps(get(b, "input") or {})},
                }
                for b in content
                if get(b, "type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        texts: list[str] = []
        for b in content:
            if get(b, "type") == "tool_result":
                result = get(b, "content", "")
                if not isinstance(result, str):
                    result = "\n".join(get(x, "text", "") for x in result)
                if get(b, "is_error"):
                    result = f"[tool error] {result}"
                out.append({"role": "tool", "tool_call_id": get(b, "tool_use_id"), "content": result})
            elif get(b, "type") == "text":
                texts.append(get(b, "text", ""))
        if texts:
            out.append({"role": role, "content": "\n".join(texts)})
    return _tool_results_first(out)


def _tool_results_first(out: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat completions require an assistant's tool calls to be answered by tool messages immediately after it.
    Anything the app put in between (e.g. a mid-conversation system note) moves to just after the results."""
    result: list[dict[str, Any]] = []
    i = 0
    while i < len(out):
        msg = out[i]
        result.append(msg)
        i += 1
        pending = {c["id"] for c in msg.get("tool_calls") or []}
        held: list[dict[str, Any]] = []
        while pending and i < len(out):
            nxt = out[i]
            if nxt["role"] == "tool" and nxt.get("tool_call_id") in pending:
                pending.discard(nxt["tool_call_id"])
                result.append(nxt)
            elif nxt["role"] == "assistant":
                break
            else:
                held.append(nxt)
            i += 1
        result.extend(held)
    return result


def to_openai_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            },
        }
        for t in tools
    ]


def from_openai_response(resp: Any) -> LLMResponse:
    choice = resp.choices[0]
    msg = choice.message
    blocks: list[Any] = []
    if msg.content:
        blocks.append(TextBlock(msg.content))
    for call in msg.tool_calls or []:
        try:
            args = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {"_raw_arguments": call.function.arguments}
        blocks.append(ToolUseBlock(id=call.id, name=call.function.name, input=args))
    stop = FINISH_TO_STOP.get(choice.finish_reason or "stop", "end_turn")
    if any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"
    usage = getattr(resp, "usage", None)
    return LLMResponse(
        blocks,
        stop,
        Usage(getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0),
        getattr(resp, "model", "") or "",
    )


class OpenAIChatLLM:
    """One class for every chat-completions server; ``provider`` names which one for traces and health."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        client: Any,
        temperature: float | None = None,
        token_param: str = "max_tokens",
    ) -> None:
        self.provider = provider
        self.model = model
        self.client = client
        self.temperature = temperature
        self.token_param = token_param  # newer OpenAI models take max_completion_tokens

    def _base(self, max_tokens: int) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"model": self.model, self.token_param: max_tokens}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        return kwargs

    async def create(
        self,
        *,
        messages: list[dict[str, Any]],
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = 4096,
        **_ignored: Any,
    ) -> LLMResponse:
        kwargs = self._base(min(max_tokens, 16000))
        kwargs["messages"] = to_openai_messages(system, messages)
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
        return from_openai_response(await self.client.chat.completions.create(**kwargs))

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T:
        kwargs = self._base(4000)
        kwargs["messages"] = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        try:
            resp = await self.client.chat.completions.create(
                **kwargs,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema.__name__, "strict": True, "schema": strict_schema(schema)},
                },
            )
        except Exception as exc:
            if "response_format" not in str(exc) and "json_schema" not in str(exc):
                raise
            kwargs["messages"][0]["content"] = (
                f"{system}\n\nReply with one JSON object only, matching this JSON Schema:\n"
                f"{json.dumps(strict_schema(schema))}"
            )
            resp = await self.client.chat.completions.create(
                **kwargs, response_format={"type": "json_object"}
            )
        choice = resp.choices[0]
        if choice.finish_reason == "content_filter":
            raise StructuredOutputError("Request was blocked by the content filter")
        if choice.finish_reason == "length":
            raise StructuredOutputError("Structured output was truncated")
        return parse_json_model(choice.message.content or "", schema)
