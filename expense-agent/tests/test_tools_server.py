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

from expense_agent import tools_server
from expense_agent.agent import ExpenseAgent, McpToolSession, http_connector
from expense_agent.backend import ExpenseBackend
from expense_agent.policy import RulesLLM
from expense_agent.tools_server import PrincipalRouter, build_server


async def test_listing_declares_kind_and_permission_and_errors_are_audited(settings):
    server = build_server(ExpenseBackend(settings.db_path, settings.out_dir, "expense-bot", settings.today))
    async with Client(server) as client:
        session = McpToolSession(client)
        tools = {t["name"]: t for t in await session.list_tools()}
        assert sorted(tools) == [
            "add_note",
            "find_duplicates",
            "get_expense_policy",
            "get_report",
            "make_reimbursement",
            "send_notice",
            "update_report",
        ]
        assert session.meta["get_report"] == {"kind": "read", "scopes": ["expenses:read"]}
        assert session.meta["get_expense_policy"] == {"kind": "read", "scopes": ["expenses:read"]}
        assert session.meta["find_duplicates"] == {"kind": "read", "scopes": ["expenses:read"]}
        assert session.meta["update_report"] == {"kind": "write", "scopes": ["expenses:update"]}
        assert session.meta["add_note"] == {"kind": "write", "scopes": ["expenses:update"]}
        assert session.meta["send_notice"] == {"kind": "write", "scopes": ["notices:write"]}
        assert session.meta["make_reimbursement"] == {"kind": "write", "scopes": ["payments:write"]}
        assert tools["update_report"]["input_schema"]["properties"]["status"]["enum"] == [
            "approved",
            "rejected",
            "returned",
            "escalated",
        ]
        ok = await session.call("get_report", {"report_id": "ER-3001"})
        assert ok.ok and "Harbor Bistro" in ok.result
        denied = await session.call("make_reimbursement", {"report_id": "ER-3007", "amount": 1})
        assert not denied.ok and denied.access_denied and denied.result.startswith("ACCESS_DENIED")
        bad = await session.call("find_duplicates", {"report_id": "ER-0"})
        assert not bad.ok and not bad.access_denied and "not found" in bad.result
        assert (await session.call("get_expense_policy", {})).ok
        await session.call("add_note", {"report_id": "ER-3001", "text": "x"})
        await session.call("send_notice", {"report_id": "ER-3004", "kind": "receipt_request", "body": "Receipt L1."})
        await session.call("update_report", {"report_id": "ER-3004", "status": "returned", "reason": "receipt"})
    con = sqlite3.connect(settings.db_path)
    outcomes = con.execute("SELECT operation, outcome FROM audit_log ORDER BY id").fetchall()
    con.close()
    assert outcomes == [
        ("get_report", "ok"),
        ("make_reimbursement", "denied"),
        ("find_duplicates", "error"),
        ("get_expense_policy", "ok"),
        ("add_note", "ok"),
        ("send_notice", "ok"),
        ("update_report", "ok"),
    ]


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

    call = await McpToolSession(FakeClient()).call("get_report", {})
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
    assert _status(s.tools_url, {"X-Principal": "expense-bot"}) == 401
    assert _status(s.tools_url, {"Authorization": "Bearer s3cret"}) == 400
    assert _status(s.tools_url, {"Authorization": "Bearer s3cret", "X-Principal": "mallory"}) == 403
    ep = await ExpenseAgent(RulesLLM(), s, http_connector(s)).handle(
        {"type": "expense.submitted", "report_id": "ER-3001"}, "expense-bot"
    )
    assert ep.done and [st["name"] for st in ep.steps] == [
        "get_report",
        "get_expense_policy",
        "find_duplicates",
        "update_report",
        "send_notice",
    ]
    again = await ExpenseAgent(RulesLLM(), s, http_connector(s)).handle(
        {"type": "expense.submitted", "report_id": "ER-3001"}, "expense-bot"
    )  # cached per-principal server; the report is no longer submitted
    assert [st["name"] for st in again.steps] == ["get_report"] and "approved, not submitted" in again.output


async def test_router_ignores_non_http_scopes_and_can_forget(settings):
    router = PrincipalRouter(settings)
    await router({"type": "websocket"}, None, None)
    router.apps["x"] = object()
    router.forget()
    assert router.apps == {}


def test_main_initialises_the_database_and_serves(settings, monkeypatch, tmp_path):
    served: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: served.update(app=app, **kw))
    monkeypatch.setenv("EXPENSE_DB", str(tmp_path / "fresh" / "e.db"))
    monkeypatch.setenv("EXPENSE_OUT", str(tmp_path / "fresh" / "out"))
    tools_server.main(["--host", "0.0.0.0", "--port", "9800"])
    assert (tmp_path / "fresh" / "e.db").exists() and isinstance(served["app"], PrincipalRouter)
    assert (tmp_path / "fresh" / "out" / "notices").is_dir()
    assert served["port"] == 9800 and served["host"] == "0.0.0.0"
    tools_server.main([])  # database already there: served without re-seeding
    assert served["port"] == 9700


async def test_requests_with_an_unlisted_host_header_are_refused(http_server):
    """DNS-rebinding protection stays on: only the configured host names are served."""
    status = _status(
        http_server.tools_url,
        {"Authorization": "Bearer s3cret", "X-Principal": "expense-bot", "Host": "evil.example.com"},
    )
    assert status == 421


def test_allowed_hosts_from_environment(monkeypatch):
    from expense_agent.config import Settings

    monkeypatch.delenv("EXPENSE_TOOLS_ALLOWED_HOSTS", raising=False)
    assert Settings().tools_allowed_hosts == ["localhost:*", "127.0.0.1:*", "expense-tools:*"]
    monkeypatch.setenv("EXPENSE_TOOLS_ALLOWED_HOSTS", "proxy:*, expense-tools:*,")
    assert Settings().tools_allowed_hosts == ["proxy:*", "expense-tools:*"]
