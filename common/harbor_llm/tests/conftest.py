"""Fakes for each provider SDK. No network: requests are captured (and, where the SDK allows, validated)."""

from __future__ import annotations

from types import SimpleNamespace as NS
from typing import Any

import pytest
from pydantic import BaseModel


class Answer(BaseModel):
    intent: str
    confidence: float


class FakeAnthropic:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.beta = NS(messages=NS(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.responses.pop(0)


def anthropic_text(text: str, stop: str = "end_turn") -> Any:
    return NS(content=[NS(type="thinking", thinking=""), NS(type="text", text=text)], stop_reason=stop)


class FakeOpenAI:
    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = NS(completions=NS(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        nxt = self.responses.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt


def openai_reply(
    content: str | None = None, calls: list[tuple[str, str, str]] | None = None, finish: str = "stop"
) -> Any:
    tool_calls = [NS(id=i, function=NS(name=n, arguments=a)) for i, n, a in calls or []]
    return NS(
        choices=[NS(message=NS(content=content, tool_calls=tool_calls or None), finish_reason=finish)],
        usage=NS(prompt_tokens=11, completion_tokens=7),
        model="m",
    )


class FakeGemini:
    """Validates every request through google-genai's own pydantic types, then returns canned responses."""

    def __init__(self, *responses: Any) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.aio = NS(models=NS(generate_content=self._generate))

    async def _generate(self, *, model: str, contents: Any, config: Any) -> Any:
        from google.genai import types

        types.GenerateContentConfig.model_validate(config)
        for c in contents:
            types.Content.model_validate(c)
        self.calls.append({"model": model, "contents": contents, "config": config})
        return self.responses.pop(0)


def gemini_reply(parts: list[Any], finish: str = "STOP") -> Any:
    return NS(
        candidates=[NS(finish_reason=NS(name=finish), content=NS(parts=parts))],
        usage_metadata=NS(prompt_token_count=5, candidates_token_count=3),
        prompt_feedback=None,
    )


@pytest.fixture
def chat_history() -> list[dict[str, Any]]:
    """A tool-loop history in the apps' format, with a mid-conversation system message."""
    return [
        {"role": "user", "content": "What do I owe on POL-1002?"},
        {"role": "system", "content": "Today's date: 2026-11-03"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "Let me check."},
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "get_billing",
                    "input": {"policy_id": "POL-1002"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call_1",
                    "content": [{"type": "text", "text": '{"balance": 193.6}'}],
                },
            ],
        },
    ]


TOOLS = [
    {
        "name": "get_billing",
        "description": "Balance and due date",
        "input_schema": {
            "type": "object",
            "properties": {"policy_id": {"type": "string"}},
            "required": ["policy_id"],
            "additionalProperties": False,
        },
    }
]
