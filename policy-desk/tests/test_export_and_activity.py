"""CSV export through the Windows Save dialog, and the activity log of a session."""

import csv

import pytest

from screens import PolicyDesk, QuoteScreen, until


@pytest.mark.catches("export_drops_last")
def test_export_writes_every_visible_quote(desk: PolicyDesk, quote: QuoteScreen) -> None:
    quotes = desk.quotes
    visible = [r["Quote"] for r in quotes.find_quotes(text="ra")]
    target = desk.export_dir / "ra.csv"
    quotes.export(target)
    assert desk.wait_status("Exported 2 quote(s) to ra.csv")
    with target.open(encoding="utf-8", newline="") as f:
        written = [row["Quote"] for row in csv.DictReader(f)]
    assert written == visible
    assert "quotes_exported" in desk.record.activity_kinds()


def test_a_session_is_recorded_in_the_activity_log(desk: PolicyDesk) -> None:
    desk.login.sign_in("tester", "wrong")
    desk.sign_in()
    desk.close()
    until(lambda: "sign_out" in desk.record.activity_kinds(), "sign_out in the activity log")
    assert desk.record.activity_kinds() == ["reset", "launch", "sign_in_failed", "sign_in", "sign_out"]
