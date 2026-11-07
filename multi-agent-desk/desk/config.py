"""Settings, all from the environment (and the nearest .env, which is the repo root's in local runs)."""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # local runs: load the nearest .env; real environment variables win
    from dotenv import load_dotenv

    for _folder in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (_folder / ".env").is_file():
            load_dotenv(_folder / ".env", override=False)
            break
except ImportError:  # python-dotenv is a declared dependency; without it, use the real environment
    pass


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(_env("DESK_DATA") or PROJECT_ROOT / "data"))
    today: date = field(default_factory=lambda: date.fromisoformat(_env("DESK_TODAY") or "2026-11-03"))
    tools_url: str = field(default_factory=lambda: _env("DESK_TOOLS_URL", "http://127.0.0.1:9300"))
    billing_url: str = field(default_factory=lambda: _env("DESK_BILLING_URL", "http://127.0.0.1:8301"))
    claims_url: str = field(default_factory=lambda: _env("DESK_CLAIMS_URL", "http://127.0.0.1:8302"))
    sandbox_token: str = field(default_factory=lambda: _env("DESK_SANDBOX_TOKEN"))
    max_steps: int = field(default_factory=lambda: int(_env("DESK_MAX_STEPS", "8")))
    fault_delay_s: float = field(default_factory=lambda: float(_env("DESK_FAULT_DELAY", "2")))
    allowed_hosts: list[str] = field(
        default_factory=lambda: [
            h.strip()
            for h in _env("DESK_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,desk-tools:*").split(",")
            if h.strip()
        ]
    )
    signing_key_env: str = field(default_factory=lambda: _env("DESK_SIGNING_KEY"))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "desk.db"

    @property
    def key_file(self) -> Path:
        return self.data_dir / ".signing_key"

    def signing_key(self, create: bool = False) -> str:
        """DESK_SIGNING_KEY, else the key in the shared data folder (created by the tool server on first start)."""
        if self.signing_key_env:
            return self.signing_key_env
        if self.key_file.exists():
            return self.key_file.read_text(encoding="utf-8").strip()
        if not create:
            return ""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(32)
        self.key_file.write_text(key, encoding="utf-8")
        self.key_file.chmod(0o600)
        return key
