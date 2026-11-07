from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import Answer, FakeAnthropic, FakeOpenAI, anthropic_text, openai_reply

from harbor_llm import BUILDERS, LLM, ConfigError, StructuredOutputError, describe, make_llm, resolve_provider
from harbor_llm.providers.scripted import ReplayLLM

# ------------------------------------------------------------------ provider choice


def test_default_provider_comes_from_env_and_app_override_wins() -> None:
    env = {"DEFAULT_LLM_PROVIDER": "azure", "CHAT_LLM_PROVIDER": "claude"}
    assert resolve_provider(env) == "AZURE"
    assert resolve_provider(env, "CHAT_LLM_PROVIDER") == "ANTHROPIC"
    assert resolve_provider(env, "OTHER_LLM_PROVIDER") == "AZURE"
    assert resolve_provider(env, provider="aws-bedrock") == "BEDROCK"


@pytest.mark.parametrize(
    ("alias", "name"),
    [
        ("GCP", "VERTEX"),
        ("google", "GEMINI"),
        ("local", "OLLAMA"),
        ("vllm", "OPENAI_COMPATIBLE"),
        ("offline", "SCRIPTED"),
    ],
)
def test_aliases(alias: str, name: str) -> None:
    assert resolve_provider({"DEFAULT_LLM_PROVIDER": alias}) == name


def test_missing_or_unknown_provider() -> None:
    with pytest.raises(ConfigError, match="set DEFAULT_LLM_PROVIDER"):
        resolve_provider({})
    with pytest.raises(ConfigError, match="unknown LLM provider 'watson'"):
        resolve_provider({"DEFAULT_LLM_PROVIDER": "watson"})


def test_every_provider_has_a_builder() -> None:
    assert set(BUILDERS) == {
        "ANTHROPIC",
        "AZURE",
        "OPENAI",
        "OLLAMA",
        "OPENAI_COMPATIBLE",
        "BEDROCK",
        "GEMINI",
        "VERTEX",
        "SCRIPTED",
    }


# ------------------------------------------------------------------ building real SDK clients (no network calls)


def test_builds_each_provider_with_its_real_sdk_client() -> None:
    built = {
        "ANTHROPIC": make_llm(env={"DEFAULT_LLM_PROVIDER": "ANTHROPIC", "ANTHROPIC_API_KEY": "k"}),
        "AZURE": make_llm(
            env={
                "DEFAULT_LLM_PROVIDER": "AZURE",
                "AZURE_API_KEY": "k",
                "AZURE_DEPLOYMENT": "gpt-4.1",
                "AZURE_ENDPOINT": "https://x.example.com/",
                "AZURE_OPENAI_TEMPERATURE": "0.1",
            }
        ),
        "OPENAI": make_llm(env={"DEFAULT_LLM_PROVIDER": "OPENAI", "OPENAI_API_KEY": "k"}),
        "OLLAMA": make_llm(env={"DEFAULT_LLM_PROVIDER": "OLLAMA", "OLLAMA_MODEL": "qwen2.5:7b"}),
        "OPENAI_COMPATIBLE": make_llm(
            env={
                "DEFAULT_LLM_PROVIDER": "GROQ",
                "OPENAI_COMPATIBLE_BASE_URL": "http://h/v1",
                "OPENAI_COMPATIBLE_MODEL": "m",
            }
        ),
        "BEDROCK": make_llm(
            env={
                "DEFAULT_LLM_PROVIDER": "BEDROCK",
                "BEDROCK_MODEL_ID": "amazon.nova-pro-v1:0",
                "AWS_REGION": "us-east-1",
            }
        ),
        "GEMINI": make_llm(env={"DEFAULT_LLM_PROVIDER": "GEMINI", "GOOGLE_API_KEY": "k"}),
    }
    for name, llm in built.items():
        assert isinstance(llm, LLM), name
        assert describe(llm)["provider"] == name
    assert built["ANTHROPIC"].model == "claude-opus-5-5"
    assert built["AZURE"].model == "gpt-4.1" and built["AZURE"].temperature == 0.1
    assert built["OPENAI"].token_param == "max_completion_tokens"
    assert built["OLLAMA"].model == "qwen2.5:7b" and built["BEDROCK"].model == "amazon.nova-pro-v1:0"
    assert "k" not in json.dumps(describe(built["AZURE"]))


def test_model_overrides() -> None:
    env = {
        "DEFAULT_LLM_PROVIDER": "ANTHROPIC",
        "ANTHROPIC_API_KEY": "k",
        "ANTHROPIC_MODEL": "claude-sonnet-5-5",
    }
    assert make_llm(env=env).model == "claude-sonnet-5-5"
    assert make_llm(env={**env, "LLM_MODEL": "claude-haiku-5-5"}).model == "claude-haiku-5-5"
    assert make_llm(env=env, model="claude-fable-5-1").model == "claude-fable-5-1"


@pytest.mark.parametrize(
    ("env", "missing"),
    [
        ({"DEFAULT_LLM_PROVIDER": "AZURE"}, "AZURE_DEPLOYMENT"),
        ({"DEFAULT_LLM_PROVIDER": "AZURE", "AZURE_DEPLOYMENT": "d"}, "AZURE_API_KEY, AZURE_ENDPOINT"),
        ({"DEFAULT_LLM_PROVIDER": "OPENAI"}, "OPENAI_API_KEY"),
        ({"DEFAULT_LLM_PROVIDER": "OPENAI_COMPATIBLE"}, "OPENAI_COMPATIBLE_BASE_URL"),
        ({"DEFAULT_LLM_PROVIDER": "BEDROCK"}, "BEDROCK_MODEL_ID"),
        ({"DEFAULT_LLM_PROVIDER": "GEMINI"}, "GOOGLE_API_KEY"),
        ({"DEFAULT_LLM_PROVIDER": "VERTEX"}, "GOOGLE_CLOUD_PROJECT"),
        ({"DEFAULT_LLM_PROVIDER": "SCRIPTED"}, "LLM_SCRIPT"),
    ],
)
def test_missing_settings_are_named(env: dict[str, str], missing: str) -> None:
    with pytest.raises(ConfigError, match=missing):
        make_llm(env=env)


def test_gemini_switches_to_vertex_when_only_a_project_is_set() -> None:
    from conftest import FakeGemini

    llm = make_llm(env={"DEFAULT_LLM_PROVIDER": "GEMINI", "GOOGLE_CLOUD_PROJECT": "p"}, client=FakeGemini())
    assert llm.provider == "VERTEX"
    llm = make_llm(
        env={"DEFAULT_LLM_PROVIDER": "GEMINI", "GOOGLE_GENAI_USE_VERTEXAI": "true"}, client=FakeGemini()
    )
    assert llm.provider == "VERTEX"


def test_injected_clients_skip_credentials() -> None:
    llm = make_llm(env={"DEFAULT_LLM_PROVIDER": "AZURE", "AZURE_DEPLOYMENT": "d"}, client=FakeOpenAI())
    assert llm.provider == "AZURE"
    assert make_llm(env={"DEFAULT_LLM_PROVIDER": "OPENAI"}, client=FakeOpenAI()).model == "gpt-4.1"
    assert make_llm(env={"DEFAULT_LLM_PROVIDER": "OLLAMA"}, client=FakeOpenAI()).model == "llama3.1:8b"
    assert (
        make_llm(env={"DEFAULT_LLM_PROVIDER": "COMPATIBLE", "LLM_MODEL": "m"}, client=FakeOpenAI()).model
        == "m"
    )
    assert make_llm(env={"DEFAULT_LLM_PROVIDER": "BEDROCK", "LLM_MODEL": "m"}, client=object()).model == "m"


async def test_end_to_end_through_the_factory() -> None:
    fake = FakeAnthropic(anthropic_text('{"intent": "billing", "confidence": 1}'))
    llm = make_llm(env={"DEFAULT_LLM_PROVIDER": "CLAUDE"}, client=fake)
    assert (await llm.structured(system="s", user="u", schema=Answer)).intent == "billing"
    llm2 = make_llm(env={"DEFAULT_LLM_PROVIDER": "OPENAI"}, client=FakeOpenAI(openai_reply("hello")))
    assert (await llm2.create(messages=[{"role": "user", "content": "hi"}])).content[0].text == "hello"


# ------------------------------------------------------------------ SCRIPTED


class _Policy:
    provider = "SCRIPTED"
    model = "rules"

    def __init__(self, env: Any) -> None:
        self.env = env

    async def create(self, **kwargs: Any) -> Any:
        return None

    async def structured(self, **kwargs: Any) -> Any:
        return None


def policy_factory(env: Any) -> _Policy:
    return _Policy(env)


def test_scripted_policy_by_dotted_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "fake_policy_mod", sys.modules[__name__])
    llm = make_llm(env={"DEFAULT_LLM_PROVIDER": "SCRIPTED", "LLM_SCRIPT": "fake_policy_mod:policy_factory"})
    assert isinstance(llm, _Policy) and llm.env["LLM_SCRIPT"]
    with pytest.raises(ConfigError, match=r"package\.module:factory"):
        make_llm(env={"DEFAULT_LLM_PROVIDER": "SCRIPTED", "LLM_SCRIPT": "nodots"})
    with pytest.raises(ConfigError, match="LLM_SCRIPT 'nope:x'"):
        make_llm(env={"DEFAULT_LLM_PROVIDER": "SCRIPTED", "LLM_SCRIPT": "nope:x"})
    marker = object()
    assert make_llm(env={"DEFAULT_LLM_PROVIDER": "SCRIPTED"}, client=marker) is marker


async def test_replay_file(tmp_path: Path) -> None:
    script = tmp_path / "replay.jsonl"
    script.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"structured": {"intent": "billing", "confidence": 1}},
                {
                    "content": [
                        {"type": "tool_use", "id": "t", "name": "get_billing", "input": {"policy_id": "P"}}
                    ]
                },
                {"content": [{"type": "text", "text": "Done."}], "stop_reason": "end_turn"},
                {"structured": {"intent": 5}},
                {"content": []},
            ]
        )
        + "\n\n",
        encoding="utf-8",
    )
    llm = make_llm(env={"DEFAULT_LLM_PROVIDER": "OFFLINE", "LLM_SCRIPT_FILE": str(script)})
    assert isinstance(llm, ReplayLLM)
    assert (await llm.structured(system="s", user="u", schema=Answer)).intent == "billing"
    first = await llm.create(messages=[])
    assert first.stop_reason == "tool_use" and first.content[0].input == {"policy_id": "P"}
    assert (await llm.create(messages=[])).content[0].text == "Done."
    with pytest.raises(StructuredOutputError, match="Invalid"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="model turn where structured"):
        await llm.structured(system="s", user="u", schema=Answer)
    with pytest.raises(StructuredOutputError, match="exhausted"):
        await llm.create(messages=[])
    assert len(llm.calls) == 6
