"""The HTTP service: events, episodes, health, and the token-guarded sandbox endpoints."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from claims_agent import service
from claims_agent.agent import inprocess_connector
from claims_agent.config import Settings
from claims_agent.policy import RulesLLM


@pytest.fixture
def client(settings):
    settings.sandbox_token = "tok"
    return TestClient(service.create_app(settings, RulesLLM(), inprocess_connector(settings)))


def test_event_round_trip_and_episode_lookup(client):
    r = client.post(
        "/events",
        json={"event": {"type": "claim.document_received", "claim_id": "CLM-5003", "document_id": "DOC-77"}},
        headers={"X-Principal": "adjuster-07"},
    )
    assert r.status_code == 200
    ep = r.json()
    assert ep["route"] == "claims_intake" and ep["done"] and len(ep["steps"]) == 3
    assert client.get(f"/episodes/{ep['episode_id']}").json() == ep
    assert client.get("/episodes/nope").status_code == 404


def test_event_validation(client):
    assert client.post("/events", json={"event": {"type": "x"}}).status_code == 400
    assert client.post("/events", json={"event": {}}, headers={"X-Principal": "a"}).status_code == 422
    assert client.post("/events", json={}, headers={"X-Principal": "a"}).status_code == 422


def test_old_episodes_are_evicted(client, monkeypatch):
    monkeypatch.setattr(service, "MAX_EPISODES", 1)
    ids = [
        client.post("/events", json={"event": {"type": "policy.renewed"}}, headers={"X-Principal": "a"}).json()[
            "episode_id"
        ]
        for _ in range(2)
    ]
    assert client.get(f"/episodes/{ids[0]}").status_code == 404 and client.get(f"/episodes/{ids[1]}").status_code == 200


def test_health_reports_the_llm(client):
    assert client.get("/healthz").json() == {
        "status": "ok",
        "llm": {"provider": "SCRIPTED", "model": "claims-rules"},
        "engine": "loop",
    }


def test_health_survives_a_misconfigured_llm_and_builds_one_lazily(settings, monkeypatch):
    monkeypatch.delenv("CLAIMS_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "nonsense")
    broken = TestClient(service.create_app(settings))
    assert broken.get("/healthz").json()["llm"]["provider"] == "unconfigured"
    monkeypatch.setenv("CLAIMS_LLM_PROVIDER", "SCRIPTED")
    monkeypatch.setenv("LLM_SCRIPT", "claims_agent.policy:factory")
    lazy = TestClient(service.create_app(settings, connect=inprocess_connector(settings)))
    assert lazy.get("/healthz").json()["llm"]["model"] == "claims-rules"


def test_sandbox_endpoints_need_the_token(client, settings):
    assert client.get("/sandbox/status").status_code == 401
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "wrong"}).status_code == 401
    status = client.get("/sandbox/status", headers={"X-Sandbox-Token": "tok"}).json()
    assert status["sandbox"] and status["today"] == "2026-11-03" and len(status["prompts"]) == 12
    client.post(
        "/events",
        json={"event": {"type": "claim.document_received", "claim_id": "CLM-5003"}},
        headers={"X-Principal": "adjuster-07"},
    )
    assert list(settings.letters_dir.glob("*.txt"))
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "tok"}).json() == {"status": "reset"}
    assert not list(settings.letters_dir.glob("*.txt"))
    assert client.get("/sandbox/status", headers={"X-Sandbox-Token": "tok"}).json()["episodes"] == 0


def test_sandbox_disabled_without_a_configured_token(settings):
    c = TestClient(service.create_app(settings, RulesLLM()))
    assert c.get("/sandbox/status", headers={"X-Sandbox-Token": ""}).status_code == 401


def test_settings_from_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAIMS_TODAY", "2027-01-02")
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "x.db"))
    monkeypatch.setenv("CLAIMS_MAX_STEPS", "4")
    s = Settings()
    assert s.today == date(2027, 1, 2) and s.db_path == tmp_path / "x.db" and s.max_steps == 4
    monkeypatch.delenv("CLAIMS_TODAY")
    assert Settings().today == date(2026, 11, 3)
