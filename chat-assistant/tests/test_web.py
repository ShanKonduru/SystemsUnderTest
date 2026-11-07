"""Web app: login, per-user conversations, persistence, resume, and the approval round trip."""

import contextlib
import sqlite3

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from insurance_copilot.core import session as session_mod
from insurance_copilot.web.store import DEFAULT_DEMO_PASSWORD, AppStore
from tests.conftest import intent, plan
from tests.fakes import ScriptedLLM, text, tools


@pytest.fixture
def web(monkeypatch, settings, db):
    from insurance_copilot.web import app as web_mod

    store = AppStore(db)
    store.ensure_schema()
    llm = ScriptedLLM()
    real_open = session_mod.open_session

    @contextlib.asynccontextmanager
    async def fake_open(**kwargs):
        kwargs.update(settings=settings, llm=llm)
        async with real_open(**kwargs) as s:
            yield s

    monkeypatch.setattr(web_mod, "open_session", fake_open)
    monkeypatch.setattr(web_mod, "settings", settings)
    monkeypatch.setattr(web_mod, "store", store)
    return web_mod, llm, store


def login(client, user="shan", password=DEFAULT_DEMO_PASSWORD):
    return client.post("/api/login", json={"username": user, "password": password})


def chat_turn(ws, message, approve=True):
    ws.send_json({"type": "user", "text": message})
    reply = None
    while True:
        msg = ws.receive_json()
        if msg["type"] == "awaiting_confirmation":
            ws.send_json({"type": "confirm", "key": msg["data"]["key"], "approved": approve})
        elif msg["type"] == "assistant_message":
            reply = msg["data"]["text"]
        elif msg["type"] == "turn_completed":
            return reply


def test_login_required_and_password_checked(web):
    app_mod, _, store = web
    client = TestClient(app_mod.app)
    assert client.get("/api/conversations").status_code == 401
    assert login(client, password="wrong").status_code == 401
    assert login(client, user="nobody").status_code == 401
    r = login(client, user="Shan")  # usernames are case-insensitive
    assert r.status_code == 200 and r.json()["customer_id"] == "CUST-001"
    assert client.get("/api/me").json()["display_name"] == "Shan"
    # passwords are not stored in plain text
    row = sqlite3.connect(store.db_path).execute("SELECT password_hash FROM users WHERE username='shan'").fetchone()
    assert DEFAULT_DEMO_PASSWORD not in row[0]
    client.post("/api/logout")
    assert client.get("/api/me").status_code == 401


def test_all_three_demo_users_can_sign_in(web):
    app_mod, _, _ = web
    for user, cust in [("shan", "CUST-001"), ("indu", "CUST-002"), ("sri", "CUST-003")]:
        assert login(TestClient(app_mod.app), user).json()["customer_id"] == cust


def test_chat_is_saved_and_can_be_continued(web):
    app_mod, llm, store = web
    llm.structured_queue += [intent("billing"), plan("make_payment", "respond"),
                             intent("billing"), plan("get_billing", "respond")]
    llm.executor_queue += [tools(("make_payment", {"policy_id": "POL-1002", "amount": 20})), text("Paid $20."),
                           tools(("get_billing", {"policy_id": "POL-1002"})), text("Balance is $173.60.")]
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()

    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        assert ws.receive_json()["type"] == "ready"
        assert chat_turn(ws, "pay $20 on my home policy") == "Paid $20."

    # history and title persisted
    msgs = client.get(f"/api/conversations/{conv['id']}/messages").json()
    assert [(m["role"], m["text"]) for m in msgs] == [("user", "pay $20 on my home policy"), ("assistant", "Paid $20.")]
    assert client.get("/api/conversations").json()[0]["title"] == "pay $20 on my home policy"
    traces = client.get(f"/api/conversations/{conv['id']}/traces").json()
    assert traces[0]["tool_calls"][0]["name"] == "make_payment" and traces[0]["tool_calls"][0]["confirmed"]

    # reconnect later: the assistant continues with the earlier context
    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        ws.receive_json()
        assert chat_turn(ws, "what is left to pay?") == "Balance is $173.60."
    resumed_request = llm.create_calls[2]["messages"]
    assert resumed_request[0] == {"role": "user", "content": "pay $20 on my home policy"}
    assert any(m["role"] == "assistant" and m["content"][0]["text"] == "Paid $20." for m in resumed_request
               if isinstance(m["content"], list) and m["content"] and m["content"][0].get("type") == "text")
    assert client.get(f"/api/conversations/{conv['id']}/traces").json()[1]["turn"] == 2


def test_users_cannot_see_each_others_chats(web):
    app_mod, _, _ = web
    shan, indu = TestClient(app_mod.app), TestClient(app_mod.app)
    login(shan)
    login(indu, "indu")
    conv = shan.post("/api/conversations").json()
    assert indu.get("/api/conversations").json() == []
    assert indu.get(f"/api/conversations/{conv['id']}/messages").status_code == 404
    assert indu.delete(f"/api/conversations/{conv['id']}").status_code == 404
    with pytest.raises(WebSocketDisconnect) as exc:
        with indu.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
            ws.receive_json()
    assert exc.value.code == 4404


def test_websocket_requires_login(web):
    app_mod, _, _ = web
    with pytest.raises(WebSocketDisconnect) as exc:
        with TestClient(app_mod.app).websocket_connect("/ws?conversation=x") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_session_cookie_is_specific_to_the_deployment(web):
    app_mod, _, _ = web
    client = TestClient(app_mod.app)
    login(client)
    names = list(client.cookies.keys())
    assert names == [app_mod.COOKIE] and names[0].startswith("copilot_session_") and names[0] != "copilot_session"


def test_empty_chats_are_hidden_and_most_recent_is_first(web):
    app_mod, _, store = web
    client = TestClient(app_mod.app)
    login(client)
    older = client.post("/api/conversations").json()
    store.add_message(older["id"], "user", "older chat")
    newer = client.post("/api/conversations").json()
    store.add_message(newer["id"], "user", "newer chat")
    client.post("/api/conversations")  # never used: not listed, and cleaned up on the next create
    listed = client.get("/api/conversations").json()
    assert [c["title"] for c in listed] == ["newer chat", "older chat"]
    # touching the older chat makes it the most recent one again
    store.add_message(older["id"], "assistant", "reply")
    assert client.get("/api/conversations").json()[0]["id"] == older["id"]


def test_delete_conversation(web):
    app_mod, _, _ = web
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    assert client.delete(f"/api/conversations/{conv['id']}").status_code == 200
    assert client.get("/api/conversations").json() == []


PDF = b"%PDF-1.4\n% proof\n%%EOF\n"


def test_upload_proof_and_send_it_in_chat(web):
    app_mod, llm, store = web
    llm.structured_queue += [intent("update_profile"), plan("submit_profile_change", "respond")]
    llm.executor_queue += [
        tools(("submit_profile_change", {"change_type": "mailing_address", "new_value": "77 Lake Shore Dr, Chicago, IL 60611",
                                         "document_id": "DOC-6001", "document_type": "utility_bill",
                                         "effective_date": "2026-10-20"})),
        text("Address updated (CHG-8001)."),
    ]
    client = TestClient(app_mod.app)
    login(client)
    conv = client.post("/api/conversations").json()
    up = client.post(f"/api/conversations/{conv['id']}/documents?filename=bill.pdf", content=PDF,
                     headers={"Content-Type": "application/pdf"})
    assert up.status_code == 200 and up.json()["document_id"] == "DOC-6001"
    bad = client.post(f"/api/conversations/{conv['id']}/documents?filename=x.pdf", content=b"not a pdf",
                      headers={"Content-Type": "application/pdf"})
    assert bad.status_code == 400

    with client.websocket_connect(f"/ws?conversation={conv['id']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user", "text": "here is my electricity bill", "attachments": ["DOC-6001"]})
        while (msg := ws.receive_json())["type"] != "turn_completed":
            if msg["type"] == "awaiting_confirmation":
                ws.send_json({"type": "confirm", "key": msg["data"]["key"], "approved": True})
    sent = llm.create_calls[0]["messages"][-2]["content"]
    assert sent.startswith("here is my electricity bill\n[Attached document DOC-6001: bill.pdf (application/pdf,")
    saved = client.get(f"/api/conversations/{conv['id']}/messages").json()[0]["text"]
    assert "[Attached document DOC-6001" in saved
    con = sqlite3.connect(store.db_path)
    assert con.execute("SELECT address FROM customers WHERE id='CUST-001'").fetchone()[0].startswith("77 Lake Shore")
    assert con.execute("SELECT document_id FROM profile_changes").fetchone()[0] == "DOC-6001"
    # the owner can open the proof; another user cannot
    assert client.get("/api/documents/DOC-6001").content == PDF
    other = TestClient(app_mod.app)
    login(other, "indu")
    assert other.get("/api/documents/DOC-6001").status_code == 404


def test_cannot_attach_someone_elses_document(web):
    app_mod, _, _ = web
    shan, indu = TestClient(app_mod.app), TestClient(app_mod.app)
    login(shan)
    login(indu, "indu")
    conv = shan.post("/api/conversations").json()
    shan.post(f"/api/conversations/{conv['id']}/documents?filename=bill.pdf", content=PDF,
              headers={"Content-Type": "application/pdf"})
    iconv = indu.post("/api/conversations").json()
    with indu.websocket_connect(f"/ws?conversation={iconv['id']}") as ws:
        ws.receive_json()
        ws.send_json({"type": "user", "text": "proof", "attachments": ["DOC-6001"]})
        msg = ws.receive_json()
    assert msg["type"] == "error" and "ACCESS_DENIED" in msg["data"]["message"]
