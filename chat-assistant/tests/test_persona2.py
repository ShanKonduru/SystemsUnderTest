"""Phase 3, persona 2: Harbor Mutual representatives.

Roles and least privilege, customer search and verification, acting for the verified customer only, notes and
tasks, the work queue (approvals with dual control, applications, cases) and the two-actor audit trail."""

import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient
from mcp import Client

from insurance_copilot.backend.db import (
    AccessDenied,
    CustomerNotVerified,
    InsuranceBackend,
    InvalidRequest,
    NotFound,
    NotPermitted,
)
from insurance_copilot.backend.policy import DISCLOSURES
from insurance_copilot.core.session import open_session
from insurance_copilot.core.toolbox import McpToolbox
from insurance_copilot.core.trace import Entity, RepIntentResult
from insurance_copilot.mcp_server import build_server
from tests.conftest import TODAY, plan
from tests.fakes import ScriptedLLM, text, tools
from tests.test_persona1 import APPLICANT, GUEST, _quote, ok
from tests.test_web import web  # noqa: F401  (fixture)

SERVICE, AGENT, SUPERVISOR = "REP-201", "REP-202", "REP-203"  # Rivera (book: CUST-001, CUST-003), Chen, Patel
SHAN = ("CUST-001", "1985-04-12", "62701")
INDU = ("CUST-002", "1990-09-30", "62565")


def _q(db, sql, *params):
    return sqlite3.connect(db).execute(sql, params).fetchall()


def rep(db, rep_id, customer=None):
    be = InsuranceBackend(db, rep_id, TODAY)
    if customer:
        assert be.verify_customer(*customer)["verified"]
    return be


def rep_intent(*names, **entities):
    return RepIntentResult(
        primary_intent=names[0],
        intents=list(names),
        entities=[Entity(name=k, value=v) for k, v in entities.items()],
        needs_clarification=False,
        clarification_question="",
        confidence="high",
    )


async def _tools(db, principal):
    async with Client(build_server(InsuranceBackend(db, principal, TODAY))) as client:
        tb = McpToolbox(client)
        await tb.load()
        return tb.tools


# ---------------------------------------------------------------- authentication, role and tools
async def test_representatives_get_rep_tools_and_customers_do_not(db):
    rep_tools = await _tools(db, SERVICE)
    customer_tools = set(await _tools(db, "CUST-001"))
    rep_only = {
        "get_my_role",
        "search_customers",
        "verify_customer",
        "end_customer_session",
        "get_service_history",
        "add_note",
        "complete_task",
        "list_work_queue",
        "review_request",
        "issue_application",
        "decline_application",
        "update_handoff",
    }
    assert rep_only <= set(rep_tools) and not rep_only & customer_tools
    assert {"list_policies", "make_payment", "start_application"} <= set(rep_tools)
    assert "request_representative" not in rep_tools and "verify_identity" not in rep_tools
    # Levels in the tool metadata follow the role: a service rep's purchases are held for approval.
    assert rep_tools["purchase_policy"].level == "submit" and rep_tools["make_payment"].level == "commit"
    assert (await _tools(db, AGENT))["purchase_policy"].level == "commit"
    assert rep_tools["issue_application"].disclosure == DISCLOSURES["purchase_policy"]


def test_unknown_representative_is_refused(db):
    with pytest.raises(NotFound):
        InsuranceBackend(db, "REP-999", TODAY)


def test_my_role_reports_least_privilege(db):
    role = rep(db, SERVICE).my_role()
    assert role["role"] == "service_rep" and not role["licensed"] and not role["can_approve_others_requests"]
    assert role["book"] == ["CUST-001", "CUST-003"] and role["active_customer"] is None
    assert role["operation_levels"]["decline_application"] == "read"
    assert role["level_overrides"]["submit_profile_change"] == {"change_type": {"date_of_birth": "submit"}}
    assert rep(db, SUPERVISOR).my_role()["can_approve_others_requests"]


# ---------------------------------------------------------------- search and verification
def test_search_is_limited_to_the_book_and_minimal(db):
    be = rep(db, SERVICE)
    found = be.search_customers("POL-1002")["customers"]
    assert found == [
        {
            "customer_id": "CUST-001",
            "name": "Shan",
            "email": "s***@example.com",
            "city": "Springfield, IL",
            "active_policies": 2,
        }
    ]
    assert "date_of_birth" not in json.dumps(found) and "Oak Ave" not in json.dumps(found)
    outside = be.search_customers("indu@example.com")  # not in Rivera's book
    assert outside["customers"] == [] and outside["matches_outside_your_book"] == 1
    assert "Indu" not in json.dumps(outside) and "CUST-002" not in json.dumps(outside)
    assert "matches_outside_your_book" not in found
    assert rep(db, AGENT).search_customers("indu@example.com")["customers"][0]["customer_id"] == "CUST-002"
    with pytest.raises(InvalidRequest):
        be.search_customers("ab")


def test_customer_tools_need_a_verified_customer(db):
    be = rep(db, SERVICE)
    with pytest.raises(CustomerNotVerified):
        be.list_policies()
    with pytest.raises(CustomerNotVerified):
        be.execute("make_payment", {"policy_id": "POL-1002", "amount": 5.0, "payment_method": "card_on_file"})
    failed = be.verify_customer("CUST-001", "1985-04-13", "62701")
    assert not failed["verified"] and failed["attempts_left"] == 2 and "1985-04-12" not in json.dumps(failed)
    with pytest.raises(CustomerNotVerified):
        be.list_policies()
    assert be.verify_customer(*SHAN)["verified"]
    assert {p["id"] for p in be.list_policies()} == {"POL-1001", "POL-1002"}
    assert _q(db, "SELECT method, outcome FROM identity_checks") == [
        ("rep:REP-201 date_of_birth+zip", "failed"),
        ("rep:REP-201 date_of_birth+zip", "passed"),
    ]


def test_out_of_book_customer_cannot_be_opened(db):
    with pytest.raises(AccessDenied):
        rep(db, SERVICE).verify_customer(*INDU)
    assert _q(db, "SELECT COUNT(*) FROM identity_checks") == [(0,)]  # refused before any check


def test_verification_locks_after_repeated_failures(db):
    be = rep(db, AGENT)
    for _ in range(3):
        assert not be.verify_customer("CUST-002", "2000-01-01", "62565")["verified"]
    with pytest.raises(NotPermitted):
        be.verify_customer(*INDU)


def test_one_customer_at_a_time_and_ending_the_session(db):
    be = rep(db, AGENT, SHAN)
    be.verify_customer(*INDU)
    again = InsuranceBackend(db, AGENT, TODAY)  # a new tool server picks up the open customer
    assert {p["id"] for p in again.list_policies()} == {"POL-2001", "POL-2002"}
    with pytest.raises(AccessDenied):
        again.get_policy("POL-1001")  # Shan's policy: no longer open
    assert again.end_customer_session()["closed"] == "CUST-002"
    with pytest.raises(CustomerNotVerified):
        InsuranceBackend(db, AGENT, TODAY).list_policies()


def test_service_context_expires(db):
    be = rep(db, AGENT, SHAN)
    be.list_policies()
    assert _q(db, "SELECT COUNT(*) FROM service_contexts") == [(1,)]  # reads don't write
    sqlite3.connect(db).execute("UPDATE service_contexts SET opened_at='2000-01-01T00:00:00+00:00'").connection.commit()
    be.load_context()
    with pytest.raises(CustomerNotVerified):
        be.list_policies()
    with pytest.raises(InvalidRequest):
        be.end_customer_session()  # nothing open to close


# ---------------------------------------------------------------- servicing within the role, two-actor trail
def test_service_rep_commits_routine_changes_and_holds_restricted_ones(db):
    be = rep(db, SERVICE, SHAN)
    paid = be.execute("make_payment", {"policy_id": "POL-1002", "amount": 50.0, "payment_method": "card_on_file"})
    assert paid["request_tracking"]["status"] == "committed"
    held = be.execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1002", "reason": "sold", "effective_date": None})
    )
    assert held["status"] == "pending_review" and "supervisor" in held["next_step"]
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("active",)]
    assert _q(db, "SELECT customer_id, actor, status FROM transactions ORDER BY id") == [
        ("CUST-001", "rep:REP-201", "committed"),
        ("CUST-001", "rep:REP-201", "pending_review"),
    ]
    assert _q(db, "SELECT DISTINCT customer_id, actor FROM audit_log") == []  # audit is written by the tool server


def test_consent_still_applies_when_a_rep_acts(db):
    be = rep(db, AGENT, SHAN)
    with pytest.raises(Exception, match="CONSENT_REQUIRED"):
        be.execute("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None})
    be.execute("cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None}))
    assert _q(db, "SELECT actor, consent FROM transactions") == [("rep:REP-202", "DSC-CANCEL-v1")]


def test_supervisor_approval_dual_control(db):
    held = rep(db, SERVICE, SHAN).execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1002", "reason": "sold", "effective_date": None})
    )
    txn = held["transaction_id"]
    with pytest.raises(NotPermitted):
        rep(db, AGENT).review_request(txn, "approve")  # not an approver
    with pytest.raises(InvalidRequest):
        rep(db, SUPERVISOR).review_request(txn, "reject")  # rejecting needs a reason
    done = rep(db, SUPERVISOR).review_request(txn, "approve", "confirmed sale of home")
    assert done["status"] == "committed" and done["decided_by"] == "M. Patel (REP-203)"
    assert done["actor"] == "rep:REP-201"  # requester and approver are different people
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("cancelled",)]

    # Supervisors also work requests customers made under the review policy.
    customer_held = InsuranceBackend(db, "CUST-001", TODAY, "review").execute(
        "update_policy_coverage", {"policy_id": "POL-1001", "deductible": 1000, "coverage_level": None}
    )
    assert rep(db, SUPERVISOR).review_request(customer_held["transaction_id"], "approve")["status"] == "committed"


def test_a_supervisor_cannot_approve_their_own_request(db):
    sup = rep(db, SUPERVISOR, SHAN)
    # Supervisors commit directly, so plant a held request made by them to check the rule.
    sqlite3.connect(db).execute(
        "INSERT INTO transactions (id, customer_id, operation, arguments, level, status, actor, created_on, created_at) "
        "VALUES ('TXN-10001','CUST-001','make_payment','{\"amount\": 5.0, \"payment_method\": \"card_on_file\", "
        "\"policy_id\": \"POL-1002\"}','submit','pending_review','rep:REP-203','2026-11-03','2026-11-03T00:00:00')"
    ).connection.commit()
    with pytest.raises(NotPermitted, match="dual control"):
        sup.review_request("TXN-10001", "approve")


def test_supervisor_queue_and_book_filter(db):
    rep(db, SERVICE, SHAN).execute(
        "purchase_policy", ok("purchase_policy", {"quote_id": _shan_quote(db), "start_date": None})
    )
    InsuranceBackend(db, "CUST-002", TODAY, "review").execute(
        "update_policy_coverage", {"policy_id": "POL-2001", "deductible": 500, "coverage_level": None}
    )
    rivera = rep(db, SERVICE).work_queue("requests")["requests_pending_review"]
    patel = rep(db, SUPERVISOR).work_queue("requests")["requests_pending_review"]
    assert [r["customer_id"] for r in rivera] == ["CUST-001"]  # Indu is outside Rivera's book
    assert [(r["customer_id"], r["you_can_approve"]) for r in patel] == [("CUST-001", True), ("CUST-002", True)]


def _shan_quote(db):
    be = rep(db, SERVICE, SHAN)
    return be.execute(
        "get_quote",
        {
            "product_code": "LIFE",
            "coverage_level": "standard",
            "deductible": 500,
            "insured_item": {"coverage_amount": "250000", "term_years": "20"},
        },
    )["quote_id"]


# ---------------------------------------------------------------- notes and history (internal)
def test_notes_tasks_and_history_are_internal(db):
    be = rep(db, SERVICE, SHAN)
    note = be.add_note("Called about the home bill; explained due date.")
    task = be.add_note("Call back after payment posts", follow_up_on="2026-11-10")
    assert note["kind"] == "note" and task["kind"] == "task"
    with pytest.raises(InvalidRequest):
        be.add_note("x", follow_up_on="2026-01-01")
    history = be.service_history()
    assert {n["note_id"] for n in history["notes"]} == {note["note_id"], task["note_id"]} and history["internal_only"]
    assert [t["task_id"] for t in be.work_queue("tasks")["my_tasks"]] == [task["note_id"]]
    with pytest.raises(AccessDenied):
        rep(db, AGENT).complete_task(task["note_id"])  # someone else's task
    assert be.complete_task(task["note_id"])["status"] == "done"
    # Customers never see notes: no customer tool reads the notes table.
    customer = InsuranceBackend(db, "CUST-001", TODAY)
    assert "Called about" not in json.dumps([customer.list_transactions(), customer.list_documents()])


async def test_customer_tools_never_expose_notes(db):
    tools = await _tools(db, "CUST-001")
    assert not {"get_service_history", "add_note", "list_work_queue"} & set(tools)


# ---------------------------------------------------------------- cases (handoffs)
def test_cases_assign_and_close(db):
    handoff = InsuranceBackend(db, "CUST-002", TODAY).execute(
        "request_representative",
        ok(
            "request_representative",
            {
                "topic": "billing",
                "summary": "Wants to discuss the bill",
                "preferred_channel": "phone",
                "contact": "217-555-0100",
                "callback_time": None,
            },
        ),
    )["handoff_id"]
    assert rep(db, SERVICE).work_queue("cases")["cases"] == []  # Indu: outside Rivera's book
    with pytest.raises(AccessDenied):
        rep(db, SERVICE).update_handoff(handoff, "assign")
    chen = rep(db, AGENT)
    assert chen.update_handoff(handoff, "assign")["assigned_to"] == "A. Chen (REP-202)"
    with pytest.raises(InvalidRequest):
        chen.update_handoff(handoff, "close")
    assert chen.update_handoff(handoff, "close", "Explained the bill; customer satisfied")["status"] == "closed"
    assert _q(db, "SELECT status, resolution FROM handoffs") == [("closed", "Explained the bill; customer satisfied")]


# ---------------------------------------------------------------- onboarding and applications
def _guest_application(db, **overrides):
    guest = InsuranceBackend(db, GUEST, TODAY)
    quote = _quote(guest)["quote_id"]
    return guest.execute("start_application", ok("start_application", {"quote_id": quote, **APPLICANT, **overrides}))[
        "application_id"
    ]


def test_licensed_agent_issues_an_application_and_creates_the_customer(db):
    app = _guest_application(db)
    with pytest.raises(NotPermitted):
        rep(db, SERVICE).execute("decline_application", {"application_id": app, "reason": "x"})
    with pytest.raises(Exception, match="CONSENT_REQUIRED"):
        rep(db, AGENT).execute(
            "issue_application", {"application_id": app, "existing_customer_id": None, "start_date": None}
        )
    issued = rep(db, AGENT).execute(
        "issue_application",
        ok("issue_application", {"application_id": app, "existing_customer_id": None, "start_date": None}),
    )
    assert issued["status"] == "issued" and issued["new_customer"] and issued["customer_id"] == "CUST-004"
    assert _q(db, "SELECT customer_id, status FROM policies WHERE id=?", issued["policy_id"]) == [
        ("CUST-004", "active")
    ]
    assert _q(db, "SELECT name, email FROM customers WHERE id='CUST-004'") == [("Maya Lopez", "maya@example.com")]
    assert _q(db, "SELECT reference, customer_id, actor FROM transactions WHERE operation='issue_application'") == [
        (issued["policy_id"], "PRS-9001", "rep:REP-202")
    ]
    # The applicant now sees the outcome on their application.
    assert InsuranceBackend(db, GUEST, TODAY).get_application(app)["status"] == "issued"


def test_service_rep_issue_is_held_and_supervisor_approves(db):
    app = _guest_application(db)
    held = rep(db, SERVICE).execute(
        "issue_application",
        ok("issue_application", {"application_id": app, "existing_customer_id": None, "start_date": None}),
    )
    assert held["status"] == "pending_review" and _q(db, "SELECT COUNT(*) FROM customers") == [(3,)]
    done = rep(db, SUPERVISOR).review_request(held["transaction_id"], "approve")
    assert done["status"] == "committed" and done["reference"].startswith("POL-")
    assert _q(db, "SELECT status, decided_by FROM applications") == [("issued", "J. Rivera (REP-201)")]


def test_possible_existing_customer_must_be_matched_or_declined(db):
    app = _guest_application(db, full_name="Shan", email="shan@example.com", date_of_birth="1985-04-12")
    chen = rep(db, AGENT)
    issue = {"application_id": app, "existing_customer_id": None, "start_date": None}
    with pytest.raises(InvalidRequest, match="already be a customer"):
        chen.execute("issue_application", ok("issue_application", issue))
    with pytest.raises(CustomerNotVerified):  # adding to an account needs that customer verified
        chen.execute("issue_application", ok("issue_application", {**issue, "existing_customer_id": "CUST-001"}))
    chen.verify_customer(*INDU)
    with pytest.raises(InvalidRequest, match="doesn't match"):
        chen.execute("issue_application", ok("issue_application", {**issue, "existing_customer_id": "CUST-002"}))
    chen.verify_customer(*SHAN)
    issued = chen.execute("issue_application", ok("issue_application", {**issue, "existing_customer_id": "CUST-001"}))
    assert issued["customer_id"] == "CUST-001" and not issued["new_customer"]
    assert _q(db, "SELECT COUNT(*) FROM customers") == [(3,)]


def test_decline_application(db):
    app = _guest_application(db)
    out = rep(db, AGENT).execute("decline_application", {"application_id": app, "reason": "Outside appetite"})
    assert out["status"] == "declined"
    with pytest.raises(InvalidRequest):
        rep(db, AGENT).execute("decline_application", {"application_id": app, "reason": "again"})


def test_rep_onboards_a_new_customer_without_an_open_customer(db):
    be = rep(db, SERVICE)
    assert be.search_customers("maya@example.com") == {
        **be.search_customers("maya@example.com"),
        "customers": [],
        "prospects": [],
    }
    quote = _quote(be)["quote_id"]
    app = be.execute("start_application", ok("start_application", {"quote_id": quote, **APPLICANT}))
    assert app["status"] == "submitted" and not app["is_policy"]
    assert _q(db, "SELECT guest_id FROM applications") == [("REP-201",)]
    assert be.search_customers("maya@example.com")["prospects"][0]["applications"][0]["status"] == "submitted"
    opened = rep(db, SERVICE, SHAN)
    with pytest.raises(NotPermitted):  # an open customer buys with purchase_policy
        opened.execute("start_application", ok("start_application", {"quote_id": quote, **APPLICANT}))


# ---------------------------------------------------------------- through the tool server and the engine
async def test_audit_records_the_representative_and_the_customer(db):
    async with Client(build_server(InsuranceBackend(db, SERVICE, TODAY))) as client:
        tb = McpToolbox(client)
        await tb.load()
        denied = await tb.call("list_policies", {})
        assert denied.is_error and "CUSTOMER_NOT_VERIFIED" in denied.text
        await tb.call(
            "verify_customer", {"customer_id": "CUST-001", "date_of_birth": "1985-04-12", "postal_code": "62701"}
        )
        await tb.call("list_policies", {})
    assert _q(db, "SELECT customer_id, tool, arguments, outcome, actor FROM audit_log ORDER BY id") == [
        ("REP-201", "list_policies", '{"include_cancelled": false}', "error", "rep:REP-201"),
        (
            "CUST-001",
            "verify_customer",
            '{"customer_id": "CUST-001", "date_of_birth": "[redacted]", "postal_code": "[redacted]"}',
            "ok",
            "rep:REP-201",
        ),
        ("CUST-001", "list_policies", '{"include_cancelled": false}', "ok", "rep:REP-201"),
    ]


async def test_rep_engine_uses_rep_prompts(settings, db):
    llm = ScriptedLLM(
        structured=[rep_intent("find_customer", "billing"), plan("search_customers", "verify_customer", "respond")],
        executor=[
            tools(("search_customers", {"query": "POL-1002"})),
            tools(
                ("verify_customer", {"customer_id": "CUST-001", "date_of_birth": "1985-04-12", "postal_code": "62701"})
            ),
            tools(("get_billing", {"policy_id": "POL-1002"})),
            text("Verified; balance is $193.60."),
        ],
    )
    async with open_session(customer_id=SERVICE, confirm=lambda r: True, settings=settings, llm=llm) as session:
        turn = await session.send("Caller on POL-1002, DOB 1985-04-12, ZIP 62701, asking about the bill")
    assert [c.name for c in turn.tool_calls] == ["search_customers", "verify_customer", "get_billing"]
    assert all(c.executed and not c.is_error for c in turn.tool_calls)
    assert "representative" in llm.create_calls[0]["system"] and "verify_customer" in llm.create_calls[0]["system"]
    assert "Signed in: representative REP-201" in json.dumps(llm.create_calls[0]["messages"])


# ---------------------------------------------------------------- web: representatives sign in
def test_web_representative_login(web):  # noqa: F811
    app_mod, _, _ = web
    client = TestClient(app_mod.app)
    r = client.post("/api/login", json={"username": "patel", "password": os.environ["DEMO_USER_PASSWORD"]})
    assert r.status_code == 200 and r.json()["customer_id"] == "REP-203"


# ---------------------------------------------------------------- phase 4: role x action matrix (scope section 6)
# How far each operation goes per role: commit = done now, submit = held for approval (pending_review) unless the
# operation is itself a submission, read = refused. This is the contract the harness's REP_* suites sample.
MATRIX = {
    #                        service_rep  licensed_agent  supervisor
    "make_payment": ("commit", "commit", "commit"),
    "file_claim": ("submit", "submit", "submit"),
    "update_policy_coverage": ("commit", "commit", "commit"),
    "add_driver": ("commit", "commit", "commit"),
    "add_rider": ("commit", "commit", "commit"),
    "purchase_policy": ("submit", "commit", "commit"),
    "cancel_policy": ("submit", "commit", "commit"),
    "start_application": ("submit", "submit", "submit"),
    "issue_application": ("submit", "commit", "commit"),
    "decline_application": ("read", "commit", "commit"),
}
DOB_CORRECTION = ("submit", "submit", "commit")
ROLE_REPS = (SERVICE, AGENT, SUPERVISOR)


@pytest.mark.parametrize("operation", sorted(MATRIX))
def test_role_action_matrix(db, operation):
    got = tuple(InsuranceBackend(db, r, TODAY).policy.effective(operation) for r in ROLE_REPS)
    assert got == MATRIX[operation]


def test_role_matrix_date_of_birth_corrections(db):
    args = {"change_type": "date_of_birth"}
    assert (
        tuple(InsuranceBackend(db, r, TODAY).policy.effective("submit_profile_change", args) for r in ROLE_REPS)
        == DOB_CORRECTION
    )
    assert (
        InsuranceBackend(db, SERVICE, TODAY).policy.effective(
            "submit_profile_change", {"change_type": "mailing_address"}
        )
        == "commit"
    )


@pytest.mark.parametrize("rep_id,can_approve", [(SERVICE, False), (AGENT, False), (SUPERVISOR, True)])
def test_matrix_approval_authority(db, rep_id, can_approve):
    held = rep(db, SERVICE, SHAN).execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1002", "reason": "sold", "effective_date": None})
    )
    if can_approve:
        assert rep(db, rep_id).review_request(held["transaction_id"], "approve")["status"] == "committed"
    else:
        with pytest.raises(NotPermitted):
            rep(db, rep_id).review_request(held["transaction_id"], "approve")


async def test_engine_refuses_without_an_approval_prompt_when_the_role_forbids_it(settings, db):
    llm = ScriptedLLM(
        structured=[rep_intent("work_queue"), plan("decline_application", "respond")],
        executor=[
            tools(("decline_application", {"application_id": "APP-9501", "reason": "x"})),
            text("A licensed agent has to decide that."),
        ],
    )
    asked = []
    async with open_session(
        customer_id=SERVICE, confirm=lambda r: asked.append(r) or True, settings=settings, llm=llm
    ) as session:
        assert session.toolbox.tools["review_request"].level == "read"
        turn = await session.send("decline APP-9501")
    assert not asked and turn.tool_calls[0].blocked_reason == "NOT_PERMITTED" and not turn.tool_calls[0].executed


def test_seed_backlog_keeps_generated_ids(tmp_path):
    from insurance_copilot.backend.db import init_db

    path = tmp_path / "queue.db"
    init_db(path, TODAY, queue=True)
    queue = rep(path, SUPERVISOR).work_queue()
    assert [r["transaction_id"] for r in queue["requests_pending_review"]] == ["TXN-10000"]
    assert {a["application_id"]: a["match_check"] for a in queue["applications"]} == {
        "APP-9500": "new",
        "APP-9499": "possible_existing_customer",
    }
    assert [c["handoff_id"] for c in rep(path, SERVICE).work_queue("cases")["cases"]] == ["HND-3500"]
    shan = InsuranceBackend(path, "CUST-001", TODAY)
    assert (
        shan.execute("make_payment", {"policy_id": "POL-1002", "amount": 5.0, "payment_method": "card_on_file"})[
            "request_tracking"
        ]["transaction_id"]
        == "TXN-10001"
    )
    assert _quote(InsuranceBackend(path, GUEST, TODAY))["quote_id"] == "QTE-7001"
