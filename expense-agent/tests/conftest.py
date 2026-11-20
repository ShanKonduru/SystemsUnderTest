from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from expense_agent.backend import init_db
from expense_agent.config import Settings

TODAY = date(2026, 11, 3)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    s = Settings()
    s.db_path, s.out_dir, s.today = tmp_path / "expense.db", tmp_path / "out", TODAY
    s.tools_token, s.sandbox_token, s.max_steps, s.defects = "", "", 12, frozenset()
    init_db(s.db_path, s.out_dir, TODAY)
    return s
