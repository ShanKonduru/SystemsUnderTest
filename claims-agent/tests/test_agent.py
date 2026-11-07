"""The agent end to end over the real MCP tool server (in-process), driven by the rule-based policy."""

from __future__ import annotations

import sqlite3
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from claims_agent.agent import ClaimsAgent, inprocess_connector
from claims_agent.policy import RulesLLM, factory


def status(settings, claim):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute("SELECT status FROM claims WHERE id=?", (claim,)).fetchone()[0]
    finally:
        con.close()


async def run(settings, event: dict[str, Any], principal="adjuster-07", llm: Any = None):
    return await ClaimsAgent(llm or RulesLLM(), settings, inprocess_connector(settings)).handle(event, principal)


async def test_document_received_moves_an_open_claim_to_review_and_writes_a_letter(settings):
    ep = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5003", "document_id": "DOC-78"})
    assert ep.route == "claims_intake" and ep.done and ep.outcome == "finished"
    assert [s["name"] for s in ep.steps] == ["get_claim", "update_claim", "write_letter"]
    assert all(s["ok"] for s in ep.steps) and ep.steps[0]["read_only"] and not ep.steps[1]["read_only"]
    assert status(settings, "CLM-5003") == "under_review"
    assert "DOC-78" in (settings.letters_dir / "CLM-5003-acknowledgement.txt").read_text(encoding="utf-8")
    assert "under review" in ep.output and ep.llm == {"provider": "SCRIPTED", "model": "claims-rules"}


async def test_document_for_a_claim_already_in_review_is_only_noted(settings):
    ep = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5004", "document_id": "DOC-80"})
    assert [s["name"] for s in ep.steps] == ["get_claim", "add_note"] and status(settings, "CLM-5004") == "under_review"
    assert "no status change" in ep.output


async def test_settlement_approves_with_a_letter_and_never_pays(settings):
    ep = await run(
        settings, {"type": "claim.assessment_completed", "claim_id": "CLM-5004", "decision": "approve", "amount": 1500}
    )
    assert ep.route == "claims_settlement"
    assert [s["name"] for s in ep.steps] == ["get_claim", "update_claim", "write_letter"]
    assert status(settings, "CLM-5004") == "approved" and "make_payment" not in [s["name"] for s in ep.steps]
    assert "$1,500.00" in (settings.letters_dir / "CLM-5004-approval.txt").read_text(encoding="utf-8")


async def test_settlement_denies_and_reports_a_refused_transition(settings):
    deny = await run(settings, {"type": "claim.assessment_completed", "claim_id": "CLM-5004", "decision": "deny"})
    assert status(settings, "CLM-5004") == "denied" and "denial letter" in deny.output
    lapsed = await run(
        settings, {"type": "claim.assessment_completed", "claim_id": "CLM-5006", "decision": "approve", "amount": 100}
    )
    assert not lapsed.steps[1]["ok"] and "cannot move to denied" in lapsed.steps[1]["result"]
    assert "not settled" in lapsed.output and status(settings, "CLM-5006") == "open"


async def test_intake_reports_a_failed_update(settings):
    con = sqlite3.connect(settings.db_path)
    con.execute("UPDATE claims SET status='open' WHERE id='CLM-5006'")
    con.execute("UPDATE principals SET permissions='claims:read,letters:write' WHERE id='adjuster-07'")
    con.commit()
    con.close()
    ep = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5006"})
    assert ep.steps[1]["access_denied"] and "not updated" in ep.output


async def test_payment_is_refused_for_an_adjuster_and_made_by_a_supervisor(settings):
    denied = await run(settings, {"type": "claim.payment_due", "claim_id": "CLM-5001"})
    assert [s["name"] for s in denied.steps] == ["get_claim", "make_payment", "add_note"]
    assert denied.steps[1]["access_denied"] and "needs a supervisor" in denied.output
    assert status(settings, "CLM-5001") == "approved"
    paid = await run(settings, {"type": "claim.payment_due", "claim_id": "CLM-5001"}, principal="supervisor-02")
    assert paid.steps[1]["ok"] and status(settings, "CLM-5001") == "paid" and "$2,400.00" in paid.output


async def test_unsupported_events_and_unknown_claims(settings):
    ep = await run(settings, {"type": "policy.renewed", "policy_id": "POL-1001"})
    assert ep.route == "unsupported" and ep.done and ep.steps == [] and ep.output.startswith("No action taken")
    missing = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-0000"})
    assert not missing.steps[0]["ok"] and "could not read claim" in missing.output


class LoopingLLM(RulesLLM):
    """Never finishes: keeps reading the same claim."""

    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse(
            [ToolUseBlock(id="x", name="get_claim", input={"claim_id": "CLM-5003"})], "tool_use", Usage(10, 2)
        )


class TruncatedLLM(RulesLLM):
    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse([TextBlock("Claim CLM-50")], "max_tokens", Usage(1, 1))


class BrokenLLM(RulesLLM):
    async def structured(self, **_: Any) -> Any:
        raise RuntimeError("model endpoint returned 503")


async def test_step_limit_truncation_errors_and_usage(settings):
    settings.max_steps = 3
    loop = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5003"}, llm=LoopingLLM())
    assert loop.outcome == "max_steps" and not loop.done and len(loop.steps) == 3
    assert loop.usage == {"input_tokens": 30, "output_tokens": 6}
    cut = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5003"}, llm=TruncatedLLM())
    assert not cut.done and cut.output == "Claim CLM-50"
    broken = await run(settings, {"type": "claim.document_received", "claim_id": "CLM-5003"}, llm=BrokenLLM())
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

    ep = await ClaimsAgent(RulesLLM(), settings, failing).handle(
        {"type": "claim.document_received", "claim_id": "CLM-5003"}, "adjuster-07"
    )
    assert ep.error == "ConnectionError: Invalid Host header"
