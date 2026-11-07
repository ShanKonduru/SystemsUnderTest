"""``make_llm()``: pick and build the provider named by ``DEFAULT_LLM_PROVIDER`` (or an app-specific override).

Providers and the settings each reads (all from the environment / ``.env``; nothing is hard-coded):

| Provider            | Aliases                      | Settings                                                              |
|---------------------|------------------------------|-----------------------------------------------------------------------|
| ANTHROPIC           | CLAUDE                       | ANTHROPIC_API_KEY, ANTHROPIC_MODEL, ANTHROPIC_FALLBACKS               |
| AZURE               | AZURE_OPENAI                 | AZURE_API_KEY, AZURE_ENDPOINT, AZURE_DEPLOYMENT, AZURE_API_VERSION    |
| OPENAI              |                              | OPENAI_API_KEY, OPENAI_MODEL, OPENAI_BASE_URL                         |
| OLLAMA              | LOCAL                        | OLLAMA_BASE_URL, OLLAMA_MODEL                                         |
| OPENAI_COMPATIBLE   | COMPATIBLE, VLLM, LMSTUDIO,  | OPENAI_COMPATIBLE_BASE_URL, OPENAI_COMPATIBLE_API_KEY,                |
|                     | GROQ, MISTRAL, TOGETHER      | OPENAI_COMPATIBLE_MODEL                                               |
| BEDROCK             | AWS, AWS_BEDROCK             | AWS_REGION, BEDROCK_MODEL_ID (+ the standard AWS credential chain)    |
| GEMINI              | GOOGLE                       | GOOGLE_API_KEY, GEMINI_MODEL                                          |
| VERTEX              | GCP, VERTEX_AI               | GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION, GEMINI_MODEL (+ ADC)     |
| SCRIPTED            | OFFLINE, FAKE                | LLM_SCRIPT or LLM_SCRIPT_FILE                                         |

``LLM_MODEL`` overrides the provider's model setting; ``LLM_TEMPERATURE`` is sent only where the provider
accepts it (never to Claude).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any

from harbor_llm.types import ConfigError

ALIASES = {
    "CLAUDE": "ANTHROPIC",
    "AZURE_OPENAI": "AZURE",
    "LOCAL": "OLLAMA",
    "COMPATIBLE": "OPENAI_COMPATIBLE",
    "VLLM": "OPENAI_COMPATIBLE",
    "LMSTUDIO": "OPENAI_COMPATIBLE",
    "GROQ": "OPENAI_COMPATIBLE",
    "MISTRAL": "OPENAI_COMPATIBLE",
    "TOGETHER": "OPENAI_COMPATIBLE",
    "AWS": "BEDROCK",
    "AWS_BEDROCK": "BEDROCK",
    "GOOGLE": "GEMINI",
    "GCP": "VERTEX",
    "VERTEX_AI": "VERTEX",
    "OFFLINE": "SCRIPTED",
    "FAKE": "SCRIPTED",
}
DEFAULT_MODELS = {
    "ANTHROPIC": "claude-opus-5-5",
    "OPENAI": "gpt-4.1",
    "OLLAMA": "llama3.1:8b",
    "GEMINI": "gemini-2.5-flash",
    "VERTEX": "gemini-2.5-flash",
}


def _need(env: Mapping[str, str], *names: str) -> list[str]:
    missing = [n for n in names if not env.get(n, "").strip()]
    if missing:
        raise ConfigError(f"missing setting(s): {', '.join(missing)}")
    return [env[n].strip() for n in names]


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    value = env.get(name)
    return (
        default if value is None or not value.strip() else value.strip().lower() in {"1", "true", "yes", "on"}
    )


def _temperature(env: Mapping[str, str], *names: str) -> float | None:
    for n in names:
        if env.get(n, "").strip():
            return float(env[n])
    return None


def resolve_provider(
    env: Mapping[str, str], override_var: str | None = None, provider: str | None = None
) -> str:
    raw = (
        provider or (env.get(override_var, "") if override_var else "") or env.get("DEFAULT_LLM_PROVIDER", "")
    )
    name = raw.strip().upper().replace("-", "_")
    if not name:
        raise ConfigError(
            "set DEFAULT_LLM_PROVIDER in .env (e.g. AZURE, ANTHROPIC, OPENAI, OLLAMA, BEDROCK, GEMINI)"
        )
    name = ALIASES.get(name, name)
    if name not in BUILDERS:
        raise ConfigError(f"unknown LLM provider {raw!r}; choose one of {', '.join(sorted(BUILDERS))}")
    return name


def _model(env: Mapping[str, str], provider: str, own: str, model: str | None) -> str:
    return (
        model
        or env.get("LLM_MODEL", "").strip()
        or env.get(own, "").strip()
        or DEFAULT_MODELS.get(provider, "")
    )


def _openai_client(**kwargs: Any) -> Any:
    try:
        import openai
    except ImportError as exc:  # pragma: no cover - depends on installed extras
        raise ImportError("this provider needs: pip install 'harbor-llm[openai]'") from exc
    return openai.AsyncOpenAI(**kwargs)


def _build_anthropic(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.anthropic_provider import AnthropicLLM

    return AnthropicLLM(
        model=_model(env, "ANTHROPIC", "ANTHROPIC_MODEL", model),
        use_fallbacks=_bool(env, "ANTHROPIC_FALLBACKS", True),
        client=client,
        api_key=env.get("ANTHROPIC_API_KEY") or None,
    )


def _build_azure(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.openai_provider import OpenAIChatLLM

    deployment = model or env.get("LLM_MODEL", "").strip() or _need(env, "AZURE_DEPLOYMENT")[0]
    if client is None:
        key, endpoint = _need(env, "AZURE_API_KEY", "AZURE_ENDPOINT")
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - depends on installed extras
            raise ImportError("provider AZURE needs: pip install 'harbor-llm[openai]'") from exc
        client = openai.AsyncAzureOpenAI(
            api_key=key,
            azure_endpoint=endpoint,
            api_version=env.get("AZURE_API_VERSION") or "2024-12-01-preview",
        )
    return OpenAIChatLLM(
        provider="AZURE",
        model=deployment,
        client=client,
        temperature=_temperature(env, "LLM_TEMPERATURE", "AZURE_OPENAI_TEMPERATURE"),
    )


def _build_openai(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.openai_provider import OpenAIChatLLM

    if client is None:
        (key,) = _need(env, "OPENAI_API_KEY")
        client = _openai_client(api_key=key, base_url=env.get("OPENAI_BASE_URL") or None)
    return OpenAIChatLLM(
        provider="OPENAI",
        model=_model(env, "OPENAI", "OPENAI_MODEL", model),
        client=client,
        temperature=_temperature(env, "LLM_TEMPERATURE"),
        token_param="max_completion_tokens",
    )


def _build_ollama(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.openai_provider import OpenAIChatLLM

    if client is None:
        base = env.get("OLLAMA_BASE_URL") or "http://localhost:11434/v1"
        client = _openai_client(api_key="ollama", base_url=base)
    return OpenAIChatLLM(
        provider="OLLAMA",
        model=_model(env, "OLLAMA", "OLLAMA_MODEL", model),
        client=client,
        temperature=_temperature(env, "LLM_TEMPERATURE"),
    )


def _build_compatible(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.openai_provider import OpenAIChatLLM

    if client is None:
        (base,) = _need(env, "OPENAI_COMPATIBLE_BASE_URL")
        client = _openai_client(api_key=env.get("OPENAI_COMPATIBLE_API_KEY") or "none", base_url=base)
    name = model or env.get("LLM_MODEL", "").strip() or _need(env, "OPENAI_COMPATIBLE_MODEL")[0]
    return OpenAIChatLLM(
        provider="OPENAI_COMPATIBLE",
        model=name,
        client=client,
        temperature=_temperature(env, "LLM_TEMPERATURE"),
    )


def _build_bedrock(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.bedrock_provider import BedrockLLM

    name = model or env.get("LLM_MODEL", "").strip() or _need(env, "BEDROCK_MODEL_ID")[0]
    region = env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or None
    return BedrockLLM(
        model=name, client=client, region=region, temperature=_temperature(env, "LLM_TEMPERATURE")
    )


def _build_gemini(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.gemini_provider import GeminiLLM

    # Vertex AI when asked for, or when a GCP project is set and no Gemini API key is.
    vertex = _bool(env, "GOOGLE_GENAI_USE_VERTEXAI", False) or (
        not env.get("GOOGLE_API_KEY") and bool(env.get("GOOGLE_CLOUD_PROJECT"))
    )
    if not vertex and client is None and not env.get("GOOGLE_API_KEY"):
        raise ConfigError("missing setting(s): GOOGLE_API_KEY (or GOOGLE_CLOUD_PROJECT for Vertex AI)")
    if vertex:
        return _build_vertex(env, model, client)
    return GeminiLLM(
        model=_model(env, "GEMINI", "GEMINI_MODEL", model),
        client=client,
        api_key=env.get("GOOGLE_API_KEY"),
        temperature=_temperature(env, "LLM_TEMPERATURE"),
    )


def _build_vertex(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.gemini_provider import GeminiLLM

    project = env.get("GOOGLE_CLOUD_PROJECT", "")
    if client is None:
        (project,) = _need(env, "GOOGLE_CLOUD_PROJECT")
    return GeminiLLM(
        model=_model(env, "VERTEX", "GEMINI_MODEL", model),
        client=client,
        vertex=True,
        project=project,
        location=env.get("GOOGLE_CLOUD_LOCATION") or "global",
        temperature=_temperature(env, "LLM_TEMPERATURE"),
    )


def _build_scripted(env: Mapping[str, str], model: str | None, client: Any) -> Any:
    from harbor_llm.providers.scripted import scripted_from_env

    return client if client is not None else scripted_from_env(env)


BUILDERS: dict[str, Callable[[Mapping[str, str], str | None, Any], Any]] = {
    "ANTHROPIC": _build_anthropic,
    "AZURE": _build_azure,
    "OPENAI": _build_openai,
    "OLLAMA": _build_ollama,
    "OPENAI_COMPATIBLE": _build_compatible,
    "BEDROCK": _build_bedrock,
    "GEMINI": _build_gemini,
    "VERTEX": _build_vertex,
    "SCRIPTED": _build_scripted,
}


def make_llm(
    provider: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    override_var: str | None = None,
    model: str | None = None,
    client: Any = None,
) -> Any:
    """Build the LLM for ``provider``, or for the app override variable, or for ``DEFAULT_LLM_PROVIDER``.

    ``client`` injects a ready SDK client (tests, custom HTTP settings); ``model`` overrides the model setting.
    """
    env = os.environ if env is None else env
    name = resolve_provider(env, override_var, provider)
    try:
        return BUILDERS[name](env, model, client)
    except ConfigError as exc:
        raise ConfigError(f"LLM provider {name}: {exc}") from exc


def describe(llm: Any) -> dict[str, str]:
    """Provider and model, safe to show on a health endpoint (no keys or endpoints)."""
    return {"provider": str(getattr(llm, "provider", "?")), "model": str(getattr(llm, "model", "?"))}
