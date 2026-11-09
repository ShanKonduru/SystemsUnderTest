"""Settings, all from the environment (and the nearest .env, which is the repo root's in local runs)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from automation_lab.defects import parse as parse_defects

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:  # local runs: load the nearest .env; real environment variables win
    from dotenv import load_dotenv

    for _folder in (PROJECT_ROOT, *PROJECT_ROOT.parents):
        if (_folder / ".env").is_file():
            load_dotenv(_folder / ".env", override=False)
            break
except ImportError:  # python-dotenv is a declared dependency; without it, use the real environment
    pass

# The UI releases the page can render. Each one keeps the same behaviour but changes the locators a script
# recorded on another release would rely on (see static/release.js).
RELEASES: dict[str, str] = {
    "v1": "Baseline: stable ids, names, classes and data-testid attributes",
    "v2": "Redesign: ids, names and classes renamed, some button texts and field order changed, extra wrappers; "
    "data-testid kept",
    "v3": "Dynamic: ids, names and classes regenerated on every page load and no data-testid; only labels, roles "
    "and visible text are stable",
}


def _today() -> date:
    value = os.environ.get("LAB_TODAY", "").strip()
    return date.fromisoformat(value) if value else date(2026, 11, 3)


def _release() -> str:
    value = os.environ.get("LAB_RELEASE", "").strip().lower() or "v1"
    if value not in RELEASES:
        raise ValueError(f"unknown LAB_RELEASE {value!r}; known: {', '.join(RELEASES)}")
    return value


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LAB_DATA") or PROJECT_ROOT / "data"))
    today: date = field(default_factory=_today)
    # the release served when the page's URL doesn't ask for one (?release=v2); /sandbox/release changes it live
    release: str = field(default_factory=_release)
    sandbox_token: str = field(default_factory=lambda: os.environ.get("LAB_SANDBOX_TOKEN", ""))
    max_upload_bytes: int = field(
        default_factory=lambda: int(float(os.environ.get("LAB_MAX_UPLOAD_MB", "5")) * 1024 * 1024)
    )
    # added to every /api/policies response, so scripts have a real network wait to synchronise on
    latency_ms: int = field(default_factory=lambda: int(os.environ.get("LAB_LATENCY_MS", "300")))
    # planted defects for test runs ("buggy build"); empty in production. See automation_lab.defects.
    defects: frozenset[str] = field(default_factory=lambda: parse_defects(os.environ.get("LAB_DEFECTS")))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "lab.db"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"
