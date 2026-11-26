from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from billing.config import DEFAULT_RATES, load_settings
from billing.dates import add_months
from billing.money import fmt, from_cents, to_cents, to_money
from billing.rates import UnknownTerritory, load_rates


def test_money_rounds_half_up_to_cents() -> None:
    assert to_money(Decimal("10.005")) == Decimal("10.01")
    assert to_money(Decimal("10.015")) == Decimal("10.02")
    assert to_money(Decimal("10.004")) == Decimal("10.00")
    assert to_money("2251.208") == Decimal("2251.21")
    assert to_cents(Decimal("117.67")) == 11767
    assert from_cents(11763) == Decimal("117.63")
    assert fmt(Decimal("3")) == "3.00"


@pytest.mark.parametrize(
    ("anchor", "months", "expected"),
    [
        (date(2026, 1, 31), 1, date(2026, 2, 28)),
        (date(2026, 1, 31), 2, date(2026, 3, 31)),
        (date(2026, 1, 31), 11, date(2026, 12, 31)),
        (date(2026, 1, 31), 12, date(2027, 1, 31)),
        (date(2027, 2, 28), 12, date(2028, 2, 28)),
        (date(2028, 2, 29), 12, date(2029, 2, 28)),
        (date(2026, 8, 20), 4, date(2026, 12, 20)),
        (date(2026, 11, 15), 3, date(2027, 2, 15)),
    ],
)
def test_add_months_keeps_the_day_and_clamps_to_month_end(anchor: date, months: int, expected: date) -> None:
    assert add_months(anchor, months) == expected


def test_settings_from_yaml_and_environment(tmp_path: Path) -> None:
    s = load_settings({})
    assert (s.currency, s.timezone, s.grace_days, s.free_look_days, s.renewal_notice_days, s.reminder_days) == (
        "USD", "America/New_York", 5, 10, 45, 7)
    assert (s.late_fee, s.cancellation_fee) == (Decimal("10.00"), Decimal("25.00"))
    assert s.installment_fees == {"annual": Decimal("0.00"), "semiannual": Decimal("0.00"),
                                  "quarterly": Decimal("2.00"), "monthly": Decimal("3.00")}
    assert s.today == date(2026, 11, 3)
    assert s.sandbox_token == ""

    custom = load_settings({
        "BILLING_TODAY": "2027-01-15", "BILLING_DATA": str(tmp_path), "BILLING_TIMEZONE": "America/Chicago",
        "BILLING_SANDBOX_TOKEN": "t",
    })
    assert custom.today == date(2027, 1, 15)
    assert custom.timezone == "America/Chicago"
    assert custom.db_path == tmp_path / "billing.db"
    assert custom.log_path == tmp_path / "logs" / "billing.log"
    assert custom.sandbox_token == "t"


def test_rate_table_lookup() -> None:
    rates = load_rates(DEFAULT_RATES)
    assert rates.base_rate("nj", "auto") == Decimal("1517.85")
    assert rates.states() == ["CT", "MA", "NJ", "NY", "PA", "VT"]
    with pytest.raises(UnknownTerritory, match="no auto rate for state 'TX'"):
        rates.base_rate("TX", "auto")


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("NJ,boat,100.00", "unknown product 'boat'"),
        ("NJ,auto,abc", "is not a number"),
        ("NJ,auto,0", "must be positive"),
    ],
)
def test_rate_table_rejects_bad_rows(tmp_path: Path, row: str, message: str) -> None:
    path = tmp_path / "rates.csv"
    path.write_text(f"state,product,base_rate\nNY,home,1.00\n{row}\n", encoding="utf-8")
    with pytest.raises(ValueError, match=rf"rates.csv:3: .*{message}"):
        load_rates(path)
