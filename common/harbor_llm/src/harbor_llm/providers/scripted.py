"""SCRIPTED: deterministic, offline "models" for CI and for tests without keys.

Two forms:
- ``LLM_SCRIPT=package.module:factory``: an app-supplied policy. ``factory(env)`` returns an object with the
  LLM interface (the claims agent and the multi-agent desk ship rule-based policies this way).
- ``LLM_SCRIPT_FILE=path.jsonl``: replay recorded responses in order. Each line is either
  ``{"content": [blocks], "stop_reason": "..."}`` for a model turn, or ``{"structured": {...}}`` for a
  structured-output call.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from harbor_llm.types import (
    ConfigError,
    LLMResponse,
    StructuredOutputError,
    T,
    TextBlock,
    ToolUseBlock,
    Usage,
)


class ReplayLLM:
    provider = "SCRIPTED"
    model = "replay"

    def __init__(self, lines: list[dict[str, Any]]) -> None:
        self._lines = list(lines)
        self.calls: list[dict[str, Any]] = []

    @classmethod
    def from_file(cls, path: str | Path) -> ReplayLLM:
        text = Path(path).read_text(encoding="utf-8")
        return cls([json.loads(line) for line in text.splitlines() if line.strip()])

    def _next(self, kind: str) -> dict[str, Any]:
        if not self._lines:
            raise StructuredOutputError(f"replay script exhausted (asked for {kind})")
        return self._lines.pop(0)

    async def create(self, **kwargs: Any) -> LLMResponse:
        self.calls.append({"create": kwargs})
        line = self._next("a model turn")
        blocks: list[Any] = []
        for b in line.get("content", []):
            if b.get("type") == "tool_use":
                blocks.append(ToolUseBlock(id=b["id"], name=b["name"], input=dict(b.get("input") or {})))
            else:
                blocks.append(TextBlock(b.get("text", "")))
        stop = line.get("stop_reason") or (
            "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
        )
        return LLMResponse(blocks, stop, Usage(), self.model)

    async def structured(self, *, system: str, user: str, schema: type[T], effort: str = "medium") -> T:
        self.calls.append({"structured": schema.__name__})
        line = self._next(f"structured {schema.__name__}")
        if "structured" not in line:
            raise StructuredOutputError("replay script has a model turn where structured output was expected")
        try:
            return schema.model_validate(line["structured"])
        except ValueError as exc:
            raise StructuredOutputError(f"Invalid structured output: {exc}") from exc


def scripted_from_env(env: Mapping[str, str]) -> Any:
    ref = env.get("LLM_SCRIPT", "").strip()
    if ref:
        module_name, sep, attr = ref.partition(":")
        if not sep:
            raise ConfigError(f"LLM_SCRIPT must be package.module:factory, got {ref!r}")
        try:
            factory = getattr(importlib.import_module(module_name), attr)
        except (ImportError, AttributeError) as exc:
            raise ConfigError(f"LLM_SCRIPT {ref!r}: {exc}") from exc
        return factory(env)
    path = env.get("LLM_SCRIPT_FILE", "").strip()
    if path:
        return ReplayLLM.from_file(path)
    raise ConfigError(
        "provider SCRIPTED needs LLM_SCRIPT (package.module:factory) or LLM_SCRIPT_FILE (a .jsonl replay)"
    )
