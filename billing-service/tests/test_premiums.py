from decimal import Decimal

import pytest

from billing.errors import BillingError
from billing.premiums import age_factor, multi_policy_discount, quote_premium
from billing.rates import RateTable, UnknownTerritory


def test_quote_breakdown(rates: RateTable) -> None:
    # NJ auto, standard, 34-year-old driver, two policies (5%) and paperless (2%): 1517.85 x 0.93
    q = quote_premium(rates, state="NJ", product="auto", coverage="standard", driver_age=34, policy_count=2,
                      paperless=True)
    assert q.base_rate == Decimal("1517.85")
    assert (q.coverage_factor, q.age_factor) == (Decimal("1.00"), Decimal("1.00"))
    assert q.subtotal == Decimal("1517.85")
    assert q.discount_rate == Decimal("0.07")
    assert q.multi_policy_discount == Decimal("75.89")  # 5% of 1517.85 = 75.8925
    assert q.paperless_discount == Decimal("30.36")  # 2% = 30.357
    assert q.premium == Decimal("1411.60")
    assert q.discount == Decimal("106.25")


def test_young_driver_basic_coverage(rates: RateTable) -> None:
    # 1655.30 x 0.85 x 1.60 = 2251.208
    q = quote_premium(rates, state="NY", product="auto", coverage="basic", driver_age=22)
    assert q.premium == Decimal("2251.21")
    assert q.discount == q.multi_policy_discount == q.paperless_discount == Decimal("0.00")


def test_only_the_final_premium_is_rounded(rates: RateTable) -> None:
    # 1517.85 x 1.25 x 1.15 = 2181.9093750; x 0.88 = 1920.080250
    q = quote_premium(rates, state="NJ", product="auto", coverage="plus", driver_age=67, policy_count=3,
                      paperless=True)
    assert q.subtotal == Decimal("2181.91")
    assert q.premium == Decimal("1920.08")


@pytest.mark.parametrize(
    ("age", "factor"), [(16, "1.60"), (24, "1.60"), (25, "1.00"), (64, "1.00"), (65, "1.15"), (99, "1.15")]
)
def test_age_bands(age: int, factor: str) -> None:
    assert age_factor("auto", age) == Decimal(factor)


def test_age_only_matters_for_auto() -> None:
    assert age_factor("home", None) == Decimal("1.00")
    with pytest.raises(BillingError, match="needs the driver's age"):
        age_factor("auto", None)
    with pytest.raises(BillingError, match="between 16 and 99"):
        age_factor("auto", 15)


@pytest.mark.parametrize(("count", "rate"), [(1, "0.00"), (2, "0.05"), (3, "0.10"), (5, "0.10")])
def test_multi_policy_discount(count: int, rate: str) -> None:
    assert multi_policy_discount(count) == Decimal(rate)


def test_invalid_quotes(rates: RateTable) -> None:
    with pytest.raises(BillingError, match="policy_count"):
        multi_policy_discount(0)
    with pytest.raises(BillingError, match="unknown product"):
        quote_premium(rates, state="NJ", product="boat", coverage="standard")
    with pytest.raises(BillingError, match="unknown coverage"):
        quote_premium(rates, state="NJ", product="home", coverage="gold")
    with pytest.raises(UnknownTerritory):
        quote_premium(rates, state="TX", product="home", coverage="standard")
