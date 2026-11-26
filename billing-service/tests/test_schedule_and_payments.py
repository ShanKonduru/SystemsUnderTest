from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from billing.errors import BillingError, Conflict
from billing.installments import Installment, build_schedule
from billing.payments import assess_payment, received_date

FEES = {"annual": Decimal("0.00"), "semiannual": Decimal("0.00"), "quarterly": Decimal("2.00"),
        "monthly": Decimal("3.00")}


def test_monthly_schedule_from_a_month_end() -> None:
    schedule = build_schedule(Decimal("1411.60"), "monthly", date(2026, 1, 31), FEES)
    assert [i.due_date.isoformat() for i in schedule] == [
        "2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30",
        "2026-07-31", "2026-08-31", "2026-09-30", "2026-10-31", "2026-11-30", "2026-12-31",
    ]
    # 141160 cents / 12 = 11763 remainder 4: the leftover goes on the first installment
    assert schedule[0].amount == Decimal("117.67")
    assert {i.amount for i in schedule[1:]} == {Decimal("117.63")}
    assert sum(i.amount for i in schedule) == Decimal("1411.60")
    assert schedule[0].fee == Decimal("3.00")
    assert schedule[0].total == Decimal("120.67")


@pytest.mark.parametrize(
    ("plan", "due_dates"),
    [
        ("annual", ["2026-04-01"]),
        ("semiannual", ["2026-04-01", "2026-10-01"]),
        ("quarterly", ["2026-04-01", "2026-07-01", "2026-10-01", "2027-01-01"]),
    ],
)
def test_other_plans(plan: str, due_dates: list[str]) -> None:
    schedule = build_schedule(Decimal("1000.01"), plan, date(2026, 4, 1), FEES)
    assert [i.due_date.isoformat() for i in schedule] == due_dates
    assert sum(i.amount for i in schedule) == Decimal("1000.01")
    assert {i.fee for i in schedule} == {FEES[plan]}


def test_schedule_rejects_bad_input() -> None:
    with pytest.raises(BillingError, match="unknown plan"):
        build_schedule(Decimal("10"), "weekly", date(2026, 1, 1), FEES)
    with pytest.raises(BillingError, match="positive"):
        build_schedule(Decimal("0"), "annual", date(2026, 1, 1), FEES)


def test_received_date_uses_the_billing_timezone() -> None:
    # 10 pm Eastern on Nov 1 is already Nov 2 in UTC
    late_evening = datetime(2026, 11, 1, 22, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert received_date(late_evening, "America/New_York") == date(2026, 11, 1)
    assert received_date(late_evening, "UTC") == date(2026, 11, 2)
    assert received_date(datetime(2026, 7, 1, 3, 30, tzinfo=UTC), "America/New_York") == date(2026, 6, 30)
    with pytest.raises(BillingError, match="UTC offset"):
        received_date(datetime(2026, 11, 1, 22, 0), "America/New_York")


INST = Installment(2, date(2026, 11, 1), Decimal("960.04"), Decimal("0.00"))


def test_on_time_payment() -> None:
    outcome = assess_payment(INST, Decimal("960.04"), date(2026, 11, 1), 10, Decimal("10.00"))
    assert (outcome.installment, outcome.late, outcome.late_fee, outcome.amount_due) == (
        2, False, Decimal("0.00"), Decimal("960.04"))


def test_late_payment_in_grace_adds_the_late_fee() -> None:
    outcome = assess_payment(INST, Decimal("970.04"), date(2026, 11, 11), 10, Decimal("10.00"))
    assert outcome.late and outcome.late_fee == Decimal("10.00")
    with pytest.raises(BillingError, match=r"needs exactly 970.04 including the 10.00 late fee"):
        assess_payment(INST, Decimal("960.04"), date(2026, 11, 2), 10, Decimal("10.00"))


def test_wrong_amount_and_lapsed() -> None:
    with pytest.raises(BillingError, match=r"needs exactly 960.04; received 900"):
        assess_payment(INST, Decimal("900"), date(2026, 10, 30), 10, Decimal("10.00"))
    with pytest.raises(Conflict, match="lapsed after 2026-11-11"):
        assess_payment(INST, Decimal("970.04"), date(2026, 11, 12), 10, Decimal("10.00"))
