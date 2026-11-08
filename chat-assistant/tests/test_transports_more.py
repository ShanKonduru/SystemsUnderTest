"""How the app reaches its tools: the REST API edge cases, the HTTP tool server's ASGI plumbing, every
connect_toolbox transport (in-process, REST, stdio), plus configuration from the environment."""

import asyncio
import importlib
import json
import runpy
import socket
import sys
import threading
import time
import warnings
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn

try:
    import httpx2 as httpx
except ImportError:
    import httpx

from insurance_copilot import config as config_mod, rest_api
from insurance_copilot.backend.db import BackendError, init_db
from insurance_copilot.config import Settings
from insurance_copilot.core import llm as llm_mod
from insurance_copilot.core.toolbox import McpToolbox, RestToolbox, connect_toolbox
from insurance_copilot.mcp_server import http as http_mod
from insurance_copilot.rest_api import InsuranceApi, _coerce
from tests.conftest import TODAY


# ------------------------------------------------------------------ REST API
def test_coerce_turns_text_into_schema_types():
    assert _coerce("", {"anyOf": [{"type": "string"}, {"type": "null"}]}) is None
    assert _coerce("", {"type": "string"}) == ""
    assert _coerce("TRUE", {"type": "boolean"}) is True and _coerce("false", {"type": "boolean"}) is False
    assert _coerce("maybe", {"type": "boolean"}) == "maybe"
    assert _coerce("-12", {"type": "integer"}) == -12 and _coerce("1.5", {"type": "integer"}) == "1.5"
    assert _coerce("1.5", {"type": "number"}) == 1.5 and _coerce("20", {"type": "number"}) == 20.0
    assert _coerce("abc", {"type": "number"}) == "abc"
    assert _coerce("POL-1002", {}) == "POL-1002"


class FakeTool:
    def __init__(self, name, schema=None):
        self.name, self.input_schema, self.description = name, schema, name

    def model_dump(self, **kwargs):
        return {"name": self.name, "inputSchema": self.input_schema or {"type": "object"}}


class FakeServer:
    """Stands in for the tool server of one principal, to return results the real tools never produce."""

    def __init__(self, reply, is_error=False):
        self.reply, self.is_error, self.calls = reply, is_error, []

    async def list_tools(self):
        return [FakeTool("get_billing", {"properties": {"policy_id": {"type": "string"}}}),
                FakeTool("brand_new_tool")]

    async def call_tool(self, name, args):
        self.calls.append((name, args))
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.reply)], is_error=self.is_error)


def _client(api, principal="CUST-001"):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app()), base_url="http://api",
                             headers={"X-Customer-Id": principal})


def test_rest_api_edge_cases(db):
    async def run():
        api = InsuranceApi(db, TODAY)
        api.servers["CUST-ERR"] = FakeServer("BUSINESS_RULE: no", is_error=True)
        api.servers["CUST-TXT"] = FakeServer("plain words, not JSON")
        async with _client(api) as http:
            assert (await http.get("/v1/tools", headers={"X-Customer-Id": ""})).status_code == 403
            # a POST without a body: only the path parameters go to the tool
            empty = await http.post("/v1/policies/POL-1002/payments")
            assert empty.status_code == 400 and "amount" in empty.json()["error"]
            not_object = await http.post("/v1/policies/POL-1002/payments", content=b"[1, 2]")
            assert not_object.status_code == 400 and "JSON object" in not_object.json()["error"]
            # tools that have a route are not served RPC-style; unknown ones are, if the principal has them
            rpc = await http.post("/v1/operations/get_billing")
            assert rpc.status_code == 404 and "/policies/{policy_id}/billing" in rpc.json()["error"]
            assert (await http.post("/v1/operations/brand_new_tool")).status_code == 404
            err = await http.get("/v1/policies/POL-1002/billing", headers={"X-Customer-Id": "CUST-ERR"})
            assert err.status_code == 400 and err.json() == {"error": "BUSINESS_RULE: no"}
            txt = await http.get("/v1/policies/POL-1002/billing", headers={"X-Customer-Id": "CUST-TXT"})
            assert txt.status_code == 200 and txt.text == "plain words, not JSON"
            rpc_ok = await http.post("/v1/operations/brand_new_tool", json={"x": 1},
                                     headers={"X-Customer-Id": "CUST-TXT"})
            assert rpc_ok.status_code == 201 and rpc_ok.headers["content-type"].startswith("text/plain")
        assert api.servers["CUST-TXT"].calls[0] == ("get_billing", {"policy_id": "POL-1002"})
    asyncio.run(run())


@pytest.mark.xfail(strict=True, reason="BUG: the RPC-style fallback (POST /v1/operations/{tool}) passes its own {tool} "
                                       "URL segment to the tool as an extra 'tool' argument")
def test_rpc_style_call_passes_only_the_body_as_arguments(db):
    async def run():
        api = InsuranceApi(db, TODAY)
        api.servers["CUST-TXT"] = FakeServer('{"done": true}')
        async with _client(api, "CUST-TXT") as http:
            r = await http.post("/v1/operations/brand_new_tool", json={"x": 1})
        assert r.status_code == 201 and r.json() == {"done": True}
        assert api.servers["CUST-TXT"].calls == [("brand_new_tool", {"x": 1})]
    asyncio.run(run())


def test_openapi_skips_a_principal_that_cannot_be_served(db, monkeypatch):
    api = InsuranceApi(db, TODAY)
    real = api.server_for

    def server_for(principal):
        if principal == "REP-203":
            raise BackendError("representative unavailable")
        return real(principal)

    monkeypatch.setattr(api, "server_for", server_for)

    async def run():
        async with _client(api) as http:
            return (await http.get("/v1/openapi.json")).json()
    spec = asyncio.run(run())
    assert "/policies/{policy_id}/payments" in spec["paths"] and "/reps/me" not in spec["paths"]


def test_rest_api_serve_and_module_entry_point(monkeypatch, tmp_path, capsys):
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "r.db"))
    monkeypatch.setenv("COPILOT_TODAY", "2026-11-03")
    monkeypatch.setenv("COPILOT_TXN_POLICY", "review")
    rest_api.serve(9301)
    assert runs[-1][1] == {"host": "127.0.0.1", "port": 9301, "log_level": "warning"}
    assert "http://127.0.0.1:9301/v1" in capsys.readouterr().out
    monkeypatch.delenv("COPILOT_DB")
    monkeypatch.delenv("COPILOT_TODAY")
    monkeypatch.setattr(sys, "argv", ["rest_api", "--port", "9302", "--host", "127.0.0.2"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("insurance_copilot.rest_api", run_name="__main__")
    assert runs[-1][1] == {"host": "127.0.0.2", "port": 9302, "log_level": "warning"}
    assert str(Path("data") / "insurance.db") in capsys.readouterr().out


# ------------------------------------------------------------------ MCP over HTTP: ASGI plumbing
def _receiver(*messages):
    queue = list(messages)

    async def receive():
        return queue.pop(0)
    return receive, queue


def test_http_router_ignores_other_scopes_and_drains_refused_bodies(tmp_path):
    router = http_mod.CustomerRouter(tmp_path / "unused.db", TODAY)
    sent = []

    async def send(msg):
        sent.append(msg)

    async def run():
        await router({"type": "websocket"}, None, send)            # not HTTP: ignored
        receive, left = _receiver({"type": "http.request", "body": b"{", "more_body": True},
                                  {"type": "http.request", "body": b"}", "more_body": False})
        await router({"type": "http", "headers": []}, receive, send)
        return left
    assert asyncio.run(run()) == []                                # the whole body was read first
    assert sent[0]["status"] == 400 and sent[1]["body"] == b"missing X-Customer-Id header"


def test_http_router_lifespan_ignores_unknown_messages(tmp_path):
    router = http_mod.CustomerRouter(tmp_path / "unused.db", TODAY)
    sent = []

    async def send(msg):
        sent.append(msg["type"])

    receive, _ = _receiver({"type": "lifespan.startup"}, {"type": "lifespan.something_else"},
                           {"type": "lifespan.shutdown"})
    asyncio.run(router({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete", "lifespan.shutdown.complete"] and router.stopping.is_set()


def test_http_serve(monkeypatch, tmp_path, capsys):
    runs = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    monkeypatch.setenv("COPILOT_DB", str(tmp_path / "h.db"))
    monkeypatch.setenv("COPILOT_TODAY", "2026-11-03")
    http_mod.serve(9401)
    app, kw = runs[-1]
    assert isinstance(app, http_mod.CustomerRouter) and app.today == date(2026, 11, 3)
    assert kw == {"host": "127.0.0.1", "port": 9401, "log_level": "warning", "lifespan": "on"}
    monkeypatch.delenv("COPILOT_DB")
    monkeypatch.delenv("COPILOT_TODAY")
    http_mod.serve(9402, "127.0.0.3")
    assert runs[-1][0].today is None and str(runs[-1][0].db_path).endswith("insurance.db")
    assert "http://127.0.0.3:9402/mcp" in capsys.readouterr().out


# ------------------------------------------------------------------ toolboxes
def test_mcp_toolbox_falls_back_to_structured_content():
    class Client:
        async def call_tool(self, name, args):
            return SimpleNamespace(content=[], structured_content={"balance_due": 12.5}, is_error=False)

    out = asyncio.run(McpToolbox(Client()).call("get_billing", {"policy_id": "POL-1002"}))
    assert json.loads(out.text) == {"balance_due": 12.5} and not out.is_error


def test_rest_toolbox_handles_non_json_answers():
    def handler(request):
        if request.url.path.endswith("/fail"):
            return httpx.Response(502, text="Bad gateway")
        return httpx.Response(200, text="Thanks, noted.")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            tb = RestToolbox(http, "http://api/v1/")
            tb.routes = {"note": ("POST", "/note"), "broken": ("GET", "/fail")}
            return await tb.call("note", {"text": "hi", "skip": None}), await tb.call("broken", {"flag": True})
    good, bad = asyncio.run(run())
    assert (good.text, good.is_error) == ("Thanks, noted.", False)
    assert (bad.text, bad.is_error) == ("Bad gateway", True)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_connect_toolbox_over_rest(tmp_path):
    db = tmp_path / "rest.db"
    init_db(db, TODAY)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(InsuranceApi(db, TODAY).app(), host="127.0.0.1", port=port,
                                           log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    settings = SimpleNamespace(mcp_transport="stdio", mcp_url="", rest_url=f"http://127.0.0.1:{port}/v1")

    async def run():
        async with connect_toolbox(settings, "CUST-002") as tb:
            assert isinstance(tb, RestToolbox) and "list_policies" in tb.tools
            return (await tb.call("list_policies", {})).text
    try:
        listed = asyncio.run(run())
    finally:
        server.should_exit = True
    assert "POL-2001" in listed and "POL-1001" not in listed


def test_connect_toolbox_over_stdio(settings):
    settings.mcp_transport, settings.mcp_url, settings.rest_url, settings.mcp_command = "stdio", "", "", []

    async def run():
        async with connect_toolbox(settings, "CUST-003") as tb:
            assert isinstance(tb, McpToolbox)
            return (await tb.call("get_customer_profile", {})).text
    profile = json.loads(asyncio.run(run()))
    assert profile["id"] == "CUST-003"


# ------------------------------------------------------------------ configuration
def test_settings_from_the_environment(monkeypatch):
    monkeypatch.setenv("COPILOT_SANDBOX", " Yes ")
    monkeypatch.setenv("COPILOT_TOOL_PROXY", "off")
    monkeypatch.setenv("COPILOT_MCP_COMMAND", '["harbor-proxy", "--record", 5]')
    s = Settings()
    assert s.sandbox_mode is True and s.tool_proxy is False
    assert s.mcp_command == ["harbor-proxy", "--record", "5"]
    monkeypatch.setenv("COPILOT_MCP_COMMAND", "harbor-proxy --record out.jsonl")
    assert Settings().mcp_command == ["harbor-proxy", "--record", "out.jsonl"]
    monkeypatch.setenv("COPILOT_MCP_COMMAND", "   ")
    assert Settings().mcp_command == []


def test_config_without_any_env_file(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda self: False)
    try:
        importlib.reload(config_mod)
        assert config_mod.PROJECT_ROOT.name == "chat-assistant"
    finally:
        monkeypatch.undo()
        importlib.reload(config_mod)


def test_make_llm_uses_the_app_provider_variable(monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_mod, "_factory", lambda **kw: seen.update(kw) or "llm")
    assert llm_mod.make_llm(Settings(), temperature=0) == "llm"
    assert seen == {"override_var": "CHAT_LLM_PROVIDER", "temperature": 0}


@pytest.mark.parametrize("value, expected", [("1", True), ("on", True), ("no", False), ("", False)])
def test_env_bool(monkeypatch, value, expected):
    monkeypatch.setenv("HARBOR_FLAG", value)
    assert config_mod._env_bool("HARBOR_FLAG", not expected) is expected
