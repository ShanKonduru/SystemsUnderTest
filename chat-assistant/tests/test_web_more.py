"""Web app: sandbox control endpoints, guest sign-in, static pages, document limits and websocket edge cases."""

import importlib
import sqlite3

import pytest
from fastapi.testclient import TestClient

from tests.conftest import intent, plan
from tests.fakes import text, tools
from tests.test_web import PDF, login, web  # noqa: F401  (web is a fixture)

TOKEN = "harbor-sandbox-token"


@pytest.fixture
def sandbox(web, settings):  # noqa: F811
    settings.sandbox_mode, settings.sandbox_token = True, TOKEN
    return web


def test_index_and_health(web):  # noqa: F811
    client = TestClient(web[0].app)
    assert client.get("/healthz").json() == {"status": "ok"}
    page = client.get("/")
    assert page.status_code == 200 and "html" in page.headers["content-type"]


def test_sandbox_endpoints_are_hidden_outside_sandbox_mode(web, settings):  # noqa: F811
    settings.sandbox_mode = False
    client = TestClient(web[0].app)
    assert client.get("/sandbox/status", headers={"x-sandbox-token": TOKEN}).status_code == 404
    assert client.post("/sandbox/reset", headers={"x-sandbox-token": TOKEN}).status_code == 404


def test_sandbox_endpoints_need_the_token(sandbox, settings):
    client = TestClient(sandbox[0].app)
    assert client.get("/sandbox/status").status_code == 401
    assert client.get("/sandbox/status", headers={"x-sandbox-token": "wrong"}).status_code == 401
    settings.sandbox_token = ""  # no token configured: nothing is accepted
    assert client.get("/sandbox/status", headers={"x-sandbox-token": ""}).status_code == 401


def test_sandbox_status_reports_versions_and_transport(sandbox, settings, monkeypatch):
    app_mod = sandbox[0]
    from insurance_copilot.core import llm as llm_mod

    monkeypatch.setattr(llm_mod, "make_llm", lambda s=None: object())
    monkeypatch.setattr(llm_mod, "describe", lambda llm: {"provider": "scripted", "model": "harbor-test"})
    client = TestClient(app_mod.app)
    status = client.get("/sandbox/status", headers={"x-sandbox-token": TOKEN}).json()
    assert status["sandbox"] is True and status["clock"] == "2026-11-03"
    assert status["versions"]["provider"] == "scripted" and status["versions"]["model"] == "harbor-test"
    assert len(status["versions"]["prompts"]) == 12 and len(status["versions"]["tools"]) == 12
    assert status["toggles"] == {"traces": True, "tool_proxy": False, "thinking": False, "txn_policy": "direct"}
    assert status["transport"] == status["mcp_transport"] == "inprocess"
    mcp_tools_hash = status["versions"]["tools"]

    settings.mcp_url = "http://127.0.0.1:9000/mcp"
    settings.tool_proxy = True
    status = client.get("/sandbox/status", headers={"x-sandbox-token": TOKEN}).json()
    assert status["transport"] == "http" and status["toggles"]["tool_proxy"] is True

    settings.rest_url = "http://127.0.0.1:9200/v1"  # REST mode: the REST layer is part of the tools fingerprint
    status = client.get("/sandbox/status", headers={"x-sandbox-token": TOKEN}).json()
    assert status["transport"] == "rest" and status["versions"]["tools"] != mcp_tools_hash


def test_sandbox_status_survives_an_unconfigured_llm(sandbox, monkeypatch):
    from insurance_copilot.core import llm as llm_mod

    def broken(settings=None):
        raise RuntimeError("CHAT_LLM_PROVIDER is not set")

    monkeypatch.setattr(llm_mod, "make_llm", broken)
    status = TestClient(sandbox[0].app).get("/sandbox/status", headers={"x-sandbox-token": TOKEN}).json()
    assert status["versions"]["provider"] == "unconfigured"
    assert status["versions"]["model"] == "CHAT_LLM_PROVIDER is not set"


def test_sandbox_reset_restores_the_seed_data(sandbox, db):
    client = TestClient(sandbox[0].app)
    con = sqlite3.connect(db)
    con.execute("DELETE FROM claims")
    con.commit()
    con.close()
    r = client.post("/sandbox/reset", headers={"x-sandbox-token": TOKEN})
    assert r.json() == {"status": "reset", "clock": "2026-11-03"}
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM claims").fetchone()[0] > 0
    assert login(client).status_code == 200  # demo users are back too


def test_guest_session(web):  # noqa: F811
    client = TestClient(web[0].app)
    guest = client.post("/api/guest").json()
    assert guest["customer_id"].startswith("GUEST-") and guest["display_name"] == "Guest"
    assert client.get("/api/me").json()["customer_id"] == guest["customer_id"]


def test_upload_limits_and_unknown_documents(web, monkeypatch):  # noqa: F811
    app_mod = web[0]
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    monkeypatch.setattr(app_mod, "MAX_DOCUMENT_BYTES", 10)
    too_big = client.post(f"/api/conversations/{conv['id']}/documents?filename=bill.pdf", content=PDF,
                          headers={"Content-Type": "application/pdf"})
    assert too_big.status_code == 413
    assert client.get("/api/documents/DOC-9999").status_code == 404


def _start_write(ws, llm):
    llm.structured_queue += [intent("billing"), plan("make_payment", "respond")]
    llm.executor_queue += [tools(("make_payment", {"policy_id": "POL-1002", "amount": 20})), text("Paid $20.")]
    ws.send_json({"type": "user", "text": "pay $20 on my home policy"})
    while (msg := ws.receive_json())["type"] != "awaiting_confirmation":
        pass
    return msg["data"]["key"]


def test_messages_while_a_reply_is_pending(web, db):  # noqa: F811
    app_mod, llm, _ = web
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        ws.receive_json()
        key = _start_write(ws, llm)
        ws.send_json({"type": "user", "text": "hello again"})       # a second message while waiting
        assert ws.receive_json() == {"type": "error", "data": {"message": "Please wait for the current reply."}}
        ws.send_json({"type": "ping"})                               # unknown message types are ignored
        ws.send_json({"type": "user", "text": "   "})               # so are empty messages
        ws.send_json({"type": "confirm", "key": "confirm-99", "approved": True})  # unknown key: ignored
        ws.send_json({"type": "confirm", "key": key, "approved": True})
        types = []
        while (msg := ws.receive_json())["type"] != "turn_completed":
            types.append(msg["type"])
    assert "confirmation_result" in types and "assistant_message" in types
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM payments WHERE amount=20").fetchone()[0] == 1


def test_disconnect_during_an_approval_declines_it_and_the_turn_is_still_saved(web, db):  # noqa: F811
    app_mod, llm, store = web
    llm.structured_queue += [intent("billing"), plan("make_payment", "respond")]
    llm.executor_queue += [tools(("make_payment", {"policy_id": "POL-1002", "amount": 20})),
                           tools(("make_payment", {"policy_id": "POL-1002", "amount": 30})),
                           text("Nothing was paid.")]
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user", "text": "pay $20 on my home policy"})
        while ws.receive_json()["type"] != "awaiting_confirmation":
            pass
    # the browser left: the pending approval counts as declined, the next one is not even asked, and the turn is saved
    msgs = client.get(f"/api/conversations/{conv['id']}/messages").json()
    assert [(m["role"], m["text"]) for m in msgs][-1] == ("assistant", "Nothing was paid.")
    calls = client.get(f"/api/conversations/{conv['id']}/traces").json()[0]["tool_calls"]
    assert [c["blocked_reason"] for c in calls] == ["USER_DECLINED", "USER_DECLINED"]
    assert sqlite3.connect(db).execute("SELECT COUNT(*) FROM payments WHERE amount IN (20, 30)").fetchone()[0] == 0


def test_a_broken_client_message_is_logged_and_ends_the_chat_loop(web, caplog):  # noqa: F811
    app_mod, _, _ = web
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    with caplog.at_level("ERROR", logger="insurance_copilot.web"):
        with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
            ws.receive_json()
            ws.send_text("this is not json")
    assert any("websocket loop failed" in r.getMessage() and conv["id"] in r.getMessage() for r in caplog.records)
    # the conversation is still usable afterwards
    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        assert ws.receive_json()["type"] == "ready"


def test_first_start_seeds_a_new_database(tmp_path, monkeypatch):
    from insurance_copilot.web import app as app_mod

    fresh = tmp_path / "fresh.db"
    monkeypatch.setenv("COPILOT_DB", str(fresh))
    monkeypatch.setenv("COPILOT_TODAY", "2026-11-03")
    try:
        importlib.reload(app_mod)
        assert app_mod.store.has_app_schema()
        names = {r[0] for r in sqlite3.connect(fresh).execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"customers", "policies", "users"} <= names
        assert login(TestClient(app_mod.app)).status_code == 200
    finally:
        monkeypatch.undo()
        importlib.reload(app_mod)
