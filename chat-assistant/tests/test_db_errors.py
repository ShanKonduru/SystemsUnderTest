"""Backend validation and error paths: every rule the Harbor Mutual system of record enforces, plus its small CLIs."""

import json
import runpy
import sqlite3
import sys
import warnings
from datetime import timedelta

import pytest

from insurance_copilot.backend import db as db_mod
from insurance_copilot.backend.db import (
    AccessDenied,
    CustomerNotVerified,
    InsuranceBackend,
    InvalidRequest,
    NotFound,
    NotPermitted,
    _item_factor,
    _reference,
    _strip_new_ids,
    driver_surcharge,
    init_db,
)
from insurance_copilot.backend.policy import TransactionPolicy
from insurance_copilot.backend.review import main as review_main
from insurance_copilot.core.attachments import compose_user_message
from tests.conftest import TODAY

GUEST = "GUEST-0B2C3D4E"
APPLICANT = {
    "full_name": "Lena Park",
    "email": "lena.park@example.com",
    "date_of_birth": "1991-03-08",
    "address": "12 Elm St, Springfield, IL 62704",
}
PDF = b"%PDF-1.4\n% proof\n%%EOF\n"
SHAN = ("CUST-001", "1985-04-12", "62701")


def _q(db, sql, *params):
    return sqlite3.connect(db).execute(sql, params).fetchall()


@pytest.fixture
def queued(tmp_path):
    """A database with the representative backlog (APP-9500 new applicant, APP-9499 possible existing customer)."""
    path = tmp_path / "queued.db"
    init_db(path, TODAY, queue=True)
    return path


def _renters_quote(be):
    return be.create_quote(
        "RENTERS",
        "standard",
        500,
        {"property_address": "12 Elm St, Springfield, IL 62704", "personal_property_value": "15000"},
    )["quote_id"]


def _rep(db, rep_id, customer=None):
    be = InsuranceBackend(db, rep_id, TODAY)
    if customer:
        assert be.verify_customer(*customer)["verified"]
    return be


# ---------------------------------------------------------------- pure helpers
def test_pricing_and_result_helpers():
    assert driver_surcharge(75) == 0.20 and driver_surcharge(40) == 0.10 and driver_surcharge(20) == 0.35
    with pytest.raises(InvalidRequest, match="Invalid insured item details"):
        _item_factor("HOME", {"square_feet": "large"}, TODAY)
    with pytest.raises(InvalidRequest):
        _item_factor("AUTO", {}, TODAY)
    assert _item_factor("TRAVEL", {}, TODAY) == 1.0
    assert _reference("not a dict") is None and _reference({"other": 1}) is None
    assert _reference({"claim_id": "CLM-1"}) == "CLM-1"
    assert _strip_new_ids(["x"], {}) == ["x"]
    assert _strip_new_ids(
        {"payment_id": "PAY-1", "policy_id": "POL-1", "id": "x", "amount": 5}, {"policy_id": "POL-1"}
    ) == {"policy_id": "POL-1", "amount": 5}


def test_attachment_message_for_several_documents():
    docs = [
        {"document_id": f"DOC-600{i}", "filename": f"f{i}.pdf", "content_type": "application/pdf", "size_bytes": 10}
        for i in (1, 2)
    ]
    out = compose_user_message("  ", docs)
    assert out.startswith("Here are the documents.\n[Attached document DOC-6001")
    assert compose_user_message("", docs[:1]).startswith("Here is the document.")
    assert compose_user_message("hi", []) == "hi"


def test_unknown_representative_role_is_rejected():
    with pytest.raises(ValueError, match="Unknown representative role"):
        TransactionPolicy(role="intern")


# ---------------------------------------------------------------- schema and principals
def test_reinit_resets_data_in_place(db):
    InsuranceBackend(db, "CUST-001", TODAY).make_payment("POL-1002", 10, "card_on_file")
    init_db(db, TODAY)
    assert _q(db, "SELECT COUNT(*) FROM payments") == [(0,)]


def test_older_database_gets_missing_columns(db):
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE handoffs DROP COLUMN resolution")
    con.execute("ALTER TABLE transactions DROP COLUMN consent")
    con.commit()
    con.close()
    InsuranceBackend(db, "CUST-001", TODAY)
    cols = {r[1] for r in _q(db, "PRAGMA table_info(handoffs)")} | {
        r[1] for r in _q(db, "PRAGMA table_info(transactions)")
    }
    assert {"resolution", "consent"} <= cols


def test_unknown_principals_are_rejected(db):
    with pytest.raises(NotFound, match="Customer CUST-999"):
        InsuranceBackend(db, "CUST-999", TODAY)
    with pytest.raises(NotFound, match="Representative REP-999"):
        InsuranceBackend(db, "REP-999", TODAY)


def test_representative_only_operations_refuse_customers(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(NotPermitted, match="Only Harbor Mutual representatives"):
        be.my_role()
    service = InsuranceBackend(db, "REP-201", TODAY)
    with pytest.raises(NotPermitted, match="licensed agent"):
        service._require_rep(licensed=True)
    service._require_rep()


# ---------------------------------------------------------------- transactions and review
def test_transaction_lookup_and_review_errors(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(NotFound):
        be.get_transaction("TXN-404")
    with pytest.raises(InvalidRequest, match="reviewer is required"):
        be.review_transaction("TXN-404", "approve", "  ")
    with pytest.raises(InvalidRequest, match="decision must be"):
        be.review_transaction("TXN-404", "maybe", "J. Rivera")
    with pytest.raises(NotFound):
        be.review_transaction("TXN-404", "approve", "J. Rivera")
    with pytest.raises(NotFound):
        InsuranceBackend.for_transaction(db, "TXN-404", TODAY)
    be.execute("make_payment", {"policy_id": "POL-1002", "amount": 5, "payment_method": "card_on_file"})
    assert [t["status"] for t in be.list_transactions(status="committed")] == ["committed"]
    assert be.list_transactions(status="rejected") == []


def test_review_cli_list_show_and_errors(db, capsys):
    assert review_main(["--db", str(db), "list"]) == 0
    assert "No transactions." in capsys.readouterr().out
    InsuranceBackend(db, "CUST-001", TODAY).execute(
        "make_payment", {"policy_id": "POL-1002", "amount": 5, "payment_method": "card_on_file"}
    )
    assert review_main(["--db", str(db), "list", "--status", "all", "--customer", "CUST-001"]) == 0
    assert "TXN-10001" in capsys.readouterr().out
    assert review_main(["--db", str(db), "list", "--status", "all", "--customer", "CUST-002"]) == 0
    assert "No transactions." in capsys.readouterr().out
    assert review_main(["--db", str(db), "show", "TXN-10001"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["status"] == "committed" and shown["result"]["payment_id"] == "PAY-9001"
    assert review_main(["--db", str(db), "show", "TXN-404"]) == 1
    assert "NOT_FOUND" in capsys.readouterr().err


def test_review_cli_runs_as_a_module(db, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["review", "--db", str(db), "list"])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("insurance_copilot.backend.review", run_name="__main__")
    assert exc.value.code == 0 and "No transactions." in capsys.readouterr().out


def test_seed_cli_creates_the_database_with_a_backlog(tmp_path, monkeypatch, capsys):
    path = tmp_path / "seeded.db"
    monkeypatch.setenv("COPILOT_TODAY", TODAY.isoformat())
    monkeypatch.setattr(sys, "argv", ["db", "--db", str(path)])
    db_mod.main()
    assert f"Seeded demo database at {path}" in capsys.readouterr().out
    assert _q(path, "SELECT id FROM transactions") == [("TXN-10000",)]
    path2 = tmp_path / "seeded2.db"
    monkeypatch.setattr(sys, "argv", ["db", "--db", str(path2)])
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        runpy.run_module("insurance_copilot.backend.db", run_name="__main__")
    assert _q(path2, "SELECT COUNT(*) FROM applications") == [(2,)]


# ---------------------------------------------------------------- prospects and applications
def test_start_application_validation(db):
    with pytest.raises(NotPermitted, match="purchase_policy"):
        InsuranceBackend(db, "CUST-001", TODAY).start_application("QTE-7001", **APPLICANT)
    guest = InsuranceBackend(db, GUEST, TODAY)
    qid = _renters_quote(guest)
    with pytest.raises(InvalidRequest, match="full_name"):
        guest.start_application(qid, **{**APPLICANT, "full_name": "L"})
    with pytest.raises(InvalidRequest, match="Phone"):
        guest.start_application(qid, **APPLICANT, phone="555-12")
    with pytest.raises(NotFound):
        guest.start_application("QTE-9999", **APPLICANT)
    later = InsuranceBackend(db, GUEST, TODAY + timedelta(days=45))
    with pytest.raises(InvalidRequest, match="expired"):
        later.start_application(qid, **APPLICANT)
    with pytest.raises(NotFound):
        guest.get_application("APP-0001")


def test_returning_applicant_matching_a_customer_is_flagged(db):
    guest = InsuranceBackend(db, GUEST, TODAY)
    applicant = {**APPLICANT, "email": "sri@example.com"}  # CUST-003's email
    first = guest.start_application(_renters_quote(guest), **applicant)
    con = sqlite3.connect(db)
    con.execute("UPDATE prospects SET match_status='new'")
    con.commit()
    con.close()
    second = guest.start_application(_renters_quote(guest), **applicant)
    assert first["application_id"] != second["application_id"]
    assert _q(db, "SELECT match_status FROM prospects") == [("possible_existing_customer",)]


def test_issue_and_decline_application_errors(queued):
    sup = _rep(queued, "REP-203")
    with pytest.raises(InvalidRequest, match="past"):
        sup.issue_application("APP-9500", start_date=(TODAY - timedelta(days=1)).isoformat())
    with pytest.raises(NotFound):
        sup.issue_application("APP-0001")
    with pytest.raises(NotFound):
        sup.issue_application("APP-9499", existing_customer_id="CUST-999")
    with pytest.raises(CustomerNotVerified):
        sup.issue_application("APP-9499", existing_customer_id="CUST-003")
    service = _rep(queued, "REP-201")
    with pytest.raises(AccessDenied, match="not in your assigned book"):
        service.issue_application("APP-9499", existing_customer_id="CUST-002")
    later = InsuranceBackend(queued, "REP-203", TODAY + timedelta(days=40))
    with pytest.raises(InvalidRequest, match="expired"):
        later.issue_application("APP-9500")
    with pytest.raises(InvalidRequest, match="reason"):
        sup.decline_application("APP-9500", "  ")
    with pytest.raises(NotFound):
        sup.decline_application("APP-0001", "no")
    assert sup.decline_application("APP-9500", "Outside appetite")["status"] == "declined"
    with pytest.raises(InvalidRequest, match="not waiting"):
        sup.decline_application("APP-9500", "again")
    with pytest.raises(InvalidRequest, match="not waiting"):
        sup.issue_application("APP-9500")


# ---------------------------------------------------------------- handoffs
def test_request_representative_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="preferred_channel"):
        be.request_representative("billing", "question", "fax")
    with pytest.raises(InvalidRequest, match="topic and summary"):
        be.request_representative(" ", "question", "chat")
    with pytest.raises(InvalidRequest, match="email address"):
        be.request_representative("billing", "question", "email", contact="not-an-email")


# ---------------------------------------------------------------- representatives
def test_representative_lookup_and_session_errors(db):
    sup = _rep(db, "REP-203")
    with pytest.raises(NotFound):
        sup.verify_customer("CUST-999", "1985-04-12", "62701")
    found = sup.search_customers("cust-001")
    assert [c["customer_id"] for c in found["customers"]] == ["CUST-001"] and found["prospects"] == []
    by_name = sup.search_customers("Indu")
    assert [c["customer_id"] for c in by_name["customers"]] == ["CUST-002"]
    assert by_name["customers"][0]["city"] == "Shelbyville, IL"
    assert InsuranceBackend._city("Springfield") == ""
    assert InsuranceBackend._mask_email("no-at-sign") == "***"
    assert sup.verify_customer(*SHAN)["verified"]
    role = sup.my_role()
    assert role["active_customer"] == {"customer_id": "CUST-001", "name": "Shan"}


def test_notes_tasks_queue_and_cases_errors(queued):
    service = _rep(queued, "REP-201", SHAN)
    with pytest.raises(InvalidRequest, match="past"):
        service.add_note("Call back about billing", follow_up_on=(TODAY - timedelta(days=1)).isoformat())
    with pytest.raises(NotFound):
        service.complete_task("NTE-0001")
    task = service.add_note("Call back about billing", follow_up_on=TODAY.isoformat())
    assert service.complete_task(task["note_id"])["status"] == "done"
    with pytest.raises(InvalidRequest, match="already done"):
        service.complete_task(task["note_id"])
    with pytest.raises(InvalidRequest, match="kind must be"):
        service.work_queue("everything")
    with pytest.raises(InvalidRequest, match="assign or close"):
        service.update_handoff("HND-3500", "escalate")
    with pytest.raises(NotFound):
        service.update_handoff("HND-0001", "assign")
    assert service.update_handoff("HND-3500", "close", "Explained monthly billing")["status"] == "closed"
    with pytest.raises(InvalidRequest, match="already closed"):
        service.update_handoff("HND-3500", "assign")


# ---------------------------------------------------------------- documents and profile changes
def test_policy_document_and_upload_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="document_type"):
        be.policy_document("POL-1001", "receipt")
    assert be.store_document("photo.jpg", "image/jpg", b"\xff\xd8\xff\xe0data")["content_type"] == "image/jpeg"
    with pytest.raises(InvalidRequest, match="empty"):
        be.store_document("a.pdf", "application/pdf", b"")
    with pytest.raises(InvalidRequest, match="at most 5 MB"):
        be.store_document("a.pdf", "application/pdf", b"%PDF" + b"0" * (5 * 1024 * 1024))
    with pytest.raises(NotFound):
        be.get_document("DOC-0001")


def test_profile_change_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="change_type"):
        be.change_requirements("phone_number")
    with pytest.raises(InvalidRequest, match="change_type"):
        be.submit_profile_change("phone_number", "x", "DOC-6001", "passport")
    with pytest.raises(InvalidRequest, match="incomplete"):
        be.submit_profile_change("mailing_address", "Main St", "DOC-6001", "utility_bill", effective_date="2026-10-01")
    with pytest.raises(InvalidRequest, match="outside 16-120"):
        be.submit_profile_change("date_of_birth", "2020-01-01", "DOC-6001", "passport", reason="typo")
    doc = be.store_document("passport.pdf", "application/pdf", PDF)["document_id"]
    with pytest.raises(InvalidRequest, match="already 1985-04-12"):
        be.submit_profile_change("date_of_birth", "1985-04-12", doc, "passport", reason="typo")


def test_admin_address_update(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest):
        be.update_address("1 St")
    out = be.update_address("  77 Lake Shore Dr, Chicago, IL 60611 ")
    assert out["old_address"].startswith("48 Oak Ave") and out["new_address"] == "77 Lake Shore Dr, Chicago, IL 60611"
    assert be.get_profile()["address"] == out["new_address"]


# ---------------------------------------------------------------- products, quotes and purchases
def test_products_and_quote_validation(db):
    be = InsuranceBackend(db, "CUST-003", TODAY)
    assert {p["code"] for p in be.list_products()} == {"AUTO", "HOME", "RENTERS", "TRAVEL", "LIFE"}
    with pytest.raises(InvalidRequest, match="coverage_level"):
        be.create_quote("RENTERS", "gold", 500, {})
    with pytest.raises(InvalidRequest, match="deductible"):
        be.create_quote("RENTERS", "standard", 300, {})
    with pytest.raises(NotFound, match="Product BOAT"):
        be.create_quote("BOAT", "standard", 500, {})
    with pytest.raises(InvalidRequest, match="Invalid insured item"):
        be.create_quote(
            "HOME", "standard", 500, {"property_address": "1 Main St", "year_built": "1990", "square_feet": "big"}
        )


def test_purchase_validation(db):
    be = InsuranceBackend(db, "CUST-003", TODAY)
    qid = _renters_quote(be)
    with pytest.raises(InvalidRequest, match="past"):
        be.purchase_policy(qid, start_date=(TODAY - timedelta(days=1)).isoformat())
    with pytest.raises(NotFound):
        be.purchase_policy("QTE-9999")
    with pytest.raises(InvalidRequest, match="expired"):
        InsuranceBackend(db, "CUST-003", TODAY + timedelta(days=31)).purchase_policy(qid)


# ---------------------------------------------------------------- policies, claims, drivers, riders, billing
def test_policy_change_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(NotFound, match="POL-9999"):
        be.get_policy("POL-9999")
    with pytest.raises(InvalidRequest, match="Provide"):
        be.update_coverage("POL-1002")
    with pytest.raises(InvalidRequest, match="deductible"):
        be.update_coverage("POL-1002", deductible=123)
    with pytest.raises(InvalidRequest, match="coverage_level"):
        be.update_coverage("POL-1002", coverage_level="gold")
    with pytest.raises(InvalidRequest, match="past"):
        be.cancel_policy("POL-1002", "moving", effective_date=(TODAY - timedelta(days=1)).isoformat())


def test_claim_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="claim_type"):
        be.file_claim("POL-1001", "2026-11-01", "meteor", "x", 100)
    with pytest.raises(InvalidRequest, match="positive"):
        be.file_claim("POL-1001", "2026-11-01", "collision", "x", 0)
    with pytest.raises(InvalidRequest, match="outside the policy period"):
        be.file_claim("POL-1001", (TODAY - timedelta(days=200)).isoformat(), "collision", "x", 100)
    assert [c["id"] for c in be.list_claims("POL-1001")] == ["CLM-5001"]
    assert be.list_claims("POL-1002") == []
    assert [c["id"] for c in be.list_claims()] == ["CLM-5001"]


def test_driver_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="not an auto policy"):
        be.list_drivers("POL-1002")
    with pytest.raises(InvalidRequest, match="full_name"):
        be.add_driver("POL-1001", "S", "1990-01-01", "spouse")
    for i, name in enumerate(["Asha", "Ravi", "Mira", "Dev"]):
        be.add_driver("POL-1001", name, f"199{i}-01-01", "other_household_member")
    with pytest.raises(InvalidRequest, match="at most 4"):
        be.add_driver("POL-1001", "Tara", "1995-01-01", "sibling")
    assert be.list_drivers("POL-1001")["additional_drivers"][0]["age"] == 36


def test_cancelled_policy_refuses_changes(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    be.cancel_policy("POL-1001", "Vehicle sold")
    with pytest.raises(InvalidRequest, match="already cancelled"):
        be.cancel_policy("POL-1001", "again")
    with pytest.raises(InvalidRequest, match="cancelled"):
        be.update_coverage("POL-1001", deductible=1000)
    with pytest.raises(InvalidRequest, match="claims require an active policy"):
        be.file_claim("POL-1001", "2026-11-01", "collision", "x", 100)
    with pytest.raises(InvalidRequest, match="cancelled"):
        be.add_driver("POL-1001", "Sri", "2000-01-01", "child")
    with pytest.raises(InvalidRequest, match="cancelled"):
        be.remove_driver("POL-1001", "DRV-3001")
    riders = {r["code"]: r for r in be.list_riders("POL-1001")["riders"]}
    assert riders["ROADSIDE"]["not_eligible_reason"] == "policy is cancelled"
    with pytest.raises(InvalidRequest, match="only a declarations page"):
        be.policy_document("POL-1001", "id_card")


def test_rental_car_needs_more_than_basic_cover_and_unknown_riders(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    be.update_coverage("POL-1001", coverage_level="basic")
    riders = {r["code"]: r for r in be.list_riders("POL-1001")["riders"]}
    assert not riders["RENTAL_CAR"]["eligible"] and "basic coverage" in riders["RENTAL_CAR"]["not_eligible_reason"]
    with pytest.raises(NotFound, match="Rider jetski not found"):
        be.add_rider("POL-1001", "jetski")


def test_payment_validation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(InvalidRequest, match="payment_method"):
        be.make_payment("POL-1002", 10, "cash")
    with pytest.raises(InvalidRequest, match="positive"):
        be.make_payment("POL-1002", 0, "card_on_file")
