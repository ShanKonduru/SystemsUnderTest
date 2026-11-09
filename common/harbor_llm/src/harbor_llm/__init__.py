"""harbor_llm: one LLM interface for the Harbor Mutual demo apps, over any provider.

from harbor_llm import make_llm
llm = make_llm()            # provider from DEFAULT_LLM_PROVIDER in .env
reply = await llm.create(messages=[...], system="...", tools=[...], max_tokens=4096)
plan = await llm.structured(system="...", user="...", schema=MyPydanticModel)
"""

from harbor_llm.factory import ALIASES, BUILDERS, describe, make_llm, resolve_provider
from harbor_llm.types import (
    LLM,
    ConfigError,
    LLMResponse,
    StructuredOutputError,
    TextBlock,
    ToolUseBlock,
    Usage,
    strict_schema,
)

__version__ = "0.1.0"

__all__ = [
    "ALIASES",
    "BUILDERS",
    "LLM",
    "ConfigError",
    "LLMResponse",
    "StructuredOutputError",
    "TextBlock",
    "ToolUseBlock",
    "Usage",
    "describe",
    "make_llm",
    "resolve_provider",
    "strict_schema",
]
