from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from automation_lab.app import create_app
from automation_lab.config import Settings

TODAY = date(2026, 11, 3)
TOKEN = "test-sandbox-token"


def make_settings(tmp_path: Path, **overrides: Any) -> Settings:
    s = Settings()
    s.data_dir, s.today, s.release, s.sandbox_token = tmp_path / "data", TODAY, "v1", TOKEN
    s.latency_ms, s.max_upload_bytes, s.defects = 0, 1024, frozenset()
    for k, v in overrides.items():
        setattr(s, k, v)
    return s


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def quote(**overrides: Any) -> dict[str, Any]:
    body = {
        "product": "auto", "coverage": "standard", "deductible": 1000, "addons": ["roadside"],
        "first_name": "Shan", "last_name": "Mercer", "date_of_birth": "1990-05-01", "email": "shan@example.com",
        "phone": "(555) 010-2030", "zip": "02110", "start_date": "2026-11-10", "payment_plan": "annual",
        "paperless": True, "multi_policy": True, "notes": "", "consent": True,
    }
    return {**body, **overrides}


def claim(**overrides: Any) -> dict[str, Any]:
    body = {
        "policy_number": "POL-1001", "incident_type": "collision", "incident_date": "2026-10-30",
        "location": "Main St, Boston", "description": "Rear-ended at a stop light, bumper and trunk damaged.",
        "police_report": True, "report_number": "BPD-7781", "estimate": 2500, "injuries": False,
        "contact": "email", "available_days": ["mon"], "upload_ids": [], "signature": "Shan Mercer", "agree": True,
    }
    return {**body, **overrides}
