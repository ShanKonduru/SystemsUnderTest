"""The assistant's LLM, built by the shared Harbor Mutual LLM factory (``harbor_llm``).

Any provider works: the factory reads ``CHAT_LLM_PROVIDER`` (this app only) or ``DEFAULT_LLM_PROVIDER`` from the
environment / ``.env`` and returns an object with ``create()`` (one tool-using model turn, Anthropic-style
messages and blocks) and ``structured()`` (a Pydantic-validated JSON answer). Tests inject a scripted fake.
"""

from __future__ import annotations

from typing import Any

from harbor_llm import LLM, StructuredOutputError, describe, strict_schema
from harbor_llm import make_llm as _factory

from insurance_copilot.config import Settings

APP_PROVIDER_VAR = "CHAT_LLM_PROVIDER"

__all__ = ["APP_PROVIDER_VAR", "LLM", "StructuredOutputError", "describe", "make_llm", "strict_schema"]


def make_llm(settings: Settings | None = None, **kwargs: Any) -> Any:
    """Build the LLM for this app (``settings`` is accepted for call-site compatibility)."""
    return _factory(override_var=APP_PROVIDER_VAR, **kwargs)
