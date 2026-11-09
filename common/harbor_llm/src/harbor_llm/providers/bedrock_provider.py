"""AWS Bedrock through the Converse API (``boto3`` ``bedrock-runtime``): any Bedrock model with tool use.

Credentials come from the standard AWS chain (environment, profile, SSO, instance role). Structured output
asks the model to call a ``respond`` tool whose input schema is the target schema; if a model answers in text
instead, that text is parsed as JSON.
"""

from __future__ import annotations

import asyncio
from typing import Any

from harbor_llm.providers.normalize import Turn, normalize
from harbor_llm.types import (
    LLMResponse,
    StructuredOutputError,
    T,
    TextBlock,
    ToolUseBlock,
    Usage,
    parse_json_model,
    strict_schema,
)

STOP = {
    "end_turn": "end_turn",
    "tool_use": "tool_use",
    "max_tokens": "max_tokens",
    "stop_sequence": "end_turn",
    "content_filtered": "refusal",
    "guardrail_intervened": "refusal",
}


def to_converse(turns: list[Turn]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for turn in turns:
        content: list[dict[str, Any]] = []
        for p in turn.parts:
            if p.kind == "text":
                content.append({"text": p.text or " "})
            elif p.kind == "tool_use":
                content.append({"toolUse": {"toolUseId": p.id, "name": p.name, "input": p.input}})
            else:
                content.append(
                    {
                        "toolResult": {
                            "toolUseId": p.id,
                            "content": [{"text": p.text}],
                            "status": "error" if p.is_error else "success",
                        }
                    }
                )
        out.append({"role": turn.role, "content": content})
    return out


def to_tool_config(tools: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "tools": [
            {
                "toolSpec": {
                    "name": t["name"],
                    "description": t.get("description", "") or t["name"],
                    "inputSchema": {"json": t["input_schema"]},
                }
            }
            for t in tools
        ]
    }


def from_converse(resp: dict[str, Any], model: str) -> LLMResponse:
    blocks: list[Any] = []
    for c in resp.get("output", {}).get("message", {}).get("content", []):
        if "text" in c:
            blocks.append(TextBlock(c["text"]))
        elif "toolUse" in c:
            tu = c["toolUse"]
            blocks.append(
                ToolUseBlock(id=tu["toolUseId"], name=tu["name"], input=dict(tu.get("input") or {}))
            )
    stop = STOP.get(resp.get("stopReason", "end_turn"), "end_turn")
    if any(b.type == "tool_use" for b in blocks):
        stop = "tool_use"
    usage = resp.get("usage", {})
    return LLMResponse(blocks, stop, Usage(usage.get("inputTokens", 0), usage.get("outputTokens", 0)), model)


class BedrockLLM:
    provider = "BEDROCK"

    def __init__(
        self,
        *,
        model: str,
        client: Any | None = None,
        region: str | None = None,
        temperature: float | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
    ) -> None:
        if client is None:
            try:
                import boto3
            except ImportError as exc:  # the provider's package is an optional extra
                raise ImportError("provider BEDROCK needs: pip install 'harbor-llm[bedrock]'") from exc
            from botocore.config import Config

            config = Config(
                read_timeout=timeout,
                connect_timeout=10,
                retries={"total_max_attempts": max_retries + 1, "mode": "standard"},
            )
            client = boto3.client("bedrock-runtime", region_name=region, config=config)
        self.client = client
        self.model = model
        self.temperature = temperature

    async def _converse(self, **kwargs: Any) -> dict[str, Any]:
        result: dict[str, Any] = await asyncio.to_thread(self.client.converse, **kwargs)
        return result

    def _inference(self, max_tokens: int) -> dict[str, Any]:
        cfg: dict[str, Any] = {"maxTokens": max_tokens}
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
        kwargs: dict[str, Any] = {
            "modelId": self.model,
            "messages": to_converse(normalize(messages)),
            "inferenceConfig": self._inference(min(max_tokens, 16000)),
        }
        if system:
            kwargs["system"] = [{"text": system}]
        if tools:
            kwargs["toolConfig"] = to_tool_config(tools)
        return from_converse(await self._converse(**kwargs), self.model)

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T:
        tool = {
            "name": "respond",
            "description": "Return the answer as structured data.",
            "input_schema": strict_schema(schema),
        }
        resp = await self._converse(
            modelId=self.model,
            system=[{"text": f"{system}\n\nAnswer by calling the respond tool."}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            toolConfig=to_tool_config([tool]),
            inferenceConfig=self._inference(4000),
        )
        out = from_converse(resp, self.model)
        if out.stop_reason == "refusal":
            raise StructuredOutputError("Request was blocked by a content filter or guardrail")
        for b in out.content:
            if b.type == "tool_use" and b.name == "respond":
                try:
                    return schema.model_validate(b.input)
                except ValueError as exc:
                    raise StructuredOutputError(f"Invalid structured output: {exc}") from exc
        if out.stop_reason == "max_tokens":
            raise StructuredOutputError("Structured output was truncated")
        return parse_json_model(next((b.text for b in out.content if b.type == "text"), ""), schema)
