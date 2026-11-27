"""Installment schedules (BR-05 to BR-07)."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .dates import add_months
from .errors import BillingError
from .money import from_cents, to_cents

PLANS = {"annual": 1, "semiannual": 2, "quarterly": 4, "bimonthly": 6, "monthly": 12}


@dataclass(frozen=True)
class Installment:
    number: int
    due_date: date
    amount: Decimal
    fee: Decimal

    @property
    def total(self) -> Decimal:
        return self.amount + self.fee


def build_schedule(premium: Decimal, plan: str, effective: date, fees: Mapping[str, Decimal]) -> list[Installment]:
    """Split the premium evenly, leftover cents on the first installment; the first is due on the effective date
    and the rest every 12/n months on the same day of the month, clamped to the month's end."""
    if plan not in PLANS:
        raise BillingError(f"unknown plan {plan!r}")
    if premium <= 0:
        raise BillingError("premium must be positive")
    count = PLANS[plan]
    base, leftover = divmod(to_cents(premium), count)
    return [
        Installment(
            number=k + 1,
            due_date=add_months(effective, k * (12 // count)),
            amount=from_cents(base + (leftover if k == 0 else 0)),
            fee=fees[plan],
        )
        for k in range(count)
    ]
