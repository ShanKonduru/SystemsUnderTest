"""Refunds on cancellation (RF-01 to RF-05 in docs/specs/refund-rules.md)."""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .errors import BillingError
from .money import to_money
from .renewals import renewal_date, term_days


@dataclass(frozen=True)
class RefundQuote:
    kind: str  # free_look or pro_rata
    paid: Decimal
    earned: Decimal
    fee: Decimal
    refund: Decimal
    days_in_force: int
    term_days: int


def premium_paid(installments: Iterable[Mapping[str, Any]]) -> Decimal:
    """The premium the member has paid so far, from the payments recorded against each installment."""
    return sum((Decimal(row["paid_amount"]) for row in installments if row["paid_on"] is not None), Decimal("0.00"))


def refund_quote(
    *,
    annual_premium: Decimal,
    effective: date,
    cancel_date: date,
    paid: Decimal,
    free_look_days: int,
    cancellation_fee: Decimal,
) -> RefundQuote:
    """`paid` is the premium paid so far, without installment or late fees (those are never refunded, RF-05).

    Within the free-look period everything paid comes back and there is no fee (RF-02). After it, the insurer keeps
    the premium earned for the days in force, counting the cancellation day as a day in force (RF-03), and the
    cancellation fee (RF-04); the refund is never negative.
    """
    end = renewal_date(effective)
    if not effective <= cancel_date < end:
        raise BillingError(f"cancel_date must be within the term {effective} to {end}")
    days = term_days(effective)
    in_force = (cancel_date - effective).days + 1
    if in_force <= free_look_days:
        return RefundQuote("free_look", paid, Decimal("0.00"), Decimal("0.00"), paid, in_force, days)
    earned = to_money(annual_premium * in_force / days)
    refund = max(Decimal("0.00"), paid - earned - cancellation_fee)
    return RefundQuote("pro_rata", paid, earned, cancellation_fee, refund, in_force, days)
