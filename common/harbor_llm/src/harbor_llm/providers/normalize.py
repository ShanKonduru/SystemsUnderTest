"""Normalise the apps' messages for APIs that only know user and model turns (Bedrock Converse, Gemini).

System-role messages appended mid-conversation become text in the next user turn (marked as app context),
and consecutive turns of the same role are merged, since those APIs require strictly alternating roles.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from harbor_llm.types import get


@dataclass
class Part:
    kind: str  # text | tool_use | tool_result
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)
    is_error: bool = False


@dataclass
class Turn:
    role: str  # user | assistant
    parts: list[Part] = field(default_factory=list)


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(get(x, "text", "") for x in content or [])


def normalize(messages: list[dict[str, Any]]) -> list[Turn]:
    turns: list[Turn] = []
    for msg in messages:
        role, content = msg["role"], msg["content"]
        parts: list[Part] = []
        if role == "system":
            text = content if isinstance(content, str) else _result_text(content)
            role, parts = "user", [Part("text", text=f"[App context, not from the user]\n{text}")]
        elif isinstance(content, str):
            parts = [Part("text", text=content)]
        else:
            for b in content:
                kind = get(b, "type")
                if kind == "text":
                    parts.append(Part("text", text=get(b, "text", "")))
                elif kind == "tool_use":
                    parts.append(
                        Part(
                            "tool_use",
                            id=get(b, "id"),
                            name=get(b, "name"),
                            input=dict(get(b, "input") or {}),
                        )
                    )
                elif kind == "tool_result":
                    parts.append(
                        Part(
                            "tool_result",
                            id=get(b, "tool_use_id"),
                            text=_result_text(get(b, "content", "")),
                            is_error=bool(get(b, "is_error")),
                        )
                    )
        if not parts:
            continue
        if turns and turns[-1].role == role:
            turns[-1].parts.extend(parts)
        else:
            turns.append(Turn(role, parts))
    return turns


def tool_names(turns: list[Turn]) -> dict[str, str]:
    """tool_use id -> tool name (Gemini's function responses are matched by name, not id)."""
    return {p.id: p.name for t in turns for p in t.parts if p.kind == "tool_use"}
