"""Tool server endpoints, the agent services, and a real end-to-end run over HTTP (four local servers)."""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
from typing import Any

import httpx
import pytest
import uvicorn
from fastapi import HTTPException
from fastapi.testclient import TestClient

from desk import identity as idmod
from desk import service, tools_server
from desk.config import Settings
from desk.policy import DeskRules
from desk.tools_server import IdentityRouter, create_app, proof

# ------------------------------------------------------------------ tool server REST side


def test_tool_server_endpoints(settings):
    settings.sandbox_token = "tok"
    with TestClient(create_app(settings)) as c:
        key = settings.signing_key()
        assert c.get("/healthz").json() == {"status": "ok"}
        assert c.get("/identity/M-1001").status_code == 401
        assert c.get("/identity/M-9", headers={"X-Desk-Proof": proof(key, "M-9")}).status_code == 404
        tok = c.get("/identity/M-1001", headers={"X-Desk-Proof": proof(key, "M-1001")}).json()["identity"]
        assert idmod.verify(tok, key)["permissions"] == ["billing:read", "cases:write", "claims:read"]
        assert c.get("/sandbox/status").status_code == 401
        h = {"X-Sandbox-Token": "tok"}
        assert (
            c.post("/sandbox/faults", json={"faults": {"get_billing": {"mode": "boom"}}}, headers=h).status_code == 422
        )
        set_ = c.post("/sandbox/faults", json={"faults": {"get_billing": {"mode": "timeout", "times": 2}}}, headers=h)
        assert set_.json() == {"faults": {"get_billing": {"mode": "timeout", "times": 2}}}
        got = c.get("/sandbox/status", headers=h).json()
        assert got["faults"]["get_billing"]["times"] == 2
        assert len(got["prompts"]) == 12 and len(got["tools"]) == 12  # fingerprints for TruAssure's comparisons
        assert c.post("/sandbox/reset", headers=h).json() == {"status": "reset"}
        assert c.get("/sandbox/status", headers=h).json()["faults"] == {}
        assert c.post("/mcp", json={}, headers={"X-Identity": "forged.token"}).status_code == 403


def test_create_app_seeds_a_fresh_data_folder_and_key(tmp_path, monkeypatch):
    monkeypatch.delenv("DESK_SIGNING_KEY", raising=False)
    s = Settings()
    s.data_dir, s.signing_key_env = tmp_path / "fresh", ""
    assert isinstance(create_app(s), IdentityRouter)
    assert s.db_path.exists() and len(s.signing_key()) > 20
    s2 = Settings()
    s2.data_dir, s2.signing_key_env = tmp_path / "empty", ""
    assert s2.signing_key() == ""  # agents never create the key: only the tool server does
    s2.signing_key_env = "from-env"
    assert s2.signing_key() == "from-env"


def test_main_serves_the_router(settings, monkeypatch):
    served: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    monkeypatch.setenv("DESK_DATA", str(settings.data_dir))
    tools_server.main(["--port", "9400"])
    assert isinstance(served["app"], IdentityRouter) and served["port"] == 9400 and served["host"] == "127.0.0.1"


# ------------------------------------------------------------------ agent services (in-process wiring)


@pytest.fixture
def triage(settings, wiring):
    async def identity_for(member: str) -> str:
        if member == "M-9":
            raise HTTPException(status_code=403, detail="unknown member M-9")
        return wiring["token_for"](member)

    return TestClient(
        service.create_triage_app(settings, DeskRules(), wiring["connect"], wiring["handoff"], identity_for)
    )


def test_triage_service_round_trip(triage):
    r = triage.post("/requests", json={"message": "How much do I owe on POL-1002?"}, headers={"X-Principal": "M-1001"})
    ep = r.json()
    assert r.status_code == 200 and ep["route"] == "billing" and ep["done"] and "$193.60" in ep["output"]
    assert ep["hops"][0]["chain"] == ["triage", "billing"] and ep["llm"]["model"] == "desk-rules"
    assert triage.get(f"/episodes/{ep['episode_id']}").json() == ep
    assert triage.get("/episodes/nope").status_code == 404
    assert triage.post("/requests", json={"message": "x"}).status_code == 400
    assert triage.post("/requests", json={"message": "x"}, headers={"X-Principal": "M-9"}).status_code == 403
    unsupported = triage.post("/requests", json={"message": "Change my address"}, headers={"X-Principal": "M-1001"})
    assert unsupported.json()["route"] == "unsupported" and unsupported.json()["steps"] == []


def test_triage_service_reports_errors_and_evicts(settings, wiring, monkeypatch):
    class Broken(DeskRules):
        async def structured(self, **_: Any) -> Any:
            raise ExceptionGroup("tg", [ConnectionError("model unreachable")])

    async def identity_for(member: str) -> str:
        return wiring["token_for"](member)

    c = TestClient(service.create_triage_app(settings, Broken(), wiring["connect"], wiring["handoff"], identity_for))
    ep = c.post("/requests", json={"message": "x"}, headers={"X-Principal": "M-1001"}).json()
    assert ep["status"] == "failed" and ep["error"] == "ConnectionError: model unreachable"
    monkeypatch.setattr(service, "MAX_EPISODES", 1)
    second = c.post("/requests", json={"message": "x"}, headers={"X-Principal": "M-1001"}).json()
    assert c.get(f"/episodes/{ep['episode_id']}").status_code == 404
    assert c.get(f"/episodes/{second['episode_id']}").status_code == 200


def test_specialist_service_checks_the_identity(settings, wiring):
    c = TestClient(service.create_specialist_app("claims", settings, DeskRules(), wiring["connect"], wiring["handoff"]))
    key = settings.signing_key()
    base = wiring["token_for"]("M-1001")
    assert c.post("/tasks", json={"task": "x"}, headers={"X-Identity": "bad"}).status_code == 401
    assert c.post("/tasks", json={"task": "x"}, headers={"X-Identity": base}).status_code == 403  # chain ends triage
    ok = c.post(
        "/tasks", json={"task": "Explain claim CLM-5003."}, headers={"X-Identity": idmod.delegate(base, key, "claims")}
    ).json()
    assert ok["status"] == "ok" and ok["steps"][0]["chain"] == ["triage", "claims"]


def test_specialist_service_turns_crashes_into_a_failed_reply(settings, wiring):
    class Crash(DeskRules):
        async def create(self, **_: Any) -> Any:
            raise ExceptionGroup("tg", [TimeoutError("model timed out")])

    c = TestClient(service.create_specialist_app("claims", settings, Crash(), wiring["connect"], wiring["handoff"]))
    token = idmod.delegate(wiring["token_for"]("M-1001"), settings.signing_key(), "claims")
    out = c.post("/tasks", json={"task": "x"}, headers={"X-Identity": token}).json()
    assert out["status"] == "failed" and "TimeoutError: model timed out" in out["output"]


def test_health_reports_the_llm_or_a_misconfiguration(settings, monkeypatch):
    monkeypatch.delenv("DESK_LLM_PROVIDER", raising=False)
    monkeypatch.setenv("DEFAULT_LLM_PROVIDER", "nonsense")
    assert TestClient(service.billing_app()).get("/healthz").json()["llm"]["provider"] == "unconfigured"
    monkeypatch.setenv("DESK_LLM_PROVIDER", "SCRIPTED")
    monkeypatch.setenv("LLM_SCRIPT", "desk.policy:factory")
    assert TestClient(service.claims_app()).get("/healthz").json() == {
        "status": "ok",
        "agent": "claims",
        "llm": {"provider": "SCRIPTED", "model": "desk-rules"},
    }
    assert TestClient(service.triage_app()).get("/healthz").json()["agent"] == "triage"


# ------------------------------------------------------------------ end to end over HTTP


def _serve(app: Any) -> tuple[str, uvicorn.Server, threading.Thread]:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    return f"http://127.0.0.1:{port}", server, thread


@pytest.fixture
def stack(settings, monkeypatch):
    monkeypatch.setenv("DESK_LLM_PROVIDER", "SCRIPTED")
    monkeypatch.setenv("LLM_SCRIPT", "desk.policy:factory")
    settings.sandbox_token = "tok"
    running = []
    url, *rest = _serve(create_app(settings))
    running.append(rest)
    settings.tools_url = url
    for role in ("billing", "claims"):
        u, *r = _serve(service.create_specialist_app(role, settings))
        running.append(r)
        setattr(settings, f"{role}_url", u)
    yield settings
    for server, thread in running:
        server.should_exit = True
        thread.join(timeout=5)


def test_end_to_end_over_http_with_a_cascading_failure(stack):
    triage = TestClient(service.create_triage_app(stack))
    ask = {"message": "Why was my claim CLM-5003 payment lower than my bill for POL-1002?"}
    ep = triage.post("/requests", json=ask, headers={"X-Principal": "M-1001"}).json()
    assert ep["route"] == "both" and ep["done"] and ep["status"] == "ok"
    assert [h["chain"] for h in ep["hops"]] == [["triage", "billing"], ["triage", "billing", "claims"]]
    assert "deductible" in ep["output"]
    httpx.post(
        f"{stack.tools_url}/sandbox/faults",
        headers={"X-Sandbox-Token": "tok"},
        json={"faults": {"get_billing": {"mode": "error"}}},
    )
    failed = triage.post("/requests", json=ask, headers={"X-Principal": "M-1001"}).json()
    assert failed["status"] == "failed" and "193.60" not in failed["output"] and len(failed["hops"]) == 1
    con = sqlite3.connect(stack.db_path)
    chains = {r[0] for r in con.execute("SELECT chain FROM audit_log")}
    con.close()
    assert chains == {'["triage", "billing"]', '["triage", "billing", "claims"]'}
    assert triage.post("/requests", json=ask, headers={"X-Principal": "M-9"}).status_code == 403


def test_a_broken_specialist_is_reported_as_failed(stack):
    stack.claims_url = stack.tools_url  # wrong service: /tasks does not exist there
    triage = TestClient(service.create_triage_app(stack))
    ep = triage.post(
        "/requests", json={"message": "Status of claim CLM-5003?"}, headers={"X-Principal": "M-1001"}
    ).json()
    assert ep["status"] == "failed" and ep["hops"][0]["status"] == "failed"
    assert "couldn't" in ep["output"]


async def test_every_tool_and_fault_mode_through_mcp(settings, faults):
    from mcp import Client

    from desk.agents import McpTools
    from desk.backend import DeskBackend, Identity
    from desk.tools_server import build_server

    ident = Identity("M-1001", frozenset({"billing:read", "claims:read", "cases:write"}), ("triage", "billing"))
    async with Client(build_server(DeskBackend(settings.db_path, ident, settings.today), faults, 0)) as client:
        tools = McpTools(client)
        listed = {t["name"] for t in await tools.list_tools(("list_payments", "make_payment", "get_billing"))}
        assert listed == {"list_payments", "make_payment", "get_billing"}
        assert tools.meta["make_payment"] == {"kind": "write", "scopes": ["billing:write"]}
        assert "PAY-8801" in (await tools.call("list_payments", {"policy_id": "POL-1002"})).result
        paid = await tools.call("make_payment", {"policy_id": "POL-1002", "amount": 5})
        assert not paid.ok and paid.access_denied
        missing = await tools.call("get_billing", {"policy_id": "POL-0"})
        assert not missing.ok and "not found" in missing.result
        faults.set({"get_billing": {"mode": "empty"}})
        empty = await tools.call("get_billing", {"policy_id": "POL-1002"})
        assert empty.ok and "193.6" not in empty.result


def test_per_identity_servers_are_cached_and_evicted(stack, monkeypatch):
    monkeypatch.setattr(tools_server, "MAX_SERVERS", 1)
    triage = TestClient(service.create_triage_app(stack))
    for member in ("M-1001", "M-2201"):
        ep = triage.post("/requests", json={"message": "Which claims do I have?"}, headers={"X-Principal": member})
        assert ep.json()["status"] == "ok", ep.json()


def test_identity_lookup_escapes_the_member_id(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only a well-formed member id reaches the tools URL, as one escaped path segment: "../" or "?" cannot reach
    another endpoint."""
    import asyncio

    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.raw_path.decode())
        if "unknown" in request.url.raw_path.decode():
            return httpx.Response(404)
        return httpx.Response(200, json={"identity": "token"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(answer), **kw))
    identity_for = service.http_identity(settings)
    assert asyncio.run(identity_for("M-1001")) == "token"
    for malformed in ("../admin", "M-1001?drop=1", "M 1001", ""):
        with pytest.raises(HTTPException) as err:
            asyncio.run(identity_for(malformed))
        assert err.value.status_code == 403 and err.value.detail == "malformed member id"
    assert seen == ["/identity/M-1001"]  # nothing malformed was sent
    with pytest.raises(HTTPException) as err:
        asyncio.run(identity_for("unknown"))
    assert err.value.status_code == 403 and seen[-1] == "/identity/unknown"
