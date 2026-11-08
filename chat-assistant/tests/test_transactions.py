"""Phase 1 foundation: action levels, the transaction policy, transaction records, review and duplicates."""

import json
import sqlite3

import pytest
from mcp import Client

from insurance_copilot.backend.db import (
    AccessDenied,
    DuplicateRequest,
    InsuranceBackend,
    InvalidRequest,
    NotPermitted,
)
from insurance_copilot.backend.policy import DISCLOSURES, POLICIES, TransactionPolicy
from insurance_copilot.backend.review import main as review_main
from insurance_copilot.core.session import open_session
from insurance_copilot.core.toolbox import McpToolbox
from insurance_copilot.mcp_server import build_server
from tests.conftest import TODAY, intent, plan
from tests.fakes import ScriptedLLM, text, tools


def _q(db, sql, *params):
    return sqlite3.connect(db).execute(sql, params).fetchall()


def ok(operation, arguments):
    """Arguments as the app sends them once the customer approved the prompt showing the disclosure."""
    return {**arguments, "disclosure_accepted": DISCLOSURES[operation]["id"]}


def verified(be):
    """The review policy asks for an identity re-check before a cancellation (Shan: 1985-04-12, ZIP 62701)."""
    assert be.verify_identity("1985-04-12", "62701")["verified"]
    return be


# ---------------------------------------------------------------- direct policy (default): same behaviour as before
def test_direct_commit_is_applied_and_recorded(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    out = be.execute("make_payment", {"policy_id": "POL-1002", "amount": 50.0, "payment_method": "card_on_file"})
    assert out["payment_id"] == "PAY-9001"
    assert out["request_tracking"] == {"transaction_id": "TXN-10001", "status": "committed"}
    assert _q(db, "SELECT balance_due FROM policies WHERE id='POL-1002'") == [(143.6,)]
    txn = be.get_transaction("TXN-10001")
    assert (txn["status"], txn["level"], txn["reference"], txn["actor"]) == (
        "committed",
        "commit",
        "PAY-9001",
        "customer:CUST-001",
    )


def test_claim_is_submitted_not_committed(db):
    out = InsuranceBackend(db, "CUST-001", TODAY).execute(
        "file_claim",
        {
            "policy_id": "POL-1001",
            "incident_date": "2026-11-02",
            "claim_type": "collision",
            "description": "bumper",
            "estimated_amount": 1800.0,
        },
    )
    assert out["claim_id"] == "CLM-5003" and out["request_tracking"]["status"] == "submitted"


def test_quotes_are_prepare_level_and_not_recorded(db):
    out = InsuranceBackend(db, "CUST-003", TODAY).execute(
        "get_quote",
        {
            "product_code": "RENTERS",
            "coverage_level": "standard",
            "deductible": 500,
            "insured_item": {"property_address": "201 River St", "personal_property_value": "30000"},
        },
    )
    assert out["quote_id"] == "QTE-7001" and "request_tracking" not in out
    assert _q(db, "SELECT COUNT(*) FROM transactions") == [(0,)]


def test_failed_validation_records_no_transaction(db):
    with pytest.raises(InvalidRequest):
        InsuranceBackend(db, "CUST-001", TODAY).execute(
            "make_payment", {"policy_id": "POL-1001", "amount": 10.0, "payment_method": "card_on_file"}
        )
    assert _q(db, "SELECT COUNT(*) FROM transactions") == [(0,)]


# ---------------------------------------------------------------- review policy
def test_review_policy_holds_cancellation_without_changing_anything(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    out = be.execute(
        "cancel_policy",
        ok("cancel_policy", {"policy_id": "POL-1002", "reason": "sold the house", "effective_date": None}),
    )
    assert out["status"] == "pending_review" and out["applied"] is False
    assert out["preview"]["effective_date"] == TODAY.isoformat()
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("active",)]
    assert be.list_transactions("pending_review")[0]["id"] == out["transaction_id"]


def test_review_preview_has_no_ids_that_do_not_exist_yet(db):
    be = InsuranceBackend(db, "CUST-003", TODAY, "review")
    q = be.execute(
        "get_quote",
        {
            "product_code": "RENTERS",
            "coverage_level": "standard",
            "deductible": 500,
            "insured_item": {"property_address": "201 River St", "personal_property_value": "30000"},
        },
    )
    out = be.execute("purchase_policy", ok("purchase_policy", {"quote_id": q["quote_id"], "start_date": None}))
    assert out["status"] == "pending_review"
    assert "policy_id" not in out["preview"] and "id" not in out["preview"]
    assert out["preview"]["quote_id"] == q["quote_id"] and out["preview"]["monthly_premium"] > 0
    assert _q(db, "SELECT COUNT(*) FROM policies WHERE customer_id='CUST-003'") == [(0,)]
    assert _q(db, "SELECT status FROM quotes WHERE id=?", q["quote_id"]) == [("open",)]


def test_review_still_enforces_business_rules_up_front(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    with pytest.raises(InvalidRequest, match="at least 16"):
        be.execute(
            "add_driver",
            {"policy_id": "POL-1001", "full_name": "Kid", "date_of_birth": "2012-01-01", "relationship": "child"},
        )
    assert _q(db, "SELECT COUNT(*) FROM transactions") == [(0,)]


def test_variant_rule_address_commits_but_date_of_birth_goes_to_review(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    assert be.policy.effective("submit_profile_change", {"change_type": "mailing_address"}) == "commit"
    assert be.policy.effective("submit_profile_change", {"change_type": "date_of_birth"}) == "submit"
    assert be.policy.effective("make_payment") == "commit"


def test_approve_applies_the_request(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    out = be.execute("update_policy_coverage", {"policy_id": "POL-1001", "deductible": 1000, "coverage_level": None})
    done = be.review_transaction(out["transaction_id"], "approve", "J. Rivera", "ok")
    assert done["status"] == "committed" and done["decided_by"] == "J. Rivera" and done["reference"] == "POL-1001"
    assert _q(db, "SELECT deductible FROM policies WHERE id='POL-1001'") == [(1000,)]


def test_reject_changes_nothing(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    out = be.execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None})
    )
    done = be.review_transaction(out["transaction_id"], "reject", "J. Rivera", "needs a call")
    assert done["status"] == "rejected" and done["decision_note"] == "needs a call"
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1001'") == [("active",)]
    with pytest.raises(InvalidRequest, match="not pending review"):
        be.review_transaction(out["transaction_id"], "approve", "J. Rivera")


def test_approve_fails_when_rules_changed_since(db):
    be = verified(InsuranceBackend(db, "CUST-001", TODAY, "review"))
    first = be.execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "a", "effective_date": None})
    )
    second = be.execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "b", "effective_date": None})
    )
    be.review_transaction(first["transaction_id"], "approve", "J. Rivera")
    out = be.review_transaction(second["transaction_id"], "approve", "J. Rivera")
    assert out["status"] == "failed" and "already cancelled" in out["decision_note"]


def test_not_permitted_below_submit(db, monkeypatch):
    monkeypatch.setitem(POLICIES, "locked", {"cancel_policy": "read"})
    be = InsuranceBackend(db, "CUST-001", TODAY, "locked")
    with pytest.raises(NotPermitted, match="representative"):
        be.execute(
            "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None})
        )


def test_unknown_policy_is_rejected():
    with pytest.raises(ValueError, match="Unknown transaction policy"):
        TransactionPolicy("lenient")


# ---------------------------------------------------------------- access and duplicates
def test_transactions_are_customer_scoped(db):
    InsuranceBackend(db, "CUST-001", TODAY).execute(
        "make_payment", {"policy_id": "POL-1002", "amount": 5.0, "payment_method": "card_on_file"}
    )
    other = InsuranceBackend(db, "CUST-002", TODAY)
    with pytest.raises(AccessDenied):
        other.get_transaction("TXN-10001")
    with pytest.raises(AccessDenied):
        other.review_transaction("TXN-10001", "approve", "x")
    assert other.list_transactions() == []


def test_duplicate_payment_is_blocked_unless_confirmed(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    args = {"policy_id": "POL-1002", "amount": 20.0, "payment_method": "card_on_file"}
    be.execute("make_payment", args)
    with pytest.raises(DuplicateRequest, match="TXN-10001, PAY-9001"):
        be.execute("make_payment", args)
    assert _q(db, "SELECT balance_due FROM policies WHERE id='POL-1002'") == [(173.6,)]
    out = be.execute("make_payment", args, repeat_of="TXN-10001")
    assert out["payment_id"] == "PAY-9002"
    # a different amount is not a duplicate
    be.execute("make_payment", {**args, "amount": 21.0})


def test_add_remove_add_rider_is_not_a_duplicate(db):
    be = InsuranceBackend(db, "CUST-001", TODAY)
    be.execute("add_rider", {"policy_id": "POL-1002", "rider_code": "FLOOD"})
    be.execute("remove_rider", {"policy_id": "POL-1002", "rider_code": "FLOOD"})
    be.execute("add_rider", {"policy_id": "POL-1002", "rider_code": "FLOOD"})
    assert [t["operation"] for t in be.list_transactions()] == ["add_rider", "remove_rider", "add_rider"]


# ---------------------------------------------------------------- MCP metadata and tools
async def _tools(db, customer="CUST-001", policy=None):
    async with Client(build_server(InsuranceBackend(db, customer, TODAY, policy))) as client:
        tb = McpToolbox(client)
        await tb.load()
        return tb


async def test_meta_level_follows_the_policy(db):
    direct = (await _tools(db)).tools
    assert direct["cancel_policy"].level == "commit" and direct["get_quote"].level == "prepare"
    assert direct["file_claim"].level == "submit" and direct["list_policies"].level == "read"
    review = (await _tools(db, policy="review")).tools
    assert review["cancel_policy"].level == "submit" and review["make_payment"].level == "commit"
    spec = review["submit_profile_change"]
    assert spec.effective_level({"change_type": "date_of_birth"}) == "submit"
    assert spec.effective_level({"change_type": "mailing_address"}) == "commit"


async def test_status_tools_over_mcp(db):
    async with Client(build_server(verified(InsuranceBackend(db, "CUST-001", TODAY, "review")))) as client:
        tb = McpToolbox(client)
        await tb.load()
        out = await tb.call("cancel_policy", ok("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"}))
        assert not out.is_error and json.loads(out.text)["status"] == "pending_review"
        status = json.loads((await tb.call("get_transaction_status", {"transaction_id": "TXN-10001"})).text)
        assert status["status"] == "pending_review" and "Nothing has changed" in status["status_meaning"]
        listed = json.loads((await tb.call("list_transactions", {"status": "pending_review"})).text)
        assert [t["id"] for t in listed["transactions"]] == ["TXN-10001"]
        assert set(tb.tools_for_intents(["request_status"])) == {"get_transaction_status", "list_transactions"}


# ---------------------------------------------------------------- engine: the approval prompt says what will happen
async def test_approval_prompt_carries_the_level(settings, db):
    settings.txn_policy = "review"
    llm = ScriptedLLM(
        structured=[intent("cancel_policy"), plan("cancel_policy", "respond")],
        executor=[
            tools(("verify_identity", {"date_of_birth": "1985-04-12", "postal_code": "62701"})),
            tools(("cancel_policy", {"policy_id": "POL-1002", "reason": "sold"})),
            text("Your cancellation request TXN-10001 is waiting for review."),
        ],
    )
    asked = []
    async with open_session(
        customer_id="CUST-001", confirm=lambda r: asked.append(r) or True, settings=settings, llm=llm
    ) as session:
        trace = await session.send("cancel my home policy")
    assert asked[0].level == "submit" and "review" in asked[0].effect
    assert trace.tool_calls[1].level == "submit" and trace.tool_calls[1].executed
    assert _q(db, "SELECT status FROM policies WHERE id='POL-1002'") == [("active",)]
    assert _q(db, "SELECT status FROM transactions") == [("pending_review",)]


# ---------------------------------------------------------------- staff review command
def test_review_cli(db, capsys):
    verified(InsuranceBackend(db, "CUST-001", TODAY, "review")).execute(
        "cancel_policy", ok("cancel_policy", {"policy_id": "POL-1001", "reason": "x", "effective_date": None})
    )
    assert review_main(["--db", str(db), "list"]) == 0
    assert "TXN-10001" in capsys.readouterr().out
    assert review_main(["--db", str(db), "approve", "TXN-10001", "--by", "J. Rivera"]) == 0
    assert '"status": "committed"' in capsys.readouterr().out
    assert review_main(["--db", str(db), "reject", "TXN-10001", "--by", "J. Rivera"]) == 1
