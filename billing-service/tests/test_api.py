from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing import __main__ as entry
from billing import api
from billing.api import create_app
from billing.config import Settings
from billing.store import Store
from tests.conftest import TOKEN


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok", "version": "1.0.0", "today": "2026-11-03"}


def test_quote(client: TestClient) -> None:
    r = client.post("/quotes", json={"state": "NJ", "product": "auto", "driver_age": 34, "policy_count": 2,
                                     "paperless": True})
    assert r.status_code == 200
    assert r.json() == {
        "base_rate": "1517.85", "coverage_factor": "1.00", "age_factor": "1.00", "subtotal": "1517.85",
        "discount_rate": "0.07", "discount": "106.25", "premium": "1411.60", "premium_tax": "29.64",
        "total": "1441.24", "currency": "USD",
    }


def test_quote_errors_are_4xx(client: TestClient) -> None:
    r = client.post("/quotes", json={"state": "TX", "product": "home"})
    assert r.status_code == 422
    assert r.json() == {"error": "unknown_territory", "detail": "no home rate for state 'TX'"}
    assert client.post("/quotes", json={"state": "NJ", "product": "auto"}).json()["error"] == "invalid"


def test_customer(client: TestClient) -> None:
    assert client.get("/customers/C-1004").json() == {
        "id": "C-1004", "name": "Dana Park", "state": "NJ", "paperless": True,
        "policies": ["P-5005", "P-5006", "P-5007"],
    }
    r = client.get("/customers/C-9999")
    assert (r.status_code, r.json()["error"]) == (404, "not_found")


@pytest.mark.parametrize(
    ("policy_id", "premium", "status"),
    [
        ("P-5001", "1411.60", "active"),
        ("P-5002", "1276.66", "active"),
        ("P-5003", "2251.21", "active"),
        ("P-5004", "1127.45", "lapsed"),
        ("P-5005", "1920.08", "past_due"),
        ("P-5006", "966.42", "active"),
        ("P-5007", "148.59", "active"),
        ("P-5008", "214.30", "active"),
    ],
)
def test_seeded_policies(client: TestClient, policy_id: str, premium: str, status: str) -> None:
    p = client.get(f"/policies/{policy_id}").json()
    assert (p["annual_premium"], p["status"]) == (premium, status)


def test_policy_standing_details(client: TestClient) -> None:
    lapsed = client.get("/policies/P-5004").json()
    assert lapsed["renewal_date"] == "2027-08-20"
    assert lapsed["standing"] == {"installment": 3, "due_date": "2026-10-20", "grace_ends": "2026-10-25",
                                  "lapse_date": "2026-10-26"}
    assert lapsed["next_payment"] is None
    assert client.get("/policies/P-9999").status_code == 404


def test_next_payment_includes_the_late_fee_once_overdue(client: TestClient) -> None:
    assert client.get("/policies/P-5005").json()["next_payment"] == {
        "installment": 2, "due_date": "2026-11-01", "late_fee": "10.00", "amount_due": "970.04"}
    assert client.get("/policies/P-5001").json()["next_payment"] == {
        "installment": 11, "due_date": "2026-11-30", "late_fee": "0.00", "amount_due": "120.63"}
    assert client.get("/policies/P-5003").json()["next_payment"] is None


def test_schedule(client: TestClient) -> None:
    body = client.get("/policies/P-5001/schedule").json()
    assert body["plan"] == "monthly"
    first, last = body["installments"][0], body["installments"][-1]
    assert (first["due_date"], first["amount"], first["fee"], first["total"], first["paid_on"]) == (
        "2026-01-31", "117.67", "3.00", "120.67", "2026-01-31")
    assert (last["due_date"], last["paid_on"]) == ("2026-12-31", None)


def test_payment_on_the_due_date_in_eastern_time(client: TestClient, settings: Settings) -> None:
    r = client.post("/policies/P-5005/payments", json={"amount": "960.04", "received_at": "2026-11-01T22:00:00-05:00"})
    assert r.status_code == 200
    assert r.json() == {"policy_id": "P-5005", "installment": 2, "received_on": "2026-11-01", "late": False,
                        "late_fee": "0.00", "amount": "960.04"}
    assert client.get("/policies/P-5005").json()["status"] == "active"
    log = settings.log_path.read_text(encoding="utf-8")
    assert "payment policy=P-5005 installment=2 received_at=2026-11-01T22:00:00-05:00 received_on=2026-11-01" in log


def test_late_payment_within_grace(client: TestClient) -> None:
    r = client.post("/policies/P-5005/payments", json={"amount": "960.04", "received_at": "2026-11-03T15:00:00Z"})
    assert r.status_code == 422
    assert "including the 10.00 late fee" in r.json()["detail"]
    r = client.post("/policies/P-5005/payments", json={"amount": "970.04", "received_at": "2026-11-03T15:00:00Z"})
    assert r.json()["late"] is True


def test_payments_refused(client: TestClient) -> None:
    lapsed = client.post("/policies/P-5004/payments", json={"amount": "97.00", "received_at": "2026-11-03T15:00:00Z"})
    assert (lapsed.status_code, lapsed.json()["error"]) == (409, "conflict")
    paid_up = client.post("/policies/P-5003/payments", json={"amount": "1", "received_at": "2026-11-03T15:00:00Z"})
    assert "nothing left to pay" in paid_up.json()["detail"]
    naive = client.post("/policies/P-5005/payments", json={"amount": "960.04", "received_at": "2026-11-01T22:00:00"})
    assert naive.json()["detail"] == "received_at must include a UTC offset"


def test_renewal(client: TestClient) -> None:
    assert client.get("/policies/P-5001/renewal").json() == {
        "policy_id": "P-5001", "renewal_date": "2027-01-31", "notice_date": "2026-12-17", "eligible": True,
        "reason": None, "renewal_premium": "1411.60",
    }
    lapsed = client.get("/policies/P-5004/renewal").json()
    assert (lapsed["eligible"], lapsed["reason"], lapsed["renewal_premium"]) == (False, "policy is lapsed", None)


def test_refund_quote_and_cancel(client: TestClient) -> None:
    preview = client.get("/policies/P-5008/refund-quote", params={"cancel_date": "2026-11-03"}).json()
    assert preview == {"policy_id": "P-5008", "kind": "free_look", "paid": "107.15", "earned": "0.00", "fee": "0.00",
                       "refund": "107.15", "days_in_force": 7, "term_days": 365}

    r = client.post("/policies/P-5003/cancel", json={"cancel_date": "2026-11-03"})
    assert r.json() == {"policy_id": "P-5003", "kind": "pro_rata", "paid": "2251.21", "earned": "875.81",
                        "fee": "25.00", "refund": "1350.40", "days_in_force": 142, "term_days": 365,
                        "status": "cancelled"}
    p = client.get("/policies/P-5003").json()
    assert (p["status"], p["cancelled_on"], p["refund"]) == ("cancelled", "2026-11-03", "1350.40")
    assert client.post("/policies/P-5003/cancel", json={"cancel_date": "2026-11-04"}).status_code == 409
    paid = client.post("/policies/P-5003/payments", json={"amount": "1", "received_at": "2026-11-03T15:00:00Z"})
    assert "was cancelled" in paid.json()["detail"]
    assert client.get("/policies/P-5003/renewal").json()["reason"] == "policy is cancelled"


def test_refund_quote_outside_the_term(client: TestClient) -> None:
    r = client.get("/policies/P-5003/refund-quote", params={"cancel_date": "2026-01-01"})
    assert r.status_code == 422


def test_unexpected_errors_are_logged_with_the_request_id(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(self: Store, customer_id: str) -> None:
        raise RuntimeError("database is locked")

    monkeypatch.setattr(Store, "customer", boom)
    r = client.get("/customers/C-1001", headers={"X-Request-Id": "req-test-1"})
    assert r.status_code == 500
    assert r.json() == {"error": "internal", "request_id": "req-test-1"}
    assert r.headers["X-Request-Id"] == "req-test-1"
    log = settings.log_path.read_text(encoding="utf-8")
    assert "unhandled error on GET /customers/C-1001 request_id=req-test-1" in log
    assert "Traceback (most recent call last)" in log and "RuntimeError: database is locked" in log


def test_request_ids_are_generated(client: TestClient) -> None:
    assert client.get("/health").headers["X-Request-Id"].startswith("req-")


def test_portal_needs_a_build(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(api, "PORTAL_DIST", tmp_path / "dist")
    r = client.get("/portal")
    assert (r.status_code, r.json()["error"]) == (503, "portal_not_built")


def test_portal_serves_the_built_page(client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<title>My billing</title>", encoding="utf-8")
    (dist / "app.js").write_text("export {};", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("no", encoding="utf-8")
    monkeypatch.setattr(api, "PORTAL_DIST", dist)
    assert "My billing" in client.get("/portal").text
    assert client.get("/portal/app.js").headers["content-type"].startswith(("text/javascript", "application/javascript"))
    assert client.get("/portal/missing.js").status_code == 404
    assert client.get("/portal/..%2Fsecret.txt").status_code == 404


def test_sandbox_needs_the_token(client: TestClient) -> None:
    assert client.get("/sandbox/status").status_code == 403
    assert client.post("/sandbox/reset", headers={"X-Sandbox-Token": "wrong"}).status_code == 403
    status = client.get("/sandbox/status", headers={"X-Sandbox-Token": TOKEN}).json()
    assert status == {"today": "2026-11-03", "version": "1.0.0", "customers": 5, "policies": 8, "installments": 35}


def test_sandbox_is_off_without_a_token(settings: Settings) -> None:
    with TestClient(create_app(replace(settings, sandbox_token=""))) as c:
        assert c.get("/sandbox/status", headers={"X-Sandbox-Token": ""}).status_code == 403


def test_sandbox_reset_restores_the_seed(client: TestClient, settings: Settings) -> None:
    client.post("/policies/P-5003/cancel", json={"cancel_date": "2026-11-03"})
    r = client.post("/sandbox/reset", headers={"X-Sandbox-Token": TOKEN})
    assert r.json() == {"reset": True, "customers": 5, "policies": 8, "installments": 35}
    assert client.get("/policies/P-5003").json()["status"] == "active"


def test_restart_keeps_the_data(settings: Settings) -> None:
    with TestClient(create_app(settings)) as c:
        c.post("/policies/P-5003/cancel", json={"cancel_date": "2026-11-03"})
    with TestClient(create_app(settings)) as c:
        assert c.get("/policies/P-5003").json()["status"] == "cancelled"


def test_main_serves_on_localhost(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    calls: list[tuple[str, int]] = []
    monkeypatch.setattr(entry, "create_app", lambda: "app")
    monkeypatch.setattr(entry.uvicorn, "run", lambda app, host, port: calls.append((host, port)))
    monkeypatch.setenv("BILLING_PORT", "8599")
    entry.main()
    assert calls == [("127.0.0.1", 8599)]
