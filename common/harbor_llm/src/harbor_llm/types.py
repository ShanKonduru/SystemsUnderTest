"""The one interface every provider implements, and the message format the apps speak.

Apps build conversations in one format (Anthropic Messages style, because the chat assistant was written
that way): ``{"role": "user"|"assistant"|"system", "content": str | [blocks]}`` with ``text``, ``tool_use``
and ``tool_result`` blocks. Each provider converts to and from its own API. Responses expose ``content``
(blocks with ``.type``), ``stop_reason`` (``end_turn`` | ``tool_use`` | ``max_tokens`` | ``refusal``) and
``usage`` (``input_tokens`` / ``output_tokens``).
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class StructuredOutputError(RuntimeError):
    """The model did not return valid structured output (refused, truncated or malformed)."""


class ConfigError(ValueError):
    """The selected provider is missing a setting or an SDK."""


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class LLMResponse:
    content: list[Any]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""


@runtime_checkable
class LLM(Protocol):
    provider: str
    model: str

    async def create(self, **kwargs: Any) -> Any:
        """One model turn: ``messages``, ``system``, ``tools``, ``max_tokens``. Extra Anthropic-only keywords
        (``output_config``, ``cache_control``) are used by the Anthropic provider and ignored by the others."""
        ...

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T: ...


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema -> structured-output schema: $refs inlined, titles and defaults dropped."""
    raw = model.model_json_schema()
    defs = raw.pop("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    result: dict[str, Any] = walk(raw)
    return result


def get(block: Any, key: str, default: Any = None) -> Any:
    """Read a field from a dict block or an SDK object block."""
    return block.get(key, default) if isinstance(block, dict) else getattr(block, key, default)


def parse_json_model(text: str, schema: type[T]) -> T:
    """Validate model output text as ``schema``; tolerate a ```json fence around it."""
    import json

    body = text.strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        body = body.rsplit("```", 1)[0]
    try:
        return schema.model_validate(json.loads(body))
    except (json.JSONDecodeError, ValueError) as exc:
        raise StructuredOutputError(f"Invalid structured output: {exc}") from exc
