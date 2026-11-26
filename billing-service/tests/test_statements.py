from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.api import create_app
from billing.config import load_settings
from billing.errors import BillingError
from billing.statements import StatementLine, amount_due, month_bounds, statement_lines

# One service for the module: the checks below follow one customer through the month.
DATA = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def service() -> Iterator[TestClient]:
    with TestClient(create_app(replace(load_settings({}), data_dir=DATA))) as c:
        yield c


def test_month_bounds() -> None:
    assert month_bounds("2026-11") == (date(2026, 11, 1), date(2026, 12, 1))
    assert month_bounds("2027-02") == (date(2027, 2, 1), date(2027, 3, 1))
    for bad in ("2026-13", "2026-1", "Nov 2026"):
        with pytest.raises(BillingError, match="month must look like 2026-11"):
            month_bounds(bad)


def test_lines_and_amount_due() -> None:
    rows = [
        {"policy_id": "P-2", "number": 4, "due_date": "2026-11-20", "amount": "93.95", "fee": "3.00", "paid_on": None},
        {"policy_id": "P-1", "number": 11, "due_date": "2026-11-30", "amount": "117.63", "fee": "3.00",
         "paid_on": "2026-11-03"},
        {"policy_id": "P-1", "number": 12, "due_date": "2026-12-31", "amount": "117.63", "fee": "3.00", "paid_on": None},
    ]
    lines = statement_lines("2026-11", rows)
    assert [(line.policy_id, line.installment) for line in lines] == [("P-2", 4), ("P-1", 11)]
    assert amount_due(lines) == Decimal("96.95")
    assert amount_due([StatementLine("P-3", 1, date(2026, 11, 1), Decimal("10.00"), True)]) == Decimal("0.00")


def test_statement(service: TestClient) -> None:
    body = service.get("/customers/C-1001/statement", params={"month": "2026-11"}).json()
    assert body == {"customer_id": "C-1001", "name": "Asha Rao", "month": "2026-11",
                    "lines": [{"policy_id": "P-5001", "installment": 11, "due_date": "2026-11-30", "total": "120.63",
                               "paid": False}],
                    "amount_due": "120.63"}


def test_a_payment_shows_on_the_statement(service: TestClient) -> None:
    service.post("/policies/P-5001/payments", json={"amount": "120.63", "received_at": "2026-11-03T15:00:00Z"})
    body = service.get("/customers/C-1001/statement", params={"month": "2026-11"}).json()
    assert (body["lines"][0]["paid"], body["amount_due"]) == (True, "0.00")


def test_cancelled_policies_and_errors(service: TestClient) -> None:
    service.post("/policies/P-5002/cancel", json={"cancel_date": "2026-11-03"})
    body = service.get("/customers/C-1001/statement", params={"month": "2026-10"}).json()
    assert [line["policy_id"] for line in body["lines"]] == ["P-5001"]
    assert service.get("/customers/C-1001/statement", params={"month": "2026-13"}).status_code == 422
    assert service.get("/customers/C-9999/statement", params={"month": "2026-11"}).status_code == 404
