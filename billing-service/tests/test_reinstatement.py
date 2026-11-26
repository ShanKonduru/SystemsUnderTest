from collections.abc import Callable
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from billing.api import create_app
from billing.config import Settings
from billing.errors import Conflict
from billing.installments import Installment
from billing.reinstatement import reinstatement_terms
from billing.renewals import Standing

LAPSED = Standing("lapsed", 3, date(2026, 10, 20), date(2026, 10, 30), date(2026, 10, 31))
UNPAID = [Installment(3, date(2026, 10, 20), Decimal("93.95"), Decimal("3.00")),
          Installment(4, date(2026, 11, 20), Decimal("93.95"), Decimal("3.00"))]
FEE = Decimal("50.00")


def test_terms_cover_every_installment_due() -> None:
    early = reinstatement_terms(LAPSED, UNPAID, date(2026, 11, 3), 30, FEE)
    assert (early.installments, early.amount_due, early.last_day) == ([3], Decimal("146.95"), date(2026, 11, 30))
    later = reinstatement_terms(LAPSED, UNPAID, date(2026, 11, 20), 30, FEE)
    assert (later.installments, later.amount_due) == ([3, 4], Decimal("243.90"))


def test_terms_refused() -> None:
    with pytest.raises(Conflict, match="only a lapsed policy"):
        reinstatement_terms(Standing("past_due", 2, date(2026, 11, 1), date(2026, 11, 11)), UNPAID,
                            date(2026, 11, 3), 30, FEE)
    with pytest.raises(Conflict, match="reinstated until 2026-11-30"):
        reinstatement_terms(LAPSED, UNPAID, date(2026, 12, 5), 30, FEE)


@pytest.fixture
def client_on(settings: Settings, tmp_path: Path) -> Callable[[date], TestClient]:
    def make(today: date) -> TestClient:
        return TestClient(create_app(replace(settings, today=today, data_dir=tmp_path / today.isoformat())))
    return make


def test_reinstate(client: TestClient, settings: Settings) -> None:
    terms = client.get("/policies/P-5004/reinstatement").json()
    due = Decimal("96.95") + settings.reinstatement_fee
    assert terms == {"policy_id": "P-5004", "lapse_date": "2026-10-26", "last_day": "2026-11-25", "installments": [3],
                     "reinstatement_fee": str(settings.reinstatement_fee), "amount_due": str(due)}
    wrong = client.post("/policies/P-5004/reinstate", json={"amount": "96.95", "received_at": "2026-11-03T15:00:00Z"})
    assert wrong.status_code == 422 and "reinstatement fee" in wrong.json()["detail"]
    r = client.post("/policies/P-5004/reinstate", json={"amount": str(due), "received_at": "2026-11-03T15:00:00Z"})
    assert (r.status_code, r.json()["status"], r.json()["received_on"]) == (200, "active", "2026-11-03")
    assert client.get("/policies/P-5004/schedule").json()["installments"][2]["paid_on"] == "2026-11-03"
    assert client.get("/policies/P-5004/reinstatement").status_code == 409


def test_too_late_to_reinstate(client_on: Callable[[date], TestClient]) -> None:
    r = client_on(date(2026, 12, 1)).get("/policies/P-5004/reinstatement")
    assert r.status_code == 409
    assert r.json()["detail"] == "the policy lapsed on 2026-10-26; it could be reinstated until 2026-11-25"
