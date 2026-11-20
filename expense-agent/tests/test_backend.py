"""The expenses system of record: seed, permissions, transitions, amounts, notices, reimbursements, audit."""

from __future__ import annotations

import sqlite3

import pytest

from expense_agent.backend import AccessDenied, BackendError, ExpenseBackend, init_db


def backend(settings, principal="expense-bot"):
    return ExpenseBackend(settings.db_path, settings.out_dir, principal, settings.today)


def rows(settings, sql):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_seed_totals_and_statuses(settings):
    assert rows(settings, "SELECT id, status, total, approved_amount FROM reports ORDER BY id") == [
        ("ER-3001", "submitted", 342.5, None),
        ("ER-3002", "submitted", 142.0, None),
        ("ER-3003", "submitted", 1560.0, None),
        ("ER-3004", "submitted", 66.5, None),
        ("ER-3005", "submitted", 85.0, None),
        ("ER-3006", "submitted", 73.4, None),
        ("ER-3007", "approved", 310.0, 310.0),
        ("ER-3008", "submitted", 1180.0, None),
        ("ER-3009", "submitted", 60.0, None),
        ("ER-3010", "paid", 120.0, 120.0),
    ]


def test_reads(settings):
    b = backend(settings)
    report = b.get_report("ER-3008")
    assert report["employee"] == {
        "id": "EMP-101",
        "name": "Asha Rao",
        "department": "Sales",
        "status": "active",
        "manager_id": "EMP-200",
    }
    assert report["lines"] == [
        {
            "line_id": "L1",
            "date": "2026-10-30",
            "category": "airfare",
            "merchant": "Blue Air",
            "amount": 1180.0,
            "cabin": "business",
            "receipt": True,
        }
    ]
    assert report["allowed_next_status"] == ["approved", "escalated", "rejected", "returned"]
    assert "cabin" not in b.get_report("ER-3004")["lines"][0] and not b.get_report("ER-3004")["lines"][0]["receipt"]
    assert b.get_report("ER-3007")["allowed_next_status"] == ["paid"]
    assert b.get_report("ER-3005")["employee"]["status"] == "left"
    policy = b.get_expense_policy()
    assert policy["limits"]["meals_per_day"] == 75 and policy["auto_approve_limit"] == 1000
    assert policy["receipt_required_over"] == 25 and policy["submission_window_days"] == 60
    with pytest.raises(BackendError, match="ER-9999 not found"):
        b.get_report("ER-9999")


def test_duplicates_are_found_only_in_earlier_reports(settings):
    b = backend(settings)
    assert b.find_duplicates("ER-3006")["duplicates"] == [
        {
            "line_id": "L1",
            "other_report_id": "ER-3001",
            "other_line_id": "L1",
            "date": "2026-10-27",
            "merchant": "Harbor Bistro",
            "amount": 58.4,
        }
    ]
    for rid in ("ER-3001", "ER-3002", "ER-3003", "ER-3004"):
        assert b.find_duplicates(rid)["duplicates"] == []
    with pytest.raises(BackendError, match="not found"):
        b.find_duplicates("ER-0")


def test_unknown_principal_and_missing_permissions(settings):
    with pytest.raises(AccessDenied, match="unknown principal"):
        backend(settings, "mallory")
    auditor = backend(settings, "auditor-03")
    assert auditor.get_report("ER-3001")["status"] == "submitted"
    for call in (
        lambda: auditor.update_report("ER-3001", "approved", approved_amount=1),
        lambda: auditor.add_note("ER-3001", "x"),
        lambda: auditor.send_notice("ER-3001", "approval", "x"),
        lambda: backend(settings).make_reimbursement("ER-3007", 10),
    ):
        with pytest.raises(AccessDenied, match="ACCESS_DENIED"):
            call()


def test_transitions_and_approval_rules(settings):
    b = backend(settings)
    with pytest.raises(BackendError, match="cannot move to approved"):
        b.update_report("ER-3007", "approved", approved_amount=10)
    with pytest.raises(BackendError, match="status must be one of"):
        b.update_report("ER-3001", "paid")
    with pytest.raises(BackendError, match=r"between 0 and the total 342.5"):
        b.update_report("ER-3001", "approved", approved_amount=400)
    with pytest.raises(BackendError, match="between 0 and"):
        b.update_report("ER-3001", "approved")
    with pytest.raises(BackendError, match="only allowed when approving"):
        b.update_report("ER-3001", "rejected", approved_amount=10)
    assert b.update_report("ER-3001", "approved", "within policy", 342.5) == {
        "report_id": "ER-3001",
        "status": "approved",
        "approved_amount": 342.5,
    }
    assert b.update_report("ER-3002", "rejected")["approved_amount"] is None
    assert rows(settings, "SELECT report_id, text FROM report_notes") == [("ER-3001", "approved: within policy")]
    with pytest.raises(BackendError, match="ER-3002 is rejected"):
        b.update_report("ER-3002", "escalated")


def test_notes_notices_and_reimbursements(settings):
    b, fin = backend(settings), backend(settings, "finance-01")
    assert b.add_note("ER-3001", " checked ")["note_id"] == 1
    with pytest.raises(BackendError, match="note text is required"):
        b.add_note("ER-3001", " ")
    first = b.send_notice("ER-3001", "approval", "Approved for $342.50.")
    second = b.send_notice("ER-3002", "rejection", "Meal over the limit.")
    assert (first["notice_id"], second["notice_id"]) == ("NTC-7001", "NTC-7002")
    text = (settings.out_dir / "notices" / "NTC-7001.txt").read_text(encoding="utf-8")
    assert "Report ER-3001 (Asha Rao)" in text and "Approved for $342.50." in text
    assert rows(settings, "SELECT id, report_id, kind FROM notices ORDER BY id") == [
        ("NTC-7001", "ER-3001", "approval"),
        ("NTC-7002", "ER-3002", "rejection"),
    ]
    with pytest.raises(BackendError, match="kind must be one of"):
        b.send_notice("ER-3001", "poem", "x")
    with pytest.raises(BackendError, match="body is required"):
        b.send_notice("ER-3001", "approval", "")
    with pytest.raises(BackendError, match="only approved reports"):
        fin.make_reimbursement("ER-3010", 120)
    with pytest.raises(BackendError, match="approved 310"):
        fin.make_reimbursement("ER-3007", 9999)
    assert fin.make_reimbursement("ER-3007", 310.0) == {
        "reimbursement_id": "RMB-8001",
        "report_id": "ER-3007",
        "amount": 310.0,
        "status": "paid",
    }
    assert rows(settings, "SELECT status FROM reports WHERE id='ER-3007'") == [("paid",)]


def test_audit_and_reset(settings):
    b = backend(settings)
    b.audit("get_report", "ok")
    assert rows(settings, "SELECT principal, operation, outcome FROM audit_log") == [("expense-bot", "get_report", "ok")]
    b.send_notice("ER-3001", "approval", "x")
    (settings.out_dir / "notices" / "keep").mkdir()
    init_db(settings.db_path, settings.out_dir, settings.today)
    assert rows(settings, "SELECT COUNT(*) FROM audit_log") == [(0,)]
    assert not list((settings.out_dir / "notices").glob("*.txt")) and (settings.out_dir / "notices").is_dir()
