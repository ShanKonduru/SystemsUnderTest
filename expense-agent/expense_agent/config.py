"""Settings, all from the environment (and the nearest .env, which is the repo root's in local runs)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from expense_agent.defects import parse as parse_defects

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # local runs: load the nearest .env; real environment variables win
    from dotenv import load_dotenv

    for _folder in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (_folder / ".env").is_file():
            load_dotenv(_folder / ".env", override=False)
            break
except ImportError:  # python-dotenv is a declared dependency; without it, use the real environment
    pass


def _today() -> date:
    value = os.environ.get("EXPENSE_TODAY", "").strip()
    return date.fromisoformat(value) if value else date(2026, 11, 3)


@dataclass
class Settings:
    db_path: Path = field(
        default_factory=lambda: Path(os.environ.get("EXPENSE_DB") or PROJECT_ROOT / "data" / "expense.db")
    )
    # notices are written as text files under <out_dir>/notices/
    out_dir: Path = field(default_factory=lambda: Path(os.environ.get("EXPENSE_OUT") or PROJECT_ROOT / "data" / "out"))
    today: date = field(default_factory=_today)
    # Where the agent reaches the tools (MCP over streamable HTTP); a recording proxy can sit in between.
    tools_url: str = field(default_factory=lambda: os.environ.get("EXPENSE_TOOLS_URL", "http://127.0.0.1:9700/mcp"))
    tools_token: str = field(default_factory=lambda: os.environ.get("EXPENSE_TOOLS_TOKEN", ""))
    # Host headers the tool server accepts (DNS-rebinding protection stays on); add a proxy's name if one is used.
    tools_allowed_hosts: list[str] = field(
        default_factory=lambda: [
            h.strip()
            for h in os.environ.get("EXPENSE_TOOLS_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,expense-tools:*").split(",")
            if h.strip()
        ]
    )
    sandbox_token: str = field(default_factory=lambda: os.environ.get("EXPENSE_SANDBOX_TOKEN", ""))
    max_steps: int = field(default_factory=lambda: int(os.environ.get("EXPENSE_MAX_STEPS", "12")))
    # planted defects for test runs ("buggy build"); empty in production. See expense_agent.defects.
    defects: frozenset[str] = field(default_factory=lambda: parse_defects(os.environ.get("EXPENSE_DEFECTS")))
