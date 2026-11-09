from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from claims_agent.backend import init_db
from claims_agent.config import Settings

TODAY = date(2026, 11, 3)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.db_path, s.letters_dir, s.today = tmp_path / "claims.db", tmp_path / "letters", TODAY
    s.tools_token, s.sandbox_token, s.max_steps = "", "", 10
    init_db(s.db_path, s.letters_dir, TODAY)
    return s
