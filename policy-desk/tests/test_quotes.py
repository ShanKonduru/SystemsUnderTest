"""Saving quotes and working with them on the Quotes tab: grid, filter, bind, decline, edit."""

import pytest

from screens import PolicyDesk, QuoteScreen


def save_quote(desk: PolicyDesk, quote: QuoteScreen, **fields: object) -> None:
    quote.fill(**fields)
    assert quote.check_underwriting() == "Underwriting: Approved"
    quote.save()


@pytest.mark.catches("grid_not_refreshed")
def test_a_saved_quote_is_in_the_grid_and_the_record(desk: PolicyDesk, quote: QuoteScreen) -> None:
    save_quote(desk, quote, policyholder="Avery Stone", email="avery@example.com")
    assert desk.wait_status("Quote 1006 saved (Pending)")
    row = desk.quotes.row(1006)
    assert row is not None, "quote 1006 is not in the grid"
    assert (row["Policyholder"], row["Premium"], row["Status"]) == ("Avery Stone", "600.00", "Pending")
    assert "6 quotes" in desk.status_texts()
    assert desk.record.quote(1006)["status"] == "Pending"


@pytest.mark.catches("multi_policy_ignored")
def test_the_saved_premium_is_the_premium_on_screen(desk: PolicyDesk, quote: QuoteScreen) -> None:
    quote.fill(policyholder="Bundle Buyer", email="b@example.com", multi_policy=True)
    assert quote.calculate() == "Premium: 540.00 a year"
    save_quote(desk, quote)
    desk.wait_status("Quote 1006 saved (Pending)")
    saved = desk.record.quote(1006)
    assert (saved["multi_policy"], saved["premium"]) == (True, 540.00)


def test_the_grid_filters_by_name_and_status(desk: PolicyDesk, quote: QuoteScreen) -> None:
    quotes = desk.quotes
    assert [r["Quote"] for r in quotes.rows()] == ["1001", "1002", "1003", "1004", "1005"]
    assert [r["Quote"] for r in quotes.find_quotes(status="Pending")] == ["1002", "1003"]
    assert [r["Quote"] for r in quotes.find_quotes(text="ra")] == ["1002", "1003"]   # case-insensitive
    assert desk.status() == "2 quote(s) match"
    assert quotes.find_quotes(text="zzz") == []


@pytest.mark.catches("phantom_bind")
def test_binding_a_pending_quote(desk: PolicyDesk, quote: QuoteScreen) -> None:
    details = desk.quotes.open_quote(1002)
    assert details.field("txtDetailStatus") == "Pending"
    details.bind()
    assert desk.wait_status("Quote 1002 bound as policy HMP-1002")
    assert desk.quotes.row(1002)["Status"] == "Bound"
    saved = desk.record.quote(1002)
    assert (saved["status"], saved["policy_number"]) == ("Bound", "HMP-1002")
    assert "quote_bound" in desk.record.activity_kinds()


def test_declining_a_quote_keeps_the_reason_and_note(desk: PolicyDesk, quote: QuoteScreen) -> None:
    details = desk.quotes.open_quote(1004)
    assert not details.enabled("btnBind")                       # Referred quotes cannot be bound
    details.decline("Underwriting decision", "second review failed")
    assert desk.wait_status("Quote 1004 declined")
    assert desk.record.quote(1004)["decline_reason"] == "Underwriting decision: second review failed"


def test_editing_a_referred_quote_updates_it(desk: PolicyDesk, quote: QuoteScreen) -> None:
    desk.quotes.edit_quote(1004)
    assert desk.wait_status("Editing quote 1004")
    assert quote.text("btnSave") == "Update quote"
    quote.fill(product="Auto")
    assert quote.check_underwriting() == "Underwriting: Approved"
    quote.save()
    assert desk.wait_status("Quote 1004 updated (Pending)")
    saved = desk.record.quote(1004)
    assert (saved["product"], saved["status"], saved["premium"]) == ("Auto", "Pending", 600.00)
    assert len(desk.record.quotes()) == 5
    assert desk.quotes.row(1004)["Product"] == "Auto"


def test_a_bound_quote_cannot_be_edited(desk: PolicyDesk, quote: QuoteScreen) -> None:
    desk.quotes.edit_quote(1001)
    box = desk.message_box()
    assert box.text == "Only Pending or Referred quotes can be edited."
    box.press("OK")
