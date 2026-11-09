"""The MCP tool server: tool metadata, error mapping and audit, and the HTTP router (token, principal, health)."""

from __future__ import annotations

import socket
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from typing import Any

import pytest
import uvicorn
from mcp import Client

from claims_agent import tools_server
from claims_agent.agent import ClaimsAgent, McpToolSession, http_connector
from claims_agent.backend import ClaimsBackend
from claims_agent.policy import RulesLLM
from claims_agent.tools_server import PrincipalRouter, build_server


async def test_listing_declares_kind_and_permission_and_errors_are_audited(settings):
    server = build_server(ClaimsBackend(settings.db_path, settings.letters_dir, "adjuster-07", settings.today))
    async with Client(server) as client:
        session = McpToolSession(client)
        tools = {t["name"]: t for t in await session.list_tools()}
        assert sorted(tools) == [
            "add_note",
            "get_claim",
            "list_documents",
            "make_payment",
            "update_claim",
            "write_letter",
        ]
        assert session.meta["get_claim"] == {"kind": "read", "scopes": ["claims:read"]}
        assert session.meta["make_payment"] == {"kind": "write", "scopes": ["payments:write"]}
        assert tools["update_claim"]["input_schema"]["properties"]["status"]["enum"][0] == "under_review"
        ok = await session.call("list_documents", {"claim_id": "CLM-5003"})
        assert ok.ok and "DOC-77" in ok.result
        denied = await session.call("make_payment", {"claim_id": "CLM-5001", "amount": 1})
        assert not denied.ok and denied.access_denied and denied.result.startswith("ACCESS_DENIED")
        bad = await session.call("get_claim", {"claim_id": "CLM-0"})
        assert not bad.ok and not bad.access_denied and "not found" in bad.result
        await session.call("add_note", {"claim_id": "CLM-5003", "text": "x"})
        await session.call("write_letter", {"claim_id": "CLM-5003", "kind": "info_request", "body": "Send photos."})
        await session.call("update_claim", {"claim_id": "CLM-5003", "status": "under_review"})
    con = sqlite3.connect(settings.db_path)
    outcomes = con.execute("SELECT operation, outcome FROM audit_log ORDER BY id").fetchall()
    con.close()
    assert outcomes[:3] == [("list_documents", "ok"), ("make_payment", "denied"), ("get_claim", "error")]


class _Structured:
    """A call result with structured content only (exercises the JSON fallback)."""

    def __init__(self) -> None:
        self.content: list[Any] = []
        self.structured_content = {"a": 1}
        self.is_error = False


async def test_structured_only_results_are_serialised():
    class FakeClient:
        async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
            return _Structured()

    call = await McpToolSession(FakeClient()).call("get_claim", {})
    assert call.ok and call.result == '{"a": 1}'


@pytest.fixture
def http_server(settings):
    settings.tools_token = "s3cret"
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    server = uvicorn.Server(
        uvicorn.Config(PrincipalRouter(settings), host="127.0.0.1", port=port, log_level="warning", lifespan="on")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        time.sleep(0.05)
    settings.tools_url = f"http://127.0.0.1:{port}/mcp"
    yield settings
    server.should_exit = True
    thread.join(timeout=5)


def _status(url: str, headers: dict[str, str]) -> int:
    req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", **headers})
    try:
        return urllib.request.urlopen(req).status
    except urllib.error.HTTPError as exc:
        return exc.code


async def test_http_router_auth_principal_and_a_full_episode(http_server):
    s = http_server
    base = s.tools_url.rsplit("/", 1)[0]
    assert urllib.request.urlopen(base + "/healthz").read() == b'{"status": "ok"}'
    assert _status(s.tools_url, {"X-Principal": "adjuster-07"}) == 401
    assert _status(s.tools_url, {"Authorization": "Bearer s3cret"}) == 400
    assert _status(s.tools_url, {"Authorization": "Bearer s3cret", "X-Principal": "mallory"}) == 403
    ep = await ClaimsAgent(RulesLLM(), s, http_connector(s)).handle(
        {"type": "claim.document_received", "claim_id": "CLM-5003", "document_id": "DOC-77"}, "adjuster-07"
    )
    assert ep.done and [st["name"] for st in ep.steps] == ["get_claim", "update_claim", "write_letter"]
    again = await ClaimsAgent(RulesLLM(), s, http_connector(s)).handle(
        {"type": "claim.document_received", "claim_id": "CLM-5003"}, "adjuster-07"
    )  # cached per-principal server
    assert [st["name"] for st in again.steps] == ["get_claim", "add_note"]


async def test_router_ignores_non_http_scopes_and_can_forget(settings):
    router = PrincipalRouter(settings)
    await router({"type": "websocket"}, None, None)
    router.apps["x"] = object()
    router.forget()
    assert router.apps == {}


def test_main_initialises_the_database_and_serves(settings, monkeypatch, tmp_path):
    served: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    monkeypatch.setenv("CLAIMS_DB", str(tmp_path / "fresh" / "c.db"))
    monkeypatch.setenv("CLAIMS_LETTERS", str(tmp_path / "fresh" / "letters"))
    tools_server.main(["--host", "0.0.0.0", "--port", "9200"])
    assert (tmp_path / "fresh" / "c.db").exists() and isinstance(served["app"], PrincipalRouter)
    assert served["port"] == 9200 and served["host"] == "0.0.0.0"
    tools_server.main([])  # database already there: served without re-seeding
    assert served["port"] == 9100


async def test_requests_with_an_unlisted_host_header_are_refused(http_server):
    """DNS-rebinding protection stays on: only the configured host names are served."""
    status = _status(
        http_server.tools_url,
        {"Authorization": "Bearer s3cret", "X-Principal": "adjuster-07", "Host": "evil.example.com"},
    )
    assert status == 421


def test_allowed_hosts_from_environment(monkeypatch):
    from claims_agent.config import Settings

    monkeypatch.setenv("CLAIMS_TOOLS_ALLOWED_HOSTS", "proxy:*, claims-tools:*,")
    assert Settings().tools_allowed_hosts == ["proxy:*", "claims-tools:*"]
