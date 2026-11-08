"""Phase 2, persona 1 gaps: guests and applications, consent, identity re-check, policy documents, handoff."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from insurance_copilot.backend.db import (
    AccessDenied,
    ConsentRequired,
    InsuranceBackend,
    InvalidRequest,
    NotPermitted,
    VerificationRequired,
)
from insurance_copilot.backend.policy import DISCLOSURES
from insurance_copilot.core.session import open_session
from insurance_copilot.core.toolbox import McpToolbox
from insurance_copilot.mcp_server import build_server
from tests.conftest import TODAY, intent, plan
from tests.fakes import ScriptedLLM, text, tools
from tests.test_web import web  # noqa: F401  (fixture)

GUEST = "GUEST-0A1B2C3D"
APPLICANT = {
    "full_name": "Maya Lopez",
    "email": "maya@example.com",
    "date_of_birth": "1992-06-15",
    "address": "12 Elm St, Springfield, IL 62704",
    "phone": None,
}


def _q(db, sql, *params):
    return sqlite3.connect(db).execute(sql, params).fetchall()


def ok(operation, arguments):
    return {**arguments, "disclosure_accepted": DISCLOSURES[operation]["id"]}


def _quote(be):
    return be.execute(
        "get_quote",
        {
            "product_code": "RENTERS",
            "coverage_level": "standard",
            "deductible": 500,
            "insured_item": {
                "property_address": "12 Elm St, Springfield, IL 62704",
                "personal_property_value": "15000",
            },
        },
    )


async def _tools(db, customer, policy=None):
    async with Client(build_server(InsuranceBackend(db, customer, TODAY, policy))) as client:
        tb = McpToolbox(client)
        await tb.load()
        return tb.tools


# ---------------------------------------------------------------- guests: only the prospect tools
async def test_guest_gets_only_prospect_tools(db):
    guest = set(await _tools(db, GUEST))
    assert guest == {
        "list_products",
        "get_quote",
        "list_riders",
        "start_application",
        "get_application_status",
        "request_representative",
        "get_transaction_status",
        "list_transactions",
    }
    customer = set(await _tools(db, "CUST-001"))
    assert "start_application" not in customer and "get_policy_document" in customer
    assert "verify_identity" not in customer  # direct policy: no step-up
    assert "verify_identity" in await _tools(db, "CUST-001", "review")


def test_guest_cannot_reach_customer_operations(db):
    be = InsuranceBackend(db, GUEST, TODAY)
    with pytest.raises(NotPermitted):
        be.execute("make_payment", {"policy_id": "POL-1001", "amount": 5.0, "payment_method": "card_on_file"})
    with pytest.raises(NotPermitted):
        be.verify_identity("1985-04-12", "62701")


# ---------------------------------------------------------------- applications
def test_guest_quote_and_application(db):
    be = InsuranceBackend(db, GUEST, TODAY)
    q = _quote(be)
    out = be.execute("start_application", ok("start_application", {"quote_id": q["quote_id"], **APPLICANT}))
    assert out["application_id"] == "APP-9501" and out["status"] == "submitted" and out["is_policy"] is False
    assert out["request_tracking"]["status"] == "submitted"
    assert _q(db, "SELECT match_status FROM prospects") == [("new",)]
    assert _q(db, "SELECT status FROM quotes WHERE id=?", q["quote_id"]) == [("applied",)]
    assert _q(db, "SELECT consent, reference FROM transactions") == [("DSC-PRIVACY-v1", "APP-9501")]
    assert _q(db, "SELECT COUNT(*) FROM policies WHERE customer_id=?", GUEST) == [(0,)]
    assert be.get_application("APP-9501")["status"] == "submitted"
    with pytest.raises(AccessDenied):  # another guest session can't see it
        InsuranceBackend(db, "GUEST-FFFFFFFF", TODAY).get_application("APP-9501")
    with pytest.raises(InvalidRequest):  # a quote applies once
        be.execute("start_application", ok("start_application", {"quote_id": q["quote_id"], **APPLICANT}))


def test_application_needs_consent(db):
    be = InsuranceBackend(db, GUEST, TODAY)
    q = _quote(be)
    with pytest.raises(ConsentRequired):
        be.execute("start_application", {"quote_id": q["quote_id"], **APPLICANT})
    assert _q(db, "SELECT COUNT(*) FROM applications") == [(0,)]


def test_matching_rules_flag_existing_customer_without_telling_the_applicant(db):
    be = InsuranceBackend(db, GUEST, TODAY)
    new = be.execute("start_application", ok("start_application", {"quote_id": _quote(be)["quote_id"], **APPLICANT}))
    known = be.execute(
        "start_application",
        ok(
            "start_application",
            {"quote_id": _quote(be)["quote_id"], **APPLICANT, "email": "Shan@Example.com", "full_name": "Shan"},
        ),
    )
    assert set(new) == set(known) and known["status"] == "submitted"  # same answer either way
    assert _q(db, "SELECT email, match_status FROM prospects ORDER BY id") == [
        ("maya@example.com", "new"),
        ("shan@example.com", "possible_existing_customer"),
    ]
    be.execute("start_application", ok("start_application", {"quote_id": _quote(be)["quote_id"], **APPLICANT}))
    assert _q(db, "SELECT COUNT(*) FROM prospects") == [(2,)]  # same email: no duplicate prospect


def test_applicant_details_are_validated(db):
    be = InsuranceBackend(db, GUEST, TODAY)
    q = _quote(be)
    for bad in ({"email": "maya@"}, {"date_of_birth": "2012-01-01"}, {"address": "Elm St"}):
        with pytest.raises(InvalidRequest):
            be.execute("start_application", ok("start_application", {"quote_id": q["quote_id"], **APPLICANT, **bad}))
    with pytest.raises(AccessDenied):  # a signed-in customer's quote
        cust_q = _quote(InsuranceBackend(db, "CUST-001", TODAY))
        be.execute("start_application", ok("start_application", {"quote_id": cust_q["quote_id"], **APPLICANT}))


# ---------------------------------------------------------------- consent is captured by the app, not the model
def test_purchase_and_cancel_need_the_disclosure(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    with pytest.raises(ConsentRequired):
        be.execute(
            "cancel_policy",
            {"policy_id": "POL-1001", "reason": "x", "effective_date": None, "disclosure_accepted": "DSC-OLD"},
        )
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1001'") == [("active",)]
    be.execute("cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None}))
    assert _q(db, "SELECT consent FROM transactions") == [("DSC-CANCEL-v1",)]


async def test_model_never_sees_or_sets_the_consent_argument(settings, db):
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")] * 2,
        executor=[
            tools(
                ("cancel_policy", {"policy_id": "POL-1002", "reason": "sold", "disclosure_accepted": "DSC-CANCEL-v1"})
            ),
            text("Not cancelled."),
            tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
            text("Cancelled."),
        ],
    )
    answers, asked = iter([False, True]), []
    async with open_session(
        customer_id="CUST-001", confirm=lambda r: asked.append(r) or next(answers), settings=settings, llm=llm
    ) as session:
        schema = session.toolbox.tools["cancel_policy"].to_anthropic()["input_schema"]
        assert "disclosure_accepted" not in schema["properties"]
        declined = await session.send("cancel my home policy")
        assert "disclosure_accepted" not in declined.tool_calls[0].arguments  # model's value dropped
        assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("active",)]
        approved = await session.send("yes, cancel it")
    assert asked[0].disclosure == DISCLOSURES["cancel_policy"]["text"]
    assert approved.tool_calls[0].arguments["disclosure_accepted"] == "DSC-CANCEL-v1"
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("cancelled",)]


# ---------------------------------------------------------------- identity re-check (review policy)
def test_step_up_before_a_cancellation(db):
    be = InsuranceBackend(db, "CUST-001", TODAY, "review")
    args = ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None})
    with pytest.raises(VerificationRequired):
        be.execute("cancel_policy", args)
    assert be.verify_identity("1985-04-12", "62701")["verified"]
    assert be.execute("cancel_policy", args)["status"] == "pending_review"


def test_failed_checks_lock_and_store_no_answers(db):
    be = InsuranceBackend(db, "CUST-001", TODAY, "review")
    first = be.verify_identity("1985-04-13", "62701")
    assert first == {**first, "verified": False, "attempts_left": 2} and "1985-04-12" not in json.dumps(first)
    be.verify_identity("1985-04-12", "99999")
    assert be.verify_identity("not a date", "62701")["attempts_left"] == 0
    with pytest.raises(NotPermitted):
        be.verify_identity("1985-04-12", "62701")  # locked even with the right answers
    assert _q(db, "SELECT outcome FROM identity_checks") == [("failed",)] * 3
    assert "1985" not in json.dumps(_q(db, "SELECT * FROM identity_checks"))


async def test_engine_asks_for_the_check_before_the_approval_prompt(settings, db):
    settings.txn_policy = "review"
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")] * 2,
        executor=[
            tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
            text("What is your date of birth and ZIP code?"),
            tools(("verify_identity", {"date_of_birth": "1985-04-12", "postal_code": "62701"})),
            tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
            text("Submitted."),
        ],
    )
    asked = []
    async with open_session(
        customer_id="CUST-001", confirm=lambda r: asked.append(r) or True, settings=settings, llm=llm
    ) as session:
        first = await session.send("cancel my home policy")
        assert first.tool_calls[0].blocked_reason == "VERIFICATION_REQUIRED" and not asked
        state = session.export_state()
        second = await session.send("1985-04-12, 62701")
    assert len(asked) == 1 and second.tool_calls[1].executed and state["verified_at"] is None
    assert session.export_state()["verified_at"]
    assert _q(db, "SELECT status FROM transactions") == [("pending_review",)]


async def test_verify_identity_audit_is_redacted(db):
    async with Client(build_server(InsuranceBackend(db, "CUST-001", TODAY, "review"))) as client:
        tb = McpToolbox(client)
        await tb.load()
        await tb.call("verify_identity", {"date_of_birth": "1985-04-12", "postal_code": "62701"})
    assert _q(db, "SELECT arguments FROM audit_log WHERE tool='verify_identity'") == [
        ('{"date_of_birth": "[redacted]", "postal_code": "[redacted]"}',)
    ]


# ---------------------------------------------------------------- policy documents
def test_policy_documents(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    out = be.policy_document("POL-1001", "id_card")
    assert out["download_url"] == f"/api/documents/{out['document_id']}"
    doc = be.get_document(out["document_id"])
    assert doc["content"].startswith(b"%PDF-1.4") and b"Toyota" in doc["content"] and doc["source"] == "generated"
    assert be.policy_document("POL-1001", "id_card")["document_id"] == out["document_id"]  # same day: reused
    with pytest.raises(InvalidRequest):
        be.policy_document("POL-1002", "id_card")  # home policy
    with pytest.raises(AccessDenied):
        be.policy_document("POL-2001", "declarations")  # someone else's
    with pytest.raises(AccessDenied):
        InsuranceBackend(db, "CUST-002", TODAY).get_document(out["document_id"])
    be.execute("cancel_policy", ok("cancel_policy", {"policy_id": "POL-1002", "reason": "x", "effective_date": None}))
    with pytest.raises(InvalidRequest):
        be.policy_document("POL-1002", "proof_of_insurance")
    assert be.policy_document("POL-1002", "declarations")["filename"] == "POL-1002 Declarations page.pdf"


# ---------------------------------------------------------------- talking to a person
def test_request_representative(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    args = {
        "topic": "billing dispute",
        "summary": "Disputes the $193.60 balance on POL-1002.",
        "preferred_channel": "email",
        "contact": None,
        "callback_time": None,
    }
    with pytest.raises(ConsentRequired):
        be.execute("request_representative", args)
    out = be.execute("request_representative", ok("request_representative", args))
    assert out["handoff_id"] == "HND-3501" and "shan@example.com" in out["next_step"]
    assert out["request_tracking"]["status"] == "submitted"
    assert _q(db, "SELECT customer_id, channel, contact, status FROM handoffs") == [
        ("CUST-001", "email", "shan@example.com", "open")
    ]
    guest = InsuranceBackend(db, GUEST, TODAY)
    with pytest.raises(InvalidRequest):  # a guest has no email on file
        guest.execute("request_representative", ok("request_representative", args))
    with pytest.raises(InvalidRequest):
        guest.execute(
            "request_representative",
            ok("request_representative", {**args, "preferred_channel": "phone", "contact": "555-01"}),
        )
    out = guest.execute(
        "request_representative",
        ok("request_representative", {**args, "preferred_channel": "phone", "contact": "(217) 555-0142"}),
    )
    assert out["handoff_id"] == "HND-3502"


# ---------------------------------------------------------------- web: continue as a guest
def test_web_guest_session(web):  # noqa: F811
    app_mod, _, _ = web
    client = TestClient(app_mod.app)
    r = client.post("/api/guest")
    assert r.status_code == 200 and r.json()["customer_id"].startswith("GUEST-")
    assert client.get("/api/me").json()["display_name"] == "Guest"
    assert client.post("/api/conversations").status_code == 200
    other = TestClient(app_mod.app).post("/api/guest").json()
    assert other["customer_id"] != r.json()["customer_id"]
