from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from billing.endorsements import endorsement_quote
from billing.errors import BillingError
from billing.rates import RateTable

POLICY = {"id": "P-1", "state": "NY", "product": "auto", "coverage": "basic", "driver_age": 22,
          "effective_date": "2026-06-15", "annual_premium": "2251.21"}


def test_upgrade_mid_term(rates: RateTable) -> None:
    e = endorsement_quote(rates, POLICY, coverage="plus", on=date(2026, 11, 3), policy_count=1, paperless=False)
    # 1655.30 x 1.25 x 1.60 = 3310.60; Nov 3 to Jun 14 is 224 days
    assert (e.new_premium, e.days_remaining, e.term_days) == (Decimal("3310.60"), 224, 365)
    assert e.adjustment == Decimal("650.15")  # 1059.39 x 224 / 365 = 650.1488


def test_last_day_of_the_term(rates: RateTable) -> None:
    e = endorsement_quote(rates, POLICY, coverage="standard", on=date(2027, 6, 14), policy_count=1, paperless=False)
    assert e.days_remaining == 1


def test_refused(rates: RateTable) -> None:
    with pytest.raises(BillingError, match="within the term"):
        endorsement_quote(rates, POLICY, coverage="plus", on=date(2027, 6, 15), policy_count=1, paperless=False)
    with pytest.raises(BillingError, match="already has basic"):
        endorsement_quote(rates, POLICY, coverage="basic", on=date(2026, 11, 3), policy_count=1, paperless=False)


def test_endorsement_quote_api(client: TestClient) -> None:
    r = client.get("/policies/P-5008/endorsement-quote", params={"coverage": "plus"})
    assert r.json() == {
        "policy_id": "P-5008", "on": "2026-11-03", "coverage_from": "standard", "coverage_to": "plus",
        "old_premium": "214.30", "new_premium": "267.88", "days_remaining": 359, "term_days": 365,
        "adjustment": "52.70",
    }
    later = client.get("/policies/P-5003/endorsement-quote", params={"coverage": "standard", "on": "2027-01-01"})
    assert later.json()["adjustment"] == "179.59"  # 397.27 x 165 / 365
    assert client.get("/policies/P-5003/endorsement-quote", params={"coverage": "gold"}).status_code == 422
    client.post("/policies/P-5008/cancel", json={"cancel_date": "2026-11-03"})
    r = client.get("/policies/P-5008/endorsement-quote", params={"coverage": "plus"})
    assert r.status_code == 409
