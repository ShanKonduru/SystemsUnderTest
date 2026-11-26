from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from billing.taxes import premium_tax


@pytest.mark.parametrize(
    ("state", "premium", "tax"),
    [
        ("NJ", "1411.60", "29.64"),  # 29.6436
        ("NY", "2251.21", "45.02"),  # 45.0242
        ("CT", "214.30", "3.75"),  # 3.75025
        ("pa", "1000.00", "20.00"),
    ],
)
def test_premium_tax(state: str, premium: str, tax: str) -> None:
    assert premium_tax(state, Decimal(premium)) == Decimal(tax)


def test_quote_shows_tax_and_total(client: TestClient) -> None:
    q = client.post("/quotes", json={"state": "NY", "product": "auto", "coverage": "basic", "driver_age": 22}).json()
    assert (q["premium"], q["premium_tax"], q["total"]) == ("2251.21", "45.02", "2296.23")


def test_vermont_auto_quote(client: TestClient) -> None:
    q = client.post("/quotes", json={"state": "VT", "product": "auto", "driver_age": 40}).json()
    assert (q["base_rate"], q["premium"]) == ("1350.60", "1350.60")
