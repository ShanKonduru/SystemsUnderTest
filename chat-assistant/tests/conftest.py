from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import pytest

from insurance_copilot.backend.db import init_db
from insurance_copilot.config import Settings
from insurance_copilot.core.trace import Entity, IntentResult, Plan, PlanStep

TODAY = date(2026, 11, 3)
# the demo users' password for tests (the app reads it when it is imported; a real one lives in .env)
os.environ.setdefault("DEMO_USER_PASSWORD", "test-demo-password")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    init_db(path, TODAY)
    return path


@pytest.fixture
def settings(db: Path) -> Settings:
    s = Settings()
    s.db_path, s.today, s.mcp_transport, s.max_tool_steps = db, TODAY, "inprocess", 5
    return s


def intent(*names: str, clarify: str = "", **entities: str) -> IntentResult:
    return IntentResult(
        primary_intent=names[0],
        intents=list(names),
        entities=[Entity(name=k, value=v) for k, v in entities.items()],
        needs_clarification=bool(clarify),
        clarification_question=clarify,
        confidence="high",
    )


def plan(*actions: str) -> Plan:
    return Plan(goal="test", steps=[PlanStep(action=a, purpose="test", depends_on=[]) for a in actions])
