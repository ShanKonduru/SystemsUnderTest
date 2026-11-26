"""Calendar helpers."""

import calendar
from datetime import date


def add_months(anchor: date, months: int) -> date:
    """The same day `months` later, clamped to the end of a shorter month (Jan 31 + 1 month is Feb 28)."""
    total = anchor.month - 1 + months
    year, month = anchor.year + total // 12, total % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)
