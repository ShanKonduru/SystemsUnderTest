"""The New quote tab: pricing, validation and what UI Automation sees of the form."""

import pytest

from screens import PolicyDesk, QuoteScreen
from screens.quote import LABELS

TODAY = "2026-11-03"  # the app's fixed "today"


def test_default_quote_is_priced(quote: QuoteScreen) -> None:
    assert quote.premium == "Premium: 600.00 a year"           # Auto, Standard, deductible 500
    assert quote.installment == "Paid annually"


@pytest.mark.catches("monthly_truncated")
def test_monthly_installment_is_rounded(quote: QuoteScreen) -> None:
    quote.fill(product="Renters", coverage="Basic", deductible=250, monthly=True)
    assert quote.calculate() == "Premium: 198.00 a year"
    assert quote.installment == "Monthly: 12 x 17.00 (204.00 a year)"   # 198 x 1.03 / 12 = 16.995


def test_every_factor_is_applied_and_rounded_per_step(quote: QuoteScreen) -> None:
    quote.fill(customer_type="Business", coverage="Premium", deductible=250, roadside=True, multi_policy=True)
    assert quote.calculate() == "Premium: 919.37 a year"


def test_roadside_is_only_for_auto(quote: QuoteScreen) -> None:
    assert quote.enabled("chkRoadside")
    quote.fill(roadside=True)
    quote.fill(product="Home")
    assert not quote.enabled("chkRoadside")
    assert not quote.find("chkRoadside").get_toggle_state()
    assert quote.calculate() == "Premium: 1,150.00 a year"     # no roadside fee on Home


def test_a_change_clears_the_premium(quote: QuoteScreen) -> None:
    quote.fill(coverage="Premium")
    assert quote.premium == "Premium: not calculated"
    assert quote.calculate() == "Premium: 768.00 a year"


def test_bad_email_and_short_phone_are_reported(quote: QuoteScreen) -> None:
    quote.fill(policyholder="Test Person", email="not-an-email", phone="617555")
    assert quote.check_expecting_errors() == ["Enter a valid email address.", "Enter a 10-digit phone number."]
    assert quote.underwriting == "Underwriting: not checked"


def test_missing_policyholder_from_the_toolbar_save(desk: PolicyDesk, quote: QuoteScreen) -> None:
    quote.save_from_toolbar()
    box = desk.message_box()
    assert box.text == "Policyholder is required."
    box.press("OK")
    assert "Policyholder is required." in quote.errors


@pytest.mark.catches("start_today_rejected")
def test_a_start_date_of_today_is_accepted(quote: QuoteScreen) -> None:
    quote.fill(policyholder="Test Person", email="t@example.com", start=TODAY)
    assert quote.check_underwriting() == "Underwriting: Approved"
    assert quote.errors == []


def test_a_start_date_in_the_past_is_refused(quote: QuoteScreen) -> None:
    quote.fill(policyholder="Test Person", email="t@example.com", start="2026-11-02")
    assert quote.check_expecting_errors() == ["Start date cannot be in the past."]


def test_a_start_date_more_than_60_days_out_is_refused(quote: QuoteScreen) -> None:
    quote.fill(policyholder="Test Person", email="t@example.com", start="2027-01-03")
    assert quote.check_expecting_errors() == ["Start date must be within 60 days."]


@pytest.mark.catches("email_unlabeled")
def test_every_field_is_named_by_its_label(quote: QuoteScreen) -> None:
    names = {auto_id: quote.name(auto_id) for auto_id in LABELS}
    assert names == LABELS
