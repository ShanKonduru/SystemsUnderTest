"""Reinstating a lapsed policy (BR-22): within the reinstatement window, by paying what is due plus a fee."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .errors import Conflict
from .installments import Installment
from .renewals import Standing


@dataclass(frozen=True)
class Reinstatement:
    lapse_date: date
    last_day: date
    installments: list[int]
    fee: Decimal
    amount_due: Decimal


def reinstatement_terms(
    standing: Standing, unpaid: Iterable[Installment], day: date, window_days: int, fee: Decimal
) -> Reinstatement:
    """What reinstating on `day` takes: every unpaid installment due by then, plus the reinstatement fee."""
    if standing.status != "lapsed" or standing.lapse_date is None:
        raise Conflict(f"only a lapsed policy can be reinstated; this one is {standing.status}")
    last_day = standing.lapse_date + timedelta(days=window_days)
    if day >= last_day:
        raise Conflict(f"the policy lapsed on {standing.lapse_date}; it could be reinstated until {last_day}")
    due = [i for i in unpaid if i.due_date <= day]
    return Reinstatement(standing.lapse_date, last_day, [i.number for i in due], fee,
                         sum((i.total for i in due), Decimal("0.00")) + fee)
