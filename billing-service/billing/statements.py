"""Monthly billing statements (BR-23): every installment a customer's policies have due in a calendar month."""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from ._vendor import ordinals
from .errors import BillingError

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


@dataclass(frozen=True)
class StatementLine:
    policy_id: str
    installment: int
    due_date: date
    total: Decimal
    paid: bool

    @property
    def label(self) -> str:
        """'11th installment', as members read it on the statement."""
        return f"{ordinals.ordinal(self.installment)} installment"


def month_bounds(month: str) -> tuple[date, date]:
    """'2026-11' -> (2026-11-01, 2026-12-01): the first day of the month and the first day of the next."""
    if not MONTH.match(month):
        raise BillingError(f"month must look like 2026-11, not {month!r}")
    first = date.fromisoformat(f"{month}-01")
    return first, date(first.year, first.month + 1, 1)


def statement_lines(month: str, installments: Iterable[Mapping[str, Any]]) -> list[StatementLine]:
    """The installments due in `month`, by due date then policy. `installments` are store rows."""
    start, end = month_bounds(month)
    lines = [
        StatementLine(row["policy_id"], row["number"], date.fromisoformat(row["due_date"]),
                      Decimal(row["amount"]) + Decimal(row["fee"]), row["paid_on"] is not None)
        for row in installments
        if start <= date.fromisoformat(row["due_date"]) < end
    ]
    return sorted(lines, key=lambda line: (line.due_date, line.policy_id))


def amount_due(lines: Iterable[StatementLine]) -> Decimal:
    return sum((line.total for line in lines if not line.paid), Decimal("0.00"))
