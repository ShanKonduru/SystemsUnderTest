from __future__ import annotations

from dataclasses import replace

import jwt
import pytest
from fastapi.testclient import TestClient

from billing import receipts
from billing.api import create_app
from billing.config import Settings
from billing.errors import BillingError

KEY = "receipt-test-key-0123456789abcdef0123456789abcdef"
PAYMENT = {"amount": "120.63", "received_at": "2026-11-03T10:00:00-05:00"}


@pytest.fixture
def keyed(settings: Settings) -> TestClient:
    return TestClient(create_app(replace(settings, receipt_key=KEY)))


def test_issue_and_verify() -> None:
    token = receipts.issue({"policy_id": "P-5001", "amount": "120.63"}, KEY)
    assert receipts.verify(token, KEY) == {"policy_id": "P-5001", "amount": "120.63"}


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        receipts.issue({"policy_id": "P-5001"}, "another-key-0123456789abcdef0123456789abcdef"),
        jwt.encode({"iss": "someone-else", "policy_id": "P-5001"}, KEY, algorithm="HS256"),
    ],
)
def test_a_forged_or_foreign_receipt_is_refused(token: str) -> None:
    with pytest.raises(BillingError, match="not a valid receipt"):
        receipts.verify(token, KEY)


def test_a_payment_returns_a_receipt_that_checks_out(keyed: TestClient) -> None:
    paid = keyed.post("/policies/P-5001/payments", json=PAYMENT).json()
    claims = receipts.verify(paid["receipt"], KEY)
    assert claims == {"policy_id": "P-5001", "installment": 11, "received_on": "2026-11-03", "late": False,
                      "late_fee": "0.00", "amount": "120.63"}
    assert keyed.get(f"/receipts/{paid['receipt']}").json() == {"valid": True, **claims}


def test_a_bad_receipt_is_refused(keyed: TestClient) -> None:
    response = keyed.get("/receipts/not-a-token")
    assert response.status_code == 422
    assert response.json()["error"] == "invalid"


def test_without_a_key_receipts_still_work_until_restart(client: TestClient) -> None:
    paid = client.post("/policies/P-5001/payments", json=PAYMENT).json()
    assert client.get(f"/receipts/{paid['receipt']}").json()["valid"] is True
