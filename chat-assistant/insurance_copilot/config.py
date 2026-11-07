"""Central configuration. Everything can be overridden with environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # load the nearest .env (the repo root's) for local runs; real environment variables win
    from dotenv import load_dotenv

    for _folder in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (_folder / ".env").is_file():
            load_dotenv(_folder / ".env", override=False)
            break
except ImportError:  # python-dotenv is a declared dependency; without it, use the real environment
    pass


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_command(name: str) -> list[str]:
    """A command given as a JSON list (preferred) or a plain space-separated string."""
    import json
    import shlex

    value = os.environ.get(name, "").strip()
    if not value:
        return []
    if value.startswith("["):
        return [str(x) for x in json.loads(value)]
    return shlex.split(value, posix=os.name != "nt")


@dataclass
class Settings:
    # The LLM provider and model are chosen by the shared factory (harbor_llm) from CHAT_LLM_PROVIDER or
    # DEFAULT_LLM_PROVIDER and that provider's settings; see the repo root's .env.example.
    # Effort per stage (used by providers that support it, e.g. Claude): routing/planning are simple, execution needs a bit more reasoning.
    router_effort: str = field(default_factory=lambda: os.environ.get("COPILOT_ROUTER_EFFORT", "low"))
    planner_effort: str = field(default_factory=lambda: os.environ.get("COPILOT_PLANNER_EFFORT", "low"))
    executor_effort: str = field(default_factory=lambda: os.environ.get("COPILOT_EXECUTOR_EFFORT", "medium"))
    max_tool_steps: int = field(default_factory=lambda: int(os.environ.get("COPILOT_MAX_STEPS", "12")))
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("COPILOT_DB", PROJECT_ROOT / "data" / "insurance.db"))
    )
    # "stdio" launches the MCP server as a subprocess (real protocol); "inprocess" is used by tests.
    mcp_transport: str = field(default_factory=lambda: os.environ.get("COPILOT_MCP_TRANSPORT", "stdio"))
    # Optional command that starts the MCP tool server (stdio), e.g. through a test team's recording proxy.
    mcp_command: list[str] = field(default_factory=lambda: _env_command("COPILOT_MCP_COMMAND"))
    # Optional URL of a tool server over MCP streamable HTTP (e.g. http://127.0.0.1:9000/mcp). When set, the app
    # connects there instead of starting a stdio server, naming the signed-in customer in an X-Customer-Id header.
    mcp_url: str = field(default_factory=lambda: os.environ.get("COPILOT_MCP_URL", ""))
    # Optional base URL of the tools as a REST API (insurance_copilot.rest_api, e.g. http://127.0.0.1:9200/v1). When set,
    # the app calls it over HTTP instead of MCP, naming the signed-in customer in an X-Customer-Id header.
    rest_url: str = field(default_factory=lambda: os.environ.get("COPILOT_REST_URL", ""))
    # Set by a sandbox whose tool traffic goes through a recording proxy that the app can't see (HTTP mode).
    tool_proxy: bool = field(default_factory=lambda: _env_bool("COPILOT_TOOL_PROXY", False))
    # Transaction policy for self-service customers (backend/policy.py): "direct" lets customers complete
    # every operation; "review" sends purchases, coverage/driver/rider changes, cancellations and
    # date-of-birth corrections to staff review instead of applying them.
    txn_policy: str = field(default_factory=lambda: os.environ.get("COPILOT_TXN_POLICY", "direct"))
    # Sandbox mode: exposes /sandbox/status and /sandbox/reset for an external test harness.
    sandbox_mode: bool = field(default_factory=lambda: _env_bool("COPILOT_SANDBOX", False))
    sandbox_token: str = field(default_factory=lambda: os.environ.get("COPILOT_SANDBOX_TOKEN", ""))
    # Fixed "today" makes relative dates ("yesterday") deterministic in tests.
    today: date = field(
        default_factory=lambda: date.fromisoformat(os.environ["COPILOT_TODAY"])
        if os.environ.get("COPILOT_TODAY")
        else date.today()
    )

