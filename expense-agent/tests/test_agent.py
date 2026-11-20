"""The agent end to end over the real MCP tool server (in-process), driven by the rule-based policy."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from expense_agent.agent import ExpenseAgent, inprocess_connector
from expense_agent.policy import RulesLLM, factory

REVIEW_STEPS = ["get_report", "get_expense_policy", "find_duplicates", "update_report", "send_notice"]


def query(settings, sql, *args):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def status(settings, rid):
    return query(settings, "SELECT status FROM reports WHERE id=?", rid)[0][0]


def notices(settings, rid):
    return [k for (k,) in query(settings, "SELECT kind FROM notices WHERE report_id=? ORDER BY id", rid)]


async def run(settings, event: dict[str, Any], principal="expense-bot", llm: Any = None):
    return await ExpenseAgent(llm or RulesLLM(), settings, inprocess_connector(settings)).handle(event, principal)


@pytest.mark.parametrize(
    ("rid", "expected", "kind", "why"),
    [
        ("ER-3001", "approved", "approval", "$342.50"),
        ("ER-3002", "rejected", "rejection", "meals limit of 75"),
        ("ER-3003", "escalated", "escalation", "auto-approval limit"),
        ("ER-3004", "returned", "receipt_request", "line L1"),
        ("ER-3005", "escalated", "escalation", "EMP-103 (Chen Li) is not active"),
        ("ER-3006", "rejected", "rejection", "duplicates ER-3001 line L1"),
        ("ER-3008", "rejected", "rejection", "business cabin"),
        ("ER-3009", "rejected", "rejection", "75 days old"),
    ],
)
async def test_every_submitted_seed_report_reaches_its_outcome(settings, rid, expected, kind, why):
    ep = await run(settings, {"type": "expense.submitted", "report_id": rid})
    assert ep.route == "expense_review" and ep.done and ep.outcome == "finished"
    assert [s["name"] for s in ep.steps] == REVIEW_STEPS and all(s["ok"] for s in ep.steps)
    assert [s["read_only"] for s in ep.steps] == [True, True, True, False, False]
    assert status(settings, rid) == expected and notices(settings, rid) == [kind]
    (path,) = query(settings, "SELECT path FROM notices WHERE report_id=?", rid)[0]
    assert why in (settings.out_dir / "notices" / path).read_text(encoding="utf-8")
    assert f"Report {rid} {expected}" in ep.output and ep.llm == {"provider": "SCRIPTED", "model": "expense-rules"}
    if expected == "approved":
        assert query(settings, "SELECT approved_amount FROM reports WHERE id=?", rid) == [(342.5,)]
    assert query(settings, "SELECT COUNT(*) FROM reimbursements") == [(0,)]


@pytest.mark.parametrize("rid", ["ER-3007", "ER-3010"])
async def test_a_report_that_is_not_submitted_is_not_reviewed(settings, rid):
    before = status(settings, rid)
    ep = await run(settings, {"type": "expense.submitted", "report_id": rid})
    assert [s["name"] for s in ep.steps] == ["get_report"] and "not submitted" in ep.output
    assert status(settings, rid) == before and notices(settings, rid) == []


async def test_payment_is_refused_for_the_bot_and_made_by_finance(settings):
    due = {"type": "expense.payment_due", "report_id": "ER-3007"}
    denied = await run(settings, due)
    assert denied.route == "expense_payment"
    assert [s["name"] for s in denied.steps] == ["get_report", "make_reimbursement", "add_note"]
    assert denied.steps[1]["access_denied"] and "needs finance" in denied.output and status(settings, "ER-3007") == "approved"
    assert query(settings, "SELECT text FROM report_notes WHERE report_id='ER-3007'")[0][0].endswith("needs finance.")
    paid = await run(settings, due, principal="finance-01")
    assert [s["name"] for s in paid.steps] == ["get_report", "make_reimbursement"] and paid.steps[1]["ok"]
    assert status(settings, "ER-3007") == "paid" and "$310.00" in paid.output
    assert query(settings, "SELECT id, report_id, amount FROM reimbursements") == [("RMB-8001", "ER-3007", 310.0)]


async def test_an_already_paid_report_is_never_paid_again(settings):
    ep = await run(settings, {"type": "expense.payment_due", "report_id": "ER-3010"}, principal="finance-01")
    assert [s["name"] for s in ep.steps] == ["get_report", "make_reimbursement", "add_note"]
    assert not ep.steps[1]["ok"] and not ep.steps[1]["access_denied"] and "only approved" in ep.steps[1]["result"]
    assert query(settings, "SELECT COUNT(*) FROM reimbursements") == [(0,)] and status(settings, "ER-3010") == "paid"


async def test_unsupported_events_and_unknown_reports(settings):
    ep = await run(settings, {"type": "travel.booked", "trip_id": "T-1"})
    assert ep.route == "unsupported" and ep.done and ep.steps == [] and ep.output.startswith("No action taken")
    missing = await run(settings, {"type": "expense.submitted", "report_id": "ER-0000"})
    assert not missing.steps[0]["ok"] and "could not read report" in missing.output


async def test_a_refused_update_sends_no_notice(settings):
    ep = await run(settings, {"type": "expense.submitted", "report_id": "ER-3001"}, principal="auditor-03")
    assert [s["name"] for s in ep.steps] == REVIEW_STEPS[:4] and ep.steps[3]["access_denied"]
    assert "not updated" in ep.output and status(settings, "ER-3001") == "submitted" and notices(settings, "ER-3001") == []


async def test_a_refused_notice_is_reported(settings):
    con = sqlite3.connect(settings.db_path)
    con.execute("UPDATE principals SET permissions='expenses:read,expenses:update' WHERE id='expense-bot'")
    con.commit()
    con.close()
    ep = await run(settings, {"type": "expense.submitted", "report_id": "ER-3002"})
    assert ep.steps[-1]["access_denied"] and "rejection notice failed" in ep.output
    assert status(settings, "ER-3002") == "rejected"


class LoopingLLM(RulesLLM):
    """Never finishes: keeps reading the same report."""

    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse(
            [ToolUseBlock(id="x", name="get_report", input={"report_id": "ER-3001"})], "tool_use", Usage(10, 2)
        )


class TruncatedLLM(RulesLLM):
    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse([TextBlock("Report ER-30")], "max_tokens", Usage(1, 1))


class BrokenLLM(RulesLLM):
    async def structured(self, **_: Any) -> Any:
        raise RuntimeError("model endpoint returned 503")


async def test_step_limit_truncation_errors_and_usage(settings):
    settings.max_steps = 3
    event = {"type": "expense.submitted", "report_id": "ER-3001"}
    loop = await run(settings, event, llm=LoopingLLM())
    assert loop.outcome == "max_steps" and not loop.done and len(loop.steps) == 3
    assert loop.usage == {"input_tokens": 30, "output_tokens": 6}
    cut = await run(settings, event, llm=TruncatedLLM())
    assert not cut.done and cut.output == "Report ER-30"
    broken = await run(settings, event, llm=BrokenLLM())
    assert broken.outcome == "error" and "503" in (broken.error or "") and not broken.done
    assert broken.ended >= broken.started and broken.to_dict()["episode_id"] == broken.episode_id


def test_policy_factory():
    assert isinstance(factory({}), RulesLLM)


async def test_connection_failures_name_the_real_cause(settings):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def failing(principal):
        raise ExceptionGroup("unhandled errors in a TaskGroup", [ConnectionError("Invalid Host header")])
        yield

    ep = await ExpenseAgent(RulesLLM(), settings, failing).handle(
        {"type": "expense.submitted", "report_id": "ER-3001"}, "expense-bot"
    )
    assert ep.error == "ConnectionError: Invalid Host header"


FIRST = {
    "role": "user",
    "content": 'Event:\n{"report_id": "ER-3001", "type": "x"}\n\nProcedure: expense_review\nToday: 2026-11-03',
}


def _turns(*calls: tuple[str, dict[str, Any], str, bool]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = [FIRST]
    for i, (name, args, text, ok) in enumerate(calls):
        msgs.append({"role": "assistant", "content": [ToolUseBlock(id=f"t{i}", name=name, input=args)]})
        msgs.append(
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": f"t{i}", "content": text, "is_error": not ok}]}
        )
    return msgs


async def test_an_empty_or_unreadable_report_changes_nothing():
    """A report lookup that answers with nothing usable (e.g. an injected empty result) ends cleanly, unchanged."""
    llm = RulesLLM()
    for text in ("{}", "", "not json", "[1, 2]"):
        reply = await llm.create(messages=_turns(("get_report", {"report_id": "ER-3001"}, text, True)))
        assert reply.stop_reason == "end_turn" and "empty or unreadable" in reply.content[0].text


async def test_a_failed_policy_or_duplicate_read_changes_nothing():
    llm = RulesLLM()
    report = '{"report_id": "ER-3001", "status": "submitted"}'
    reply = await llm.create(
        messages=_turns(
            ("get_report", {"report_id": "ER-3001"}, report, True),
            ("get_expense_policy", {}, "{}", True),
            ("find_duplicates", {"report_id": "ER-3001"}, "timeout", False),
        )
    )
    assert reply.content[0].text == "No changes: find_duplicates failed for report ER-3001 (timeout)."


def test_decide_rejects_a_line_over_its_category_limit():
    from datetime import date

    from expense_agent.backend import POLICY
    from expense_agent.policy import decide

    report = {
        "report_id": "ER-1",
        "total": 300.0,
        "employee": {"id": "EMP-101", "name": "Asha Rao", "status": "active"},
        "lines": [{"line_id": "L1", "date": "2026-11-01", "category": "lodging", "merchant": "Inn", "amount": 300.0, "receipt": True}],
    }
    outcome = decide(report, POLICY, [], date(2026, 11, 3))
    assert outcome["status"] == "rejected" and outcome["reason"] == "line L1 lodging 300.00 is over the lodging limit of 250"
