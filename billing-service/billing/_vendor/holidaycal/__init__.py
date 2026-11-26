# holidaycal - US federal holiday calendar
# Copyright (c) 2021-2026 Tidewater Software Cooperative. MIT License, see LICENSE in this directory.
"""US federal holidays (5 U.S.C. 6103) and business-day arithmetic.

    >>> from holidaycal import holidays, is_business_day
    >>> holidays(2026)[datetime.date(2026, 11, 26)]
    'Thanksgiving Day'

A holiday on a Saturday is observed on the Friday before; on a Sunday, on the Monday after.
"""
import datetime as _dt

__version__ = "1.3.0"
__all__ = [
    "holidays", "is_holiday", "is_weekend", "is_business_day", "next_business_day", "previous_business_day",
]

_MON, _THU, _SAT, _SUN = 0, 3, 5, 6


# Floating holidays: (name, month, weekday, n), the n-th weekday of the month; n = -1 is the last one.
_FLOATING = (
    ("Birthday of Martin Luther King, Jr.", 1, _MON, 3),
    ("Washington's Birthday", 2, _MON, 3),
    ("Memorial Day", 5, _MON, -1),
    ("Labor Day", 9, _MON, 1),
    ("Columbus Day", 10, _MON, 2),
    ("Thanksgiving Day", 11, _THU, -1),
)


def _nth_weekday(year, month, weekday, n):
    """The n-th `weekday` (0 = Monday) of the month; n = -1 for the last one."""
    if n == -1:
        if month == 12:
            last = _dt.date(year, 12, 31)
        else:
            last = _dt.date(year, month + 1, 1) - _dt.timedelta(days=1)
        return last - _dt.timedelta(days=(last.weekday() - weekday) % 7)
    first = _dt.date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + _dt.timedelta(days=offset + 7 * (n - 1))


def _observed(day):
    if day.weekday() == _SAT:
        return day - _dt.timedelta(days=1)
    if day.weekday() == _SUN:
        return day + _dt.timedelta(days=1)
    return day


def holidays(year):
    """{observed date: name} for the federal holidays of `year`."""
    fixed = {
        "New Year's Day": _dt.date(year, 1, 1),
        "Independence Day": _dt.date(year, 7, 4),
        "Veterans Day": _dt.date(year, 11, 11),
        "Christmas Day": _dt.date(year, 12, 25),
    }
    floating = {name: _nth_weekday(year, month, weekday, n) for name, month, weekday, n in _FLOATING}
    if year >= 2021:
        fixed["Juneteenth National Independence Day"] = _dt.date(year, 6, 19)
    result = {_observed(day): name for name, day in fixed.items()}
    result.update({day: name for name, day in floating.items()})
    return result


def is_holiday(day):
    # Next year's New Year's Day can be observed on December 31.
    return day in holidays(day.year) or day in holidays(day.year + 1)


def is_weekend(day):
    return day.weekday() >= _SAT


def is_business_day(day):
    return not is_weekend(day) and not is_holiday(day)


def next_business_day(day):
    day += _dt.timedelta(days=1)
    while not is_business_day(day):
        day += _dt.timedelta(days=1)
    return day


def previous_business_day(day):
    day -= _dt.timedelta(days=1)
    while not is_business_day(day):
        day -= _dt.timedelta(days=1)
    return day
