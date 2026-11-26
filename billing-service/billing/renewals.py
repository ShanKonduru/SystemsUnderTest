"""Policy standing, renewal dates and renewal notices (BR-08, BR-09, BR-13, BR-14)."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta

from ._vendor import holidaycal
from .dates import add_months

TERM_MONTHS = 12


@dataclass(frozen=True)
class Standing:
    status: str  # active, past_due, lapsed or cancelled
    installment: int | None = None
    due_date: date | None = None
    grace_ends: date | None = None
    lapse_date: date | None = None


def renewal_date(effective: date) -> date:
    return add_months(effective, TERM_MONTHS)


def term_days(effective: date) -> int:
    """365, or 366 when the term includes a February 29."""
    return (renewal_date(effective) - effective).days


def standing(cancelled: bool, unpaid: Iterable[tuple[int, date]], today: date, grace_days: int) -> Standing:
    """`unpaid` is (number, due date) of the unpaid installments. The earliest one already due decides (BR-08):
    within the grace period the policy is past due; after it, it has lapsed, from the day after the grace ends."""
    if cancelled:
        return Standing("cancelled")
    overdue = [(number, due) for number, due in unpaid if due < today]
    if not overdue:
        return Standing("active")
    number, due = min(overdue, key=lambda item: item[1])
    grace_ends = due + timedelta(days=grace_days)
    if today > grace_ends:
        return Standing("lapsed", number, due, grace_ends, grace_ends + timedelta(days=1))
    return Standing("past_due", number, due, grace_ends)


def notice_date(renewal: date, notice_days: int) -> date:
    """The renewal notice goes out `notice_days` before renewal, or on the business day before that (BR-14)."""
    day = renewal - timedelta(days=notice_days)
    while not holidaycal.is_business_day(day):
        day -= timedelta(days=1)
    return day
