"""The claims system of record: permissions, state transitions, amounts, letters, payments, audit."""

from __future__ import annotations

import sqlite3

import pytest

from claims_agent.backend import AccessDenied, BackendError, ClaimsBackend, init_db


def backend(settings, principal="adjuster-07"):
    return ClaimsBackend(settings.db_path, settings.letters_dir, principal, settings.today)


def rows(settings, sql):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_reads(settings):
    b = backend(settings)
    claim = b.get_claim("CLM-5003")
    assert (
        claim["status"] == "open"
        and claim["documents"] == 2
        and claim["allowed_next_status"] == ["under_review", "withdrawn"]
    )
    assert claim["policy_status"] == "active" and claim["holder"] == "Shan"
    assert [d["id"] for d in b.list_documents("CLM-5003")["documents"]] == ["DOC-77", "DOC-78"]
    with pytest.raises(BackendError, match="CLM-9999 not found"):
        b.get_claim("CLM-9999")
    with pytest.raises(BackendError, match="not found"):
        b.list_documents("CLM-9999")


def test_unknown_principal_and_missing_permissions(settings):
    with pytest.raises(AccessDenied, match="unknown principal"):
        backend(settings, "mallory")
    intake = backend(settings, "intake-bot")
    assert intake.get_claim("CLM-5003")["status"] == "open"
    for call in (
        lambda: intake.update_claim("CLM-5003", "under_review"),
        lambda: intake.add_note("CLM-5003", "x"),
        lambda: intake.write_letter("CLM-5003", "acknowledgement", "x"),
        lambda: backend(settings).make_payment("CLM-5001", 10),
    ):
        with pytest.raises(AccessDenied, match="ACCESS_DENIED"):
            call()


def test_transitions_and_approval_rules(settings):
    b = backend(settings)
    with pytest.raises(BackendError, match="cannot move to approved"):
        b.update_claim("CLM-5003", "approved", 100)
    assert b.update_claim("CLM-5003", "under_review", reason="docs in")["status"] == "under_review"
    with pytest.raises(BackendError, match="between 0 and 6200"):
        b.update_claim("CLM-5003", "approved", 7000)
    with pytest.raises(BackendError, match="between 0 and"):
        b.update_claim("CLM-5003", "approved")
    with pytest.raises(BackendError, match="only allowed when approving"):
        b.update_claim("CLM-5003", "denied", 100)
    assert b.update_claim("CLM-5003", "approved", 5000.0)["approved_amount"] == 5000.0
    assert rows(settings, "SELECT text FROM claim_notes") == [("under_review: docs in",)]
    b.update_claim("CLM-5006", "under_review")
    with pytest.raises(BackendError, match="POL-3001 is lapsed"):
        b.update_claim("CLM-5006", "approved", 100)


def test_notes_letters_and_payments(settings):
    b, sup = backend(settings), backend(settings, "supervisor-02")
    assert b.add_note("CLM-5003", " checked ")["note_id"] == 1
    with pytest.raises(BackendError, match="note text is required"):
        b.add_note("CLM-5003", " ")
    letter = b.write_letter("CLM-5003", "acknowledgement", "We got DOC-77.")
    text = (settings.letters_dir / letter["file"]).read_text(encoding="utf-8")
    assert "Claim CLM-5003 (Shan)" in text and "We got DOC-77." in text
    with pytest.raises(BackendError, match="kind must be one of"):
        b.write_letter("CLM-5003", "poem", "x")
    with pytest.raises(BackendError, match="body is required"):
        b.write_letter("CLM-5003", "approval", "")
    with pytest.raises(BackendError, match="only approved claims"):
        sup.make_payment("CLM-5003", 10)
    with pytest.raises(BackendError, match="approved 2400"):
        sup.make_payment("CLM-5001", 9999)
    assert sup.make_payment("CLM-5001", 2400.0) == {
        "payment_id": "PAY-CLM-5001",
        "claim_id": "CLM-5001",
        "amount": 2400.0,
        "status": "paid",
    }
    assert rows(settings, "SELECT status FROM claims WHERE id='CLM-5001'") == [("paid",)]


def test_audit_and_reset(settings):
    b = backend(settings)
    b.audit("get_claim", "ok")
    assert rows(settings, "SELECT principal, operation, outcome FROM audit_log") == [("adjuster-07", "get_claim", "ok")]
    b.write_letter("CLM-5003", "acknowledgement", "x")
    init_db(settings.db_path, settings.letters_dir, settings.today)
    assert rows(settings, "SELECT COUNT(*) FROM audit_log") == [(0,)] and not list(settings.letters_dir.glob("*.txt"))
