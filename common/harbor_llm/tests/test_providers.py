from __future__ import annotations

import json
from types import SimpleNamespace as NS
from typing import Any

import pytest
from conftest import (
    TOOLS,
    Answer,
    FakeAnthropic,
    FakeGemini,
    FakeOpenAI,
    anthropic_text,
    gemini_reply,
    openai_reply,
)

from harbor_llm import StructuredOutputError
from harbor_llm.providers.anthropic_provider import AnthropicLLM
from harbor_llm.providers.bedrock_provider import BedrockLLM
from harbor_llm.providers.gemini_provider import GeminiLLM
from harbor_llm.providers.normalize import normalize, tool_names
from harbor_llm.providers.openai_provider import OpenAIChatLLM, to_openai_messages
from harbor_llm.types import parse_json_model, strict_schema

# ------------------------------------------------------------------ Anthropic


async def test_anthropic_passes_requests_through_with_fallbacks_and_no_temperature() -> None:
    fake = FakeAnthropic(anthropic_text("hi"))
    llm = AnthropicLLM(model="claude-opus-5-5", client=fake)
    resp = await llm.create(
        messages=[{"role": "user", "content": "x"}],
        max_tokens=10,
        temperature=0.3,
        output_config={"effort": "low"},
    )
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5" and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and "temperature" not in call
    assert call["output_config"] == {"effort": "low"}
    assert [b.type for b in resp.content] == ["thinking", "text"]  # SDK blocks returned unchanged


async def test_anthropic_fallbacks_only_for_supporting_models_or_when_enabled() -> None:
    fake = FakeAnthropic(anthropic_text("hi"), anthropic_text("hi"))
    await AnthropicLLM(model="claude-haiku-5-5", client=fake).create(messages=[])
    await AnthropicLLM(model="claude-opus-5-5", use_fallbacks=False, client=fake).create(messages=[])
    assert all("fallbacks" not in c and "betas" not in c for c in fake.calls)


async def test_anthropic_structured_output() -> None:
    fake = FakeAnthropic(anthropic_text('{"intent": "billing", "confidence": 0.9}'))
    out = await AnthropicLLM(client=fake).structured(system="s", user="u", schema=Answer, effort="low")
    assert out == Answer(intent="billing", confidence=0.9)
    fmt = fake.calls[0]["output_config"]
    assert fmt["effort"] == "low" and fmt["format"]["type"] == "json_schema"
    assert fmt["format"]["schema"]["required"] == ["intent", "confidence"]


@pytest.mark.parametrize(
    ("stop", "text", "msg"),
    [("refusal", "", "declined"), ("max_tokens", "{", "truncated"), ("end_turn", "not json", "Invalid")],
)
async def test_anthropic_structured_failures(stop: str, text: str, msg: str) -> None:
    llm = AnthropicLLM(client=FakeAnthropic(anthropic_text(text, stop)))
    with pytest.raises(StructuredOutputError, match=msg):
        await llm.structured(system="s", user="u", schema=Answer)


# ------------------------------------------------------------------ OpenAI-style (OpenAI, Azure, Ollama, compatible)


def test_openai_message_conversion(chat_history: list[dict[str, Any]]) -> None:
    chat_history.append(
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call_2", "content": "boom", "is_error": True},
                {"type": "text", "text": "and?"},
            ],
        }
    )
    out = to_openai_messages("sys", chat_history)
    assert [m["role"] for m in out] == ["system", "user", "system", "assistant", "tool", "tool", "user"]
    assert out[3]["tool_calls"][0]["function"] == {
        "name": "get_billing",
        "arguments": '{"policy_id": "POL-1002"}',
    }
    assert out[4] == {"role": "tool", "tool_call_id": "call_1", "content": '{"balance": 193.6}'}
    assert out[5]["content"] == "[tool error] boom"
    assert to_openai_messages(None, [{"role": "assistant", "content": [NS(type="text", text="")]}]) == [
        {"role": "assistant", "content": None}
    ]


def test_tool_results_follow_their_calls_even_with_a_system_note_in_between() -> None:
    """Regression (live Azure 400): a system note between tool_use and tool_result must move after the results."""
    history = [
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "a", "name": "t", "input": {}},
                {"type": "tool_use", "id": "b", "name": "t", "input": {}},
            ],
        },
        {"role": "system", "content": "note"},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "1"}]},
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "b", "content": "2"},
                {"type": "text", "text": "more"},
            ],
        },
        {"role": "assistant", "content": [{"type": "tool_use", "id": "c", "name": "t", "input": {}}]},
        {"role": "assistant", "content": "no result came for c"},
    ]
    roles = [
        (m["role"], m.get("tool_call_id") or m.get("content")) for m in to_openai_messages(None, history)
    ]
    assert roles[1][0] == "assistant"
    assert roles[2:6] == [("tool", "a"), ("tool", "b"), ("system", "note"), ("user", "more")]
    assert roles[6][0] == "assistant" and roles[7] == ("assistant", "no result came for c")


async def test_openai_tool_call_round_trip(chat_history: list[dict[str, Any]]) -> None:
    fake = FakeOpenAI(
        openai_reply("checking", [("call_9", "get_billing", '{"policy_id": "POL-1002"}')], "tool_calls"),
        openai_reply(None, [("c", "t", "{bad json")], "stop"),
        openai_reply("done", finish="length"),
    )
    llm = OpenAIChatLLM(provider="AZURE", model="gpt-4.1", client=fake, temperature=0.1)
    r1 = await llm.create(
        messages=chat_history, system="sys", tools=TOOLS, max_tokens=99999, cache_control={}
    )
    assert r1.stop_reason == "tool_use" and r1.content[1].input == {"policy_id": "POL-1002"}
    assert r1.usage.input_tokens == 11 and r1.model == "m"
    call = fake.calls[0]
    assert call["max_tokens"] == 16000 and call["temperature"] == 0.1 and "cache_control" not in call
    assert call["tools"][0]["function"]["parameters"] == TOOLS[0]["input_schema"]
    r2 = await llm.create(messages=[])
    assert r2.content[0].input == {"_raw_arguments": "{bad json"}
    assert (await llm.create(messages=[])).stop_reason == "max_tokens"


async def test_openai_token_param_and_no_temperature_by_default() -> None:
    fake = FakeOpenAI(openai_reply("x"))
    await OpenAIChatLLM(
        provider="OPENAI", model="m", client=fake, token_param="max_completion_tokens"
    ).create(messages=[], max_tokens=50)
    assert fake.calls[0]["max_completion_tokens"] == 50 and "temperature" not in fake.calls[0]


async def test_openai_structured_strict_schema_and_fallback_to_json_object() -> None:
    good = openai_reply('{"intent": "billing", "confidence": 1}')
    fake = FakeOpenAI(good, RuntimeError("response_format json_schema is not supported"), good)
    llm = OpenAIChatLLM(provider="OLLAMA", model="llama3.1:8b", client=fake)
    assert (await llm.structured(system="s", user="u", schema=Answer)).intent == "billing"
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True
    assert (await llm.structured(system="s", user="u", schema=Answer)).confidence == 1
    assert fake.calls[2]["response_format"] == {"type": "json_object"}
    assert "JSON Schema" in fake.calls[2]["messages"][0]["content"]


async def test_openai_structured_errors() -> None:
    llm = OpenAIChatLLM(
        provider="OPENAI",
        model="m",
        client=FakeOpenAI(
            RuntimeError("rate limited"),
            openai_reply("x", finish="content_filter"),
            openai_reply("{", finish="length"),
        ),
    )
    with pytest.raises(RuntimeError, match="rate limited"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="content filter"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="truncated"):
        await llm.structured(system="s", user="u", schema=Answer)


# ------------------------------------------------------------------ normalisation


def test_normalize_merges_roles_and_folds_system_messages(chat_history: list[dict[str, Any]]) -> None:
    turns = normalize(
        [
            *chat_history,
            {"role": "system", "content": [{"type": "text", "text": "note"}]},
            {"role": "assistant", "content": []},
        ]
    )
    assert [t.role for t in turns] == ["user", "assistant", "user"]
    assert turns[0].parts[1].text.startswith("[App context, not from the user]")
    assert turns[2].parts[0].kind == "tool_result" and turns[2].parts[0].text == '{"balance": 193.6}'
    assert tool_names(turns) == {"call_1": "get_billing"}


# ------------------------------------------------------------------ Bedrock (validated by botocore's own service model)


def _bedrock(
    *responses: dict[str, Any], expect: list[dict[str, Any]] | None = None
) -> tuple[BedrockLLM, Any]:
    import boto3
    from botocore.stub import ANY, Stubber

    client = boto3.client(
        "bedrock-runtime", region_name="us-east-1", aws_access_key_id="x", aws_secret_access_key="y"
    )
    stub = Stubber(client)
    for i, r in enumerate(responses):
        stub.add_response(
            "converse",
            r,
            (expect or [None] * len(responses))[i]
            or {"modelId": ANY, "messages": ANY, "inferenceConfig": ANY},
        )
    stub.activate()
    return BedrockLLM(model="anthropic.claude-opus-5-5", client=client, temperature=0.2), stub


def _converse(content: list[dict[str, Any]], stop: str = "end_turn") -> dict[str, Any]:
    return {
        "output": {"message": {"role": "assistant", "content": content}},
        "stopReason": stop,
        "usage": {"inputTokens": 4, "outputTokens": 2, "totalTokens": 6},
        "metrics": {"latencyMs": 1},
    }


async def test_bedrock_tool_loop_request_is_valid_converse(chat_history: list[dict[str, Any]]) -> None:
    from botocore.stub import ANY

    chat_history[-1]["content"].append(
        {"type": "tool_result", "tool_use_id": "x", "content": "e", "is_error": True}
    )
    reply = _converse(
        [
            {"text": "Balance $193.60"},
            {"toolUse": {"toolUseId": "t1", "name": "get_billing", "input": {"policy_id": "POL-1002"}}},
        ],
        "tool_use",
    )
    llm, stub = _bedrock(
        reply,
        expect=[
            {
                "modelId": "anthropic.claude-opus-5-5",
                "messages": ANY,
                "system": ANY,
                "toolConfig": ANY,
                "inferenceConfig": {"maxTokens": 100, "temperature": 0.2},
            }
        ],
    )
    out = await llm.create(messages=chat_history, system="sys", tools=TOOLS, max_tokens=100)
    stub.assert_no_pending_responses()
    assert (
        out.stop_reason == "tool_use"
        and out.content[1].name == "get_billing"
        and out.usage.output_tokens == 2
    )


async def test_bedrock_structured_via_respond_tool_or_text() -> None:
    from botocore.stub import ANY

    tool_reply = _converse(
        [
            {
                "toolUse": {
                    "toolUseId": "r",
                    "name": "respond",
                    "input": {"intent": "billing", "confidence": 0.5},
                }
            }
        ],
        "tool_use",
    )
    text_reply = _converse([{"text": '```json\n{"intent": "x", "confidence": 1}\n```'}])
    llm, _ = _bedrock(
        tool_reply,
        text_reply,
        _converse([], "guardrail_intervened"),
        _converse([], "max_tokens"),
        _converse([{"toolUse": {"toolUseId": "r", "name": "respond", "input": {"intent": 1}}}], "tool_use"),
        expect=[
            {
                "modelId": "anthropic.claude-opus-5-5",
                "system": ANY,
                "messages": ANY,
                "toolConfig": ANY,
                "inferenceConfig": {"maxTokens": 4000, "temperature": 0.2},
            }
        ]
        * 5,
    )
    assert (await llm.structured(system="s", user="u", schema=Answer)).confidence == 0.5
    assert (await llm.structured(system="s", user="u", schema=Answer)).intent == "x"
    with pytest.raises(StructuredOutputError, match="guardrail"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="truncated"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="Invalid"):
        await llm.structured(system="s", user="u", schema=Answer)


# ------------------------------------------------------------------ Gemini (validated by google-genai's own types)


async def test_gemini_tool_loop_and_usage(chat_history: list[dict[str, Any]]) -> None:
    chat_history[-1]["content"].append(
        {"type": "tool_result", "tool_use_id": "zz", "content": "e", "is_error": True}
    )
    fake = FakeGemini(
        gemini_reply(
            [
                NS(function_call=NS(id=None, name="get_billing", args={"policy_id": "P"}), text=None),
                NS(function_call=None, text="checking"),
            ]
        ),
        gemini_reply([NS(function_call=None, text="")], finish="SAFETY"),
    )
    llm = GeminiLLM(model="gemini-2.5-flash", client=fake, temperature=0.0)
    out = await llm.create(messages=chat_history, system="sys", tools=TOOLS)
    call = fake.calls[0]
    assert call["config"]["system_instruction"] == "sys" and call["config"]["temperature"] == 0.0
    assert (
        call["config"]["tools"][0]["function_declarations"][0]["parameters_json_schema"]
        == TOOLS[0]["input_schema"]
    )
    responses = [p for c in call["contents"] for p in c["parts"] if "function_response" in p]
    assert responses[0]["function_response"] == {
        "name": "get_billing",
        "response": {"result": '{"balance": 193.6}'},
    }
    assert responses[1]["function_response"]["response"] == {"error": "e"}
    assert out.stop_reason == "tool_use" and out.content[0].id == "call_1" and out.usage.input_tokens == 5
    assert (await llm.create(messages=[{"role": "user", "content": "x"}])).stop_reason == "refusal"
    assert llm.provider == "GEMINI"


async def test_gemini_structured_and_blocked_prompt() -> None:
    blocked = NS(candidates=[], usage_metadata=None, prompt_feedback=NS(block_reason="SAFETY"))
    fake = FakeGemini(
        gemini_reply([NS(function_call=None, text='{"intent": "b", "confidence": 0.1}')]),
        blocked,
        gemini_reply([NS(function_call=None, text="{")], finish="MAX_TOKENS"),
        NS(candidates=[], usage_metadata=None, prompt_feedback=None),
    )
    llm = GeminiLLM(model="m", client=fake, vertex=True)
    assert (await llm.structured(system="s", user="u", schema=Answer)).intent == "b"
    assert fake.calls[0]["config"]["response_json_schema"] == strict_schema(Answer)
    with pytest.raises(StructuredOutputError, match="safety"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="truncated"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="Invalid"):
        await llm.structured(system="s", user="u", schema=Answer)
    assert llm.provider == "VERTEX"


# ------------------------------------------------------------------ shared helpers


def test_strict_schema_inlines_refs_and_drops_titles() -> None:
    from pydantic import BaseModel

    class Inner(BaseModel):
        x: int = 3

    class Outer(BaseModel):
        inner: Inner
        items: list[Inner]

    schema = strict_schema(Outer)
    assert "$defs" not in schema and "title" not in json.dumps(schema)
    assert schema["properties"]["inner"]["properties"]["x"] == {"type": "integer"}


def test_parse_json_model_handles_fences() -> None:
    assert parse_json_model('```json\n{"intent":"a","confidence":1}\n```', Answer).intent == "a"
    with pytest.raises(StructuredOutputError):
        parse_json_model("```", Answer)
