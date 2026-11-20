"""The HTTP service: events, episodes, health, and the token-guarded sandbox endpoints."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from expense_agent import service
from expense_agent.agent import inprocess_connector
from expense_agent.config import Settings
from expense_agent.policy import RulesLLM

SUBMITTED = {"type": "expense.submitted", "report_id": "ER-3001"}


@pytest.fixture
def client(settings):
    settings.sandbox_token = "tok"
    return TestClient(service.create_app(settings, RulesLLM(), inprocess_connector(settings)))


def report_status(settings, rid):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute("SELECT status FROM reports WHERE id=?", (rid,)).fetchone()[0]
    finally:
        con.close()


def test_event_round_trip_and_episode_lookup(client):
    r = client.post("/events", json={"event": SUBMITTED}, headers={"X-Principal": "expense-bot"})
    assert r.status_code == 200
    ep = r.json()
    assert ep["route"] == "expense_review" and ep["done"] and len(ep["steps"]) == 5
    assert set(ep) == {
        "episode_id",
        "event",
        "principal",
        "route",
        "reason",
        "steps",
        "output",
        "done",
        "outcome",
        "error",
        "usage",
        "started",
        "ended",
        "llm",
    }
    assert set(ep["steps"][0]) == {"kind", "name", "args", "ok", "result", "read_only", "access_denied", "id"}
    assert client.get(f"/episodes/{ep['episode_id']}").json() == ep
    assert client.get("/episodes/nope").status_code == 404


def test_event_validation(client):
    assert client.post("/events", json={"event": {"type": "x"}}).status_code == 400
    assert client.post("/events", json={"event": {}}, headers={"X-Principal": "a"}).status_code == 422
    assert client.post("/events", json={}, headers={"X-Principal": "a"}).status_code == 422


def test_old_episodes_are_evicted(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_EPISODES", 1)
    ids = [
        client.post("/events", json={"event": {"type": "travel.booked"}}, headers={"X-Principal": "a"}).json()[
            "episode_id"
        ]
        for _ in range(2)
    ]
    assert client.get(f"/episodes/{ids[0]}").status_code == 404 and client.get(f"/episodes/{ids[1]}").status_code == 200


def test_health_reports_the_llm(client):
    assert client.get("/healthz").json() == {"status": "ok", "llm": {"provider": "SCRIPTED", "model": "expense-rules"}}


def test_health_survives_a_misconfigured_llm_and_builds_one_lazily(settings, monkeypatch):
    monkeypatch.delenv("EXPENSE_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "nonsense")
    broken = TestClient(service.create_app(settings))
    assert broken.get("/healthz").json()["llm"]["provider"] == "unconfigured"
    monkeypatch.setenv("EXPENSE_LLM_PROVIDER", "SCRIPTED")
    monkeypatch.setenv("LLM_SCRIPT", "expense_agent.policy:factory")
    lazy = TestClient(service.create_app(settings, connect=inprocess_connector(settings)))
    assert lazy.get("/healthz").json()["llm"]["model"] == "expense-rules"


def test_sandbox_endpoints_need_the_token(client, settings):
    assert client.get("/sandbox/status").status_code == 401
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "wrong"}).status_code == 401
    status = client.get("/sandbox/status", headers={"X-Sandbox-Token": "tok"}).json()
    assert status["sandbox"] and status["today"] == "2026-11-03" and len(status["prompts"]) == 12
    assert len(status["tools"]) == 12 and status["llm"]["model"] == "expense-rules"
    client.post("/events", json={"event": SUBMITTED}, headers={"X-Principal": "expense-bot"})
    notices = settings.out_dir / "notices"
    assert list(notices.glob("*.txt")) and report_status(settings, "ER-3001") == "approved"
    reset = client.post("/sandbox/reset", headers={"X-Sandbox-Token": "tok"}, json={"fixture": "seed"})
    assert reset.json() == {"status": "reset"}
    assert not list(notices.glob("*.txt")) and notices.is_dir() and report_status(settings, "ER-3001") == "submitted"
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "tok"}, content=b"not json").status_code == 200
    assert client.get("/sandbox/status", headers={"X-Sandbox-Token": "tok"}).json()["episodes"] == 0


def test_sandbox_disabled_without_a_configured_token(settings):
    c = TestClient(service.create_app(settings, RulesLLM()))
    assert c.get("/sandbox/status", headers={"X-Sandbox-Token": ""}).status_code == 401
    assert c.post("/sandbox/reset").status_code == 401


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("EXPENSE_TODAY", "2027-01-02")
    monkeypatch.setenv("EXPENSE_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("EXPENSE_OUT", str(tmp_path / "o"))
    monkeypatch.setenv("EXPENSE_MAX_STEPS", "4")
    s = Settings()
    assert s.today == date(2027, 1, 2) and s.db_path == tmp_path / "x.db" and s.max_steps == 4
    assert s.out_dir == tmp_path / "o"
    monkeypatch.delenv("EXPENSE_TODAY")
    monkeypatch.delenv("EXPENSE_MAX_STEPS")
    assert Settings().today == date(2026, 11, 3) and Settings().max_steps == 12
