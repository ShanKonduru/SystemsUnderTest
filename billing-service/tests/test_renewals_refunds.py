from datetime import date
from decimal import Decimal

import pytest

from billing.errors import BillingError
from billing.refunds import RefundQuote, premium_paid, refund_quote
from billing.renewals import Standing, notice_date, renewal_date, standing, term_days

TODAY = date(2026, 11, 3)


def test_renewal_and_term() -> None:
    assert renewal_date(date(2026, 6, 15)) == date(2027, 6, 15)
    assert renewal_date(date(2028, 2, 29)) == date(2029, 2, 28)
    assert term_days(date(2026, 6, 15)) == 365
    assert term_days(date(2027, 6, 1)) == 366  # includes 2028-02-29


def test_standing() -> None:
    assert standing(True, [(3, date(2026, 10, 20))], TODAY, 10) == Standing("cancelled")
    assert standing(False, [], TODAY, 10) == Standing("active")
    # due today is not overdue yet
    assert standing(False, [(4, TODAY)], TODAY, 10) == Standing("active")
    assert standing(False, [(2, date(2026, 11, 1))], TODAY, 10) == Standing(
        "past_due", 2, date(2026, 11, 1), date(2026, 11, 11))
    # the earliest overdue installment decides
    assert standing(False, [(4, date(2026, 11, 2)), (3, date(2026, 10, 20))], TODAY, 10) == Standing(
        "lapsed", 3, date(2026, 10, 20), date(2026, 10, 30), date(2026, 10, 31))
    # the last day of grace is still past due
    assert standing(False, [(3, date(2026, 10, 24))], TODAY, 10).status == "past_due"


@pytest.mark.parametrize(
    ("renewal", "expected"),
    [
        (date(2027, 1, 31), date(2026, 12, 17)),  # a Thursday
        (date(2027, 1, 9), date(2026, 11, 25)),  # a Wednesday
        (date(2027, 1, 10), date(2026, 11, 25)),  # Thanksgiving, Nov 26, moves back a day
        (date(2027, 1, 4), date(2026, 11, 20)),  # a Friday
        (date(2026, 9, 1), date(2026, 7, 17)),  # Sat Jul 18 -> Fri Jul 17
        (date(2026, 8, 17), date(2026, 7, 2)),  # Fri Jul 3 (Independence Day observed) -> Thu Jul 2
    ],
)
def test_notice_date_falls_on_a_business_day(renewal: date, expected: date) -> None:
    assert notice_date(renewal, 45) == expected


def quote(cancel: date, paid: str = "2251.21", effective: date = date(2026, 6, 15)) -> RefundQuote:
    return refund_quote(annual_premium=Decimal("2251.21"), effective=effective, cancel_date=cancel,
                        paid=Decimal(paid), free_look_days=10, cancellation_fee=Decimal("25.00"))


def test_free_look_refunds_everything() -> None:
    q = quote(date(2026, 6, 24))  # day 10
    assert (q.kind, q.refund, q.fee, q.earned, q.days_in_force) == (
        "free_look", Decimal("2251.21"), Decimal("0.00"), Decimal("0.00"), 10)


def test_pro_rata_after_free_look() -> None:
    q = quote(date(2026, 6, 25))  # day 11
    assert q.kind == "pro_rata"
    assert q.days_in_force == 11
    assert q.earned == Decimal("67.84")  # 2251.21 x 11 / 365 = 67.845...
    assert q.refund == Decimal("2251.21") - Decimal("67.84") - Decimal("25.00")

    q = quote(date(2026, 11, 3))
    assert (q.days_in_force, q.term_days, q.earned, q.refund) == (
        142, 365, Decimal("875.81"), Decimal("1350.40"))


def test_refund_is_never_negative() -> None:
    q = quote(date(2026, 11, 3), paid="500.00")
    assert q.refund == Decimal("0.00")


def test_leap_year_term_uses_366_days() -> None:
    q = quote(date(2027, 12, 1), effective=date(2027, 6, 1))
    assert q.term_days == 366
    assert q.days_in_force == 184
    assert q.earned == Decimal("1131.76")  # 2251.21 x 184 / 366 = 1131.756...


def test_cancel_date_must_be_in_the_term() -> None:
    with pytest.raises(BillingError, match="within the term"):
        quote(date(2026, 6, 14))
    with pytest.raises(BillingError, match="within the term"):
        quote(date(2027, 6, 15))


def test_premium_paid_counts_paid_installments_only() -> None:
    rows = [
        {"number": 1, "amount": "960.04", "paid_on": "2026-05-01", "paid_amount": "960.04"},
        {"number": 2, "amount": "960.04", "paid_on": None, "paid_amount": None},
    ]
    assert premium_paid(rows) == Decimal("960.04")
    assert premium_paid([]) == Decimal("0.00")
