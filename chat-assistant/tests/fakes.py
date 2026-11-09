"""A scripted stand-in for the Claude API so engine mechanics can be tested offline."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

_ids = itertools.count(1)


@dataclass
class TextBlock:
    text: str
    type: str = "text"


@dataclass
class ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = field(default_factory=lambda: f"toolu_{next(_ids):04d}")
    type: str = "tool_use"


@dataclass
class FakeMessage:
    content: list[Any]
    stop_reason: str = "end_turn"


def text(reply: str) -> FakeMessage:
    return FakeMessage([TextBlock(reply)], "end_turn")


def tools(*calls: tuple[str, dict[str, Any]]) -> FakeMessage:
    return FakeMessage([ToolUseBlock(name, args) for name, args in calls], "tool_use")


class ScriptedLLM:
    """structured() pops from per-schema queues; create() pops executor responses in order."""

    def __init__(self, structured: list[BaseModel] | None = None, executor: list[FakeMessage] | None = None):
        self.structured_queue = list(structured or [])
        self.executor_queue = list(executor or [])
        self.create_calls: list[dict[str, Any]] = []

    async def structured(self, *, system: str, user: str, schema: type, effort: str):
        for i, item in enumerate(self.structured_queue):
            if isinstance(item, schema):
                return self.structured_queue.pop(i)
        raise AssertionError(f"No scripted {schema.__name__} left")

    async def create(self, **kwargs: Any) -> FakeMessage:
        # Snapshot the messages: the engine keeps appending to the same list.
        self.create_calls.append({**kwargs, "messages": list(kwargs["messages"])})
        if not self.executor_queue:
            raise AssertionError("No scripted executor response left")
        return self.executor_queue.pop(0)
