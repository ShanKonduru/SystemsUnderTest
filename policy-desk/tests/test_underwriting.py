"""The asynchronous underwriting check and what it decides."""

import pytest

from screens import PolicyDesk, QuoteScreen


@pytest.mark.catches("slow_underwriting")
def test_save_waits_for_an_underwriting_check(quote: QuoteScreen) -> None:
    quote.fill(policyholder="Test Person", email="t@example.com")
    assert not quote.enabled("btnSave")
    assert quote.check_underwriting() == "Underwriting: Approved"   # within 5 s; the suite launches with 300 ms
    assert quote.enabled("btnSave")
    quote.fill(coverage="Basic")
    assert quote.underwriting == "Underwriting: not checked"
    assert not quote.enabled("btnSave")


@pytest.mark.catches("umbrella_unchecked")
def test_umbrella_without_a_bound_policy_is_referred(quote: QuoteScreen) -> None:
    quote.fill(policyholder="New Customer", email="n@example.com", product="Umbrella")
    assert quote.check_underwriting() == (
        "Underwriting: Referred - Umbrella needs a bound Auto or Home policy for this policyholder.")


def test_umbrella_for_a_customer_with_a_bound_auto_policy_is_approved(quote: QuoteScreen) -> None:
    quote.fill(policyholder="dana whitfield", email="d@example.com", product="Umbrella")
    assert quote.check_underwriting() == "Underwriting: Approved"


def test_a_high_premium_is_referred_and_saved_as_referred(desk: PolicyDesk, quote: QuoteScreen) -> None:
    quote.fill(policyholder="Big Home", email="b@example.com", product="Home", coverage="Premium", deductible=250)
    assert quote.check_underwriting() == "Underwriting: Referred - Premium over 1,500.00 needs an underwriter."
    quote.save()
    assert desk.wait_status("Quote 1006 saved (Referred)")
    saved = desk.record.quote(1006)
    assert (saved["status"], saved["premium"]) == ("Referred", 1619.20)
