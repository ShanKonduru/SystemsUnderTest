from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TOKEN, claim, quote

SANDBOX = {"X-Sandbox-Token": TOKEN}


def test_page_health_and_config(client: TestClient) -> None:
    page = client.get("/")
    assert page.status_code == 200 and "Harbor Mutual Automation Lab" in page.text
    assert page.headers["cache-control"] == "no-store"
    for asset in ("lab.css", "release.js", "app.js", "receipt.html", "frames/payment.html"):
        assert client.get("/static/" + asset).status_code == 200
    assert client.get("/healthz").json() == {"status": "ok", "app": "automation-lab", "version": "0.1.0", "release": "v1"}
    cfg = client.get("/api/config").json()
    assert cfg["release"] == "v1" and set(cfg["releases"]) == {"v1", "v2", "v3"}
    assert cfg["today"] == "2026-11-03" and cfg["ui_defects"] == [] and ".pdf" in cfg["upload_types"]


def test_policies_search_filter_sort_page(client: TestClient) -> None:
    first = client.get("/api/policies").json()
    assert first["total"] == 57 and first["pages"] == 6 and len(first["items"]) == 10
    assert first["items"][0]["policy_number"] == "POL-1001" and first["items"][0]["holder"] == "Shan Mercer"
    by_name = client.get("/api/policies", params={"q": "indu"}).json()
    assert by_name["total"] == 3 and all(p["holder"] == "Indu Varma" for p in by_name["items"])
    active_auto = client.get("/api/policies", params={"status": "active", "product": "auto,boat", "size": 100}).json()
    assert active_auto["items"] and all(p["status"] == "active" and p["product"] in {"auto", "boat"} for p in active_auto["items"])
    desc = client.get("/api/policies", params={"sort": "premium", "dir": "desc", "size": 100}).json()["items"]
    assert [p["premium"] for p in desc] == sorted((p["premium"] for p in desc), reverse=True)
    last = client.get("/api/policies", params={"page": 99, "size": 25}).json()
    assert last["page"] == 3 and len(last["items"]) == 7
    assert client.get("/api/policies", params={"sort": "zip"}).status_code == 400
    assert client.get("/api/policies", params={"q": "nobody"}).json() == {"total": 0, "page": 1, "pages": 1, "size": 10, "items": []}


def test_policy_lookup_and_csv(client: TestClient) -> None:
    assert client.get("/api/policies/pol-1002").json()["holder"] == "Indu Varma"
    assert client.get("/api/policies/POL-9999").status_code == 404
    full = client.get("/api/policies.csv")
    assert full.headers["content-type"].startswith("text/csv")
    assert 'filename="harbor-mutual-policies.csv"' in full.headers["content-disposition"]
    assert len(full.text.strip().splitlines()) == 58
    some = client.get("/api/policies.csv", params={"ids": "POL-1001, pol-1003"})
    assert some.text.strip().splitlines()[1].startswith("POL-1001") and len(some.text.strip().splitlines()) == 3
    assert "selected" in some.headers["content-disposition"]
    assert client.get("/api/activity").json()[-1]["kind"] == "export"


def test_quote_priced_saved_and_logged(client: TestClient) -> None:
    res = client.post("/api/quotes", json=quote())
    assert res.status_code == 201
    body = res.json()
    assert body["quote_id"] == "Q-2026-0001" and body["annual_premium"] == 583.16 and body["monthly_payment"] is None
    labels = [line["label"] for line in body["breakdown"]]
    assert "Multi-policy discount (10%)" in labels and "Paperless discount (3%)" in labels
    monthly = client.post("/api/quotes", json=quote(payment_plan="monthly")).json()
    assert monthly["quote_id"] == "Q-2026-0002" and monthly["monthly_payment"] == 50.54
    saved = client.get("/api/quotes").json()
    assert [q["id"] for q in saved] == ["Q-2026-0001", "Q-2026-0002"] and saved[0]["request"]["email"] == "shan@example.com"
    log = client.get("/api/activity").json()
    assert [a["kind"] for a in log] == ["quote", "quote"] and log[0]["source"] == "server"


def test_quote_validation_errors(client: TestClient) -> None:
    res = client.post("/api/quotes", json={"addons": "x"})
    assert res.status_code == 422
    errors = res.json()["errors"]
    assert set(errors) >= {"product", "coverage", "deductible", "first_name", "last_name", "email", "zip",
                           "date_of_birth", "start_date", "addons", "payment_plan", "consent"}
    res = client.post("/api/quotes", json=quote(phone="12", first_name="x" * 41, date_of_birth="2015-01-01",
                                                start_date="2027-06-01", addons=["flood"], notes="n" * 501))
    assert set(res.json()["errors"]) == {"phone", "first_name", "date_of_birth", "start_date", "addons", "notes"}
    assert client.post("/api/quotes", json=quote(date_of_birth="not-a-date")).json()["errors"]["date_of_birth"]
    assert client.get("/api/quotes").json() == []


def test_claim_filed(client: TestClient) -> None:
    up = client.post("/api/uploads", data={"area": "claim"}, files=[("files", ("photo.png", b"png", "image/png"))]).json()
    res = client.post("/api/claims", json=claim(upload_ids=[up["uploaded"][0]["id"]], policy_number=" pol-1001 ",
                                                signature="  shan   mercer "))
    assert res.status_code == 201 and res.json() == {"claim_id": "CLM-9001", "status": "received"}
    saved = client.get("/api/claims").json()
    assert saved[0]["id"] == "CLM-9001" and saved[0]["policy_number"] == "POL-1001"
    assert client.get("/api/activity").json()[-1]["summary"] == "Claim CLM-9001 filed on POL-1001"


@pytest.mark.parametrize(("change", "field"), [
    ({"policy_number": "POL-0000"}, "policy_number"),
    ({"policy_number": "POL-1002", "signature": "Indu Varma"}, "policy_number"),   # a lapsed policy
    ({"incident_type": "meteor"}, "incident_type"),
    ({"incident_date": "2026-12-01"}, "incident_date"),
    ({"incident_date": "2024-01-01"}, "incident_date"),
    ({"incident_date": ""}, "incident_date"),
    ({"location": " "}, "location"),
    ({"description": "too short"}, "description"),
    ({"report_number": ""}, "report_number"),
    ({"estimate": 0}, "estimate"),
    ({"estimate": True}, "estimate"),
    ({"estimate": "2500"}, "estimate"),
    ({"contact": "pigeon"}, "contact"),
    ({"upload_ids": ["UPL-0404"]}, "upload_ids"),
    ({"signature": "Someone Else"}, "signature"),
    ({"agree": False}, "agree"),
])
def test_claim_validation(client: TestClient, change: dict, field: str) -> None:
    res = client.post("/api/claims", json=claim(**change))
    assert res.status_code == 422 and field in res.json()["errors"]
    assert client.get("/api/claims").json() == []


def test_uploads_store_reject_download_delete(client: TestClient) -> None:
    res = client.post("/api/uploads", data={"area": "documents"}, files=[
        ("files", ("estimate.pdf", b"%PDF-1.4 demo", "application/pdf")),
        ("files", ("notes.txt", b"hello", "text/plain")),
        ("files", ("virus.exe", b"MZ", "application/octet-stream")),
        ("files", ("empty.csv", b"", "text/csv")),
        ("files", ("huge.png", b"x" * 2048, "image/png")),
        ("files", ("../../evil?.jpg", b"jpg", "image/jpeg")),
    ])
    assert res.status_code == 201
    body = res.json()
    assert [u["name"] for u in body["uploaded"]] == ["estimate.pdf", "notes.txt", "evil_.jpg"]
    assert [r["name"] for r in body["rejected"]] == ["virus.exe", "empty.csv", "huge.png"]
    assert [r["reason"] for r in body["rejected"]] == [".exe isn't allowed", "The file is empty", "The file is larger than 1 KB"]
    listed = client.get("/api/uploads").json()
    assert [u["id"] for u in listed] == ["UPL-0001", "UPL-0002", "UPL-0003"] and listed[0]["area"] == "documents"
    got = client.get("/api/uploads/UPL-0002")
    assert got.content == b"hello" and "notes.txt" in got.headers["content-disposition"]
    assert client.delete("/api/uploads/UPL-0002").json() == {"status": "deleted", "id": "UPL-0002"}
    assert client.get("/api/uploads/UPL-0002").status_code == 404
    assert client.delete("/api/uploads/UPL-0002").status_code == 404
    assert client.post("/api/uploads", data={"area": "attic"}, files=[("files", ("a.txt", b"a", "text/plain"))]).status_code == 400
    only_rejected = client.post("/api/uploads", files=[("files", ("a.exe", b"a", "application/octet-stream"))]).json()
    assert only_rejected["uploaded"] == [] and len(only_rejected["rejected"]) == 1
    assert [a["kind"] for a in client.get("/api/activity").json()] == ["upload", "upload_deleted"]


def test_board_moves_are_saved(client: TestClient) -> None:
    board = client.get("/api/board").json()
    assert len(board) == 10 and {c["column"] for c in board} == {"new", "review", "approved", "paid"}
    moved = client.put("/api/board/cards/CLM-5002", json={"column": "review", "position": 0}).json()
    assert moved == {"id": "CLM-5002", "column": "review", "position": 0, "from": "new"}
    cols = {}
    for c in client.get("/api/board").json():
        cols.setdefault(c["column"], []).append(c["id"])
    assert cols["review"][0] == "CLM-5002" and "CLM-5002" not in cols["new"]
    assert cols["new"] == ["CLM-5003", "CLM-5006"]
    # within a column, past the end is clamped
    client.put("/api/board/cards/CLM-5002", json={"column": "review", "position": 99})
    assert [c["id"] for c in client.get("/api/board").json() if c["column"] == "review"][-1] == "CLM-5002"
    assert client.put("/api/board/cards/CLM-0000", json={"column": "new", "position": 0}).status_code == 404
    assert client.put("/api/board/cards/CLM-5002", json={"column": "lost", "position": 0}).status_code == 400
    assert client.put("/api/board/cards/CLM-5002", json={"column": "new", "position": -1}).status_code == 422
    assert client.get("/api/activity").json()[0]["summary"] == "Moved CLM-5002 from new to review"


def test_activity_feed_and_slow(client: TestClient) -> None:
    seq = client.post("/api/activity", json={"kind": "click", "summary": "Clicked 1 time", "detail": {"n": 1}}).json()["seq"]
    rows = client.get("/api/activity").json()
    assert rows == [{**rows[0], "seq": seq, "source": "page", "kind": "click", "summary": "Clicked 1 time", "detail": {"n": 1}}]
    assert client.get("/api/activity", params={"after": seq}).json() == []
    assert client.post("/api/activity", json={"kind": "", "summary": "x"}).status_code == 422
    page1 = client.get("/api/feed").json()
    assert [n["id"] for n in page1["items"]][:2] == ["N-001", "N-002"] and page1["next"] == 10
    end = client.get("/api/feed", params={"offset": 50, "limit": 50}).json()
    assert len(end["items"]) == 10 and end["next"] is None
    assert client.get("/api/slow", params={"ms": 1}).json() == {"status": "done", "waited_ms": 1}
    assert client.get("/api/slow", params={"ms": -5}).json()["waited_ms"] == 0


def test_sandbox_controls(client: TestClient) -> None:
    assert client.get("/sandbox/status").status_code == 401
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "wrong"}).status_code == 401
    client.post("/api/quotes", json=quote())
    client.put("/api/board/cards/CLM-5002", json={"column": "paid", "position": 0})
    status = client.get("/sandbox/status", headers=SANDBOX).json()
    assert status["counts"]["quotes"] == 1 and status["release"] == "v1" and status["defects"] == []
    assert client.post("/sandbox/release", json={"release": "v3"}, headers=SANDBOX).json() == {"status": "ok", "release": "v3"}
    assert client.get("/api/config").json()["release"] == "v3" and client.get("/healthz").json()["release"] == "v3"
    assert client.post("/sandbox/release", json={"release": "v9"}, headers=SANDBOX).status_code == 400
    assert client.post("/sandbox/release", json={"release": "v2"}).status_code == 401
    assert client.post("/sandbox/reset", headers=SANDBOX).json() == {"status": "reset", "release": "v1"}
    assert client.get("/sandbox/status", headers=SANDBOX).json()["counts"] == {"quotes": 0, "claims": 0, "uploads": 0, "activity": 0}
    assert next(c for c in client.get("/api/board").json() if c["id"] == "CLM-5002")["column"] == "new"
    assert client.get("/api/quotes").json() == []


def test_no_token_refuses_sandbox(tmp_path) -> None:
    from automation_lab.app import create_app
    from tests.conftest import make_settings

    c = TestClient(create_app(make_settings(tmp_path, sandbox_token="")))
    assert c.get("/sandbox/status", headers={"X-Sandbox-Token": ""}).status_code == 401


def test_latency_is_applied(tmp_path) -> None:
    from automation_lab.app import create_app
    from tests.conftest import make_settings

    c = TestClient(create_app(make_settings(tmp_path, latency_ms=1)))
    assert c.get("/api/policies").json()["total"] == 57
    assert c.get("/api/feed").json()["next"] == 10


def test_data_survives_restart(settings) -> None:
    from automation_lab.app import create_app

    TestClient(create_app(settings)).post("/api/quotes", json=quote())
    assert len(TestClient(create_app(settings)).get("/api/quotes").json()) == 1
