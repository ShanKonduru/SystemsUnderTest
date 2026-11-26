"""Premium quotes (BR-01 to BR-04 in docs/specs/billing-rules.md)."""

from dataclasses import dataclass
from decimal import Decimal

from .errors import BillingError
from .money import to_money
from .rates import PRODUCTS, RateTable

COVERAGE_FACTORS = {"basic": Decimal("0.85"), "standard": Decimal("1.00"), "plus": Decimal("1.25")}
PAPERLESS_DISCOUNT = Decimal("0.02")


@dataclass(frozen=True)
class Quote:
    base_rate: Decimal
    coverage_factor: Decimal
    age_factor: Decimal
    subtotal: Decimal
    discount_rate: Decimal
    discount: Decimal
    premium: Decimal


def age_factor(product: str, driver_age: int | None) -> Decimal:
    """Only auto premiums depend on the driver's age (BR-02)."""
    if product != "auto":
        return Decimal("1.00")
    if driver_age is None:
        raise BillingError("an auto quote needs the driver's age")
    if not 16 <= driver_age <= 99:
        raise BillingError("driver age must be between 16 and 99")
    if driver_age < 25:
        return Decimal("1.60")
    if driver_age >= 65:
        return Decimal("1.15")
    return Decimal("1.00")


def multi_policy_discount(policy_count: int) -> Decimal:
    """BR-03: 5% with two active policies, 10% with three or more."""
    if policy_count < 1:
        raise BillingError("policy_count must be at least 1")
    if policy_count >= 3:
        return Decimal("0.10")
    elif policy_count == 2:
        return Decimal("0.05")
    return Decimal("0.00")


def quote_premium(
    rates: RateTable,
    *,
    state: str,
    product: str,
    coverage: str,
    driver_age: int | None = None,
    policy_count: int = 1,
    paperless: bool = False,
) -> Quote:
    """The annual premium. Factors multiply, discounts add up, and only the result is rounded (BR-04)."""
    if product not in PRODUCTS:
        raise BillingError(f"unknown product {product!r}")
    if coverage not in COVERAGE_FACTORS:
        raise BillingError(f"unknown coverage {coverage!r}")
    base = rates.base_rate(state, product)
    cov, age = COVERAGE_FACTORS[coverage], age_factor(product, driver_age)
    subtotal = base * cov * age
    discount_rate = multi_policy_discount(policy_count) + (PAPERLESS_DISCOUNT if paperless else Decimal("0.00"))
    premium = to_money(subtotal * (1 - discount_rate))
    return Quote(
        base_rate=base,
        coverage_factor=cov,
        age_factor=age,
        subtotal=to_money(subtotal),
        discount_rate=discount_rate,
        discount=to_money(subtotal) - premium,
        premium=premium,
    )
