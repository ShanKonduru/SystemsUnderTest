from decimal import Decimal

from fastapi.testclient import TestClient

from billing.config import Settings


def test_settings_have_the_returned_payment_fee(settings: Settings) -> None:
    assert settings.returned_payment_fee == Decimal("15.00")


def test_returned_payment(client: TestClient, settings: Settings) -> None:
    r = client.post("/policies/P-5005/installments/1/return")
    assert r.status_code == 200
    assert r.json() == {"policy_id": "P-5005", "installment": 1, "returned_payment_fee": "15.00", "fee": "15.00",
                        "amount_due": "975.04"}
    first = client.get("/policies/P-5005/schedule").json()["installments"][0]
    assert (first["paid_on"], first["paid_amount"], first["fee"], first["total"]) == (None, None, "15.00", "975.04")
    assert "returned payment policy=P-5005 installment=1 fee=15.00" in settings.log_path.read_text(encoding="utf-8")


def test_returned_payment_refused(client: TestClient) -> None:
    assert client.post("/policies/P-5005/installments/2/return").status_code == 409  # not paid
    assert client.post("/policies/P-5005/installments/7/return").status_code == 404
    client.post("/policies/P-5003/cancel", json={"cancel_date": "2026-11-03"})
    assert client.post("/policies/P-5003/installments/1/return").status_code == 409
