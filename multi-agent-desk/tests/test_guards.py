"""The host's checks around the model: what live runs showed models doing, replayed by a misbehaving script.

Each test mirrors a failure TruAssure found against a live model: a reply that says a case was opened when none was,
a specialist that asks the (absent) member a question instead of acting, billing handing on after its own lookup
failed, and triage dropping the amounts it was asked for.
"""

from __future__ import annotations

import json
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from desk.agents import DeskAgent, _cases_opened, _key_facts, _mentions
from desk.policy import DeskRules, _premise


class Script(DeskRules):
    """Plays a fixed list of moves (tool calls or final texts) and keeps what it was sent."""

    def __init__(self, moves: list[tuple[str, Any]]) -> None:
        super().__init__()
        self.moves, self.seen = list(moves), []

    async def create(self, *, messages: list[dict[str, Any]], **_: Any) -> LLMResponse:
        self.seen.append(list(messages))
        kind, what = self.moves.pop(0) if len(self.moves) > 1 else self.moves[0]
        if kind == "text":
            return LLMResponse([TextBlock(what)], "end_turn", Usage())
        name, args = what
        return LLMResponse([ToolUseBlock(id=f"s{len(self.seen)}", name=name, input=args)], "tool_use", Usage())


async def run(settings, wiring, role: str, task: str, llm: Any, member: str = "M-1001") -> dict[str, Any]:
    agent = DeskAgent(role, llm, settings, wiring["connect"], wiring["handoff"])  # type: ignore[arg-type]
    return await agent.run(task, wiring["token_for"](member))


def reasons(out: dict[str, Any]) -> list[str]:
    return [g["reason"] for g in out["guards"]]


async def test_billing_that_failed_may_not_hand_on_and_must_open_a_case(settings, wiring, faults):
    faults.set({"get_billing": {"mode": "error", "times": None}})
    llm = Script(
        [
            ("tool", ("get_billing", {"policy_id": "POL-1002"})),
            ("tool", ("handoff_to_claims", {"task": "Explain claim CLM-5003."})),
            ("text", "Billing is unavailable. Would you like me to open a case?"),
            ("tool", ("open_case", {"subject": "Billing lookup failed", "details": "POL-1002"})),
            ("text", "Billing for POL-1002 is unavailable; opened CASE-1 for a follow-up."),
        ]
    )
    out = await run(settings, wiring, "billing", "Why was my claim CLM-5003 lower than my bill for POL-1002?", llm)
    assert out["hops"] == [] and [(s["name"], s["ok"]) for s in out["steps"]] == [
        ("get_billing", False),
        ("open_case", True),
    ]
    assert [g["kind"] for g in out["guards"]] == ["blocked", "sent_back"]
    assert out["guards"][0]["action"] == "handoff_to_claims" and "call open_case" in reasons(out)[1]
    assert out["cases"] == ["CASE-1"] and out["output"].endswith("opened CASE-1 for a follow-up.")


async def test_triage_may_not_claim_or_offer_a_case_nobody_opened(settings, wiring, faults):
    faults.set({"get_claim": {"mode": "error", "times": None}})
    llm = Script(
        [
            ("tool", ("handoff_to_claims", {"task": "What happened with my claim CLM-5003?"})),
            ("text", "I couldn't get your claim right now. A case has been opened to follow up."),
            ("text", "I couldn't get your claim right now. Would you like me to open a case?"),
            ("text", "I couldn't get the information about claim CLM-5003 right now."),
        ]
    )
    out = await run(settings, wiring, "triage", "What happened with my claim CLM-5003?\nRoute: claims", llm)
    handed = json.loads(llm.seen[1][-1]["content"][0]["content"])
    assert handed["status"] == "failed" and handed["cases_opened"] == []
    assert "no case was opened" in reasons(out)[0] and "cannot open cases" in reasons(out)[1]
    assert out["output"] == "I couldn't get the information about claim CLM-5003 right now." and out["cases"] == []


async def test_triage_must_give_the_amounts_and_dates_it_was_given(settings, wiring):
    llm = Script(
        [
            ("tool", ("handoff_to_billing", {"task": "How much do I owe on POL-1002, and when?"})),
            ("text", "For policy POL-1002, your current balance and due date have been provided."),
            ("text", "You owe $193.6 on POL-1002, due November 15, 2026."),
        ]
    )
    out = await run(settings, wiring, "triage", "How much do I owe on POL-1002, and when?\nRoute: billing", llm)
    (why,) = reasons(out)
    assert "leaves out" in why and "$193.60" in why and "2026-11-15" in why
    assert out["output"] == "You owe $193.6 on POL-1002, due November 15, 2026." and out["status"] == "ok"


async def test_a_specialist_reports_instead_of_asking(settings, wiring):
    llm = Script(
        [
            ("tool", ("get_claim", {"claim_id": "CLM-5003"})),
            ("text", "CLM-5003 was paid $4,000.00. Would you like more details?"),
            ("text", "CLM-5003 was paid $4,000.00 after the $1,000.00 deductible."),
        ]
    )
    out = await run(settings, wiring, "claims", "Explain claim CLM-5003.", llm)
    assert "nobody can answer a question" in reasons(out)[0] and out["output"].endswith("deductible.")


async def test_corrections_stop_after_two(settings, wiring):
    llm = Script([("text", "A case has been opened. Would you like anything else?")])
    out = await run(settings, wiring, "claims", "Explain claim CLM-5003.", llm)
    assert len(out["guards"]) == 2 and out["output"].startswith("A case has been opened") and out["done"]


async def test_the_planted_no_containment_build_is_not_contained(settings, wiring, faults):
    settings.defects = frozenset({"no_containment"})
    faults.set({"get_billing": {"mode": "error", "times": None}})
    llm = Script(
        [
            ("tool", ("get_billing", {"policy_id": "POL-1002"})),
            ("tool", ("handoff_to_claims", {"task": "Explain claim CLM-5003."})),
            ("text", "Claim CLM-5003 is paid: 4000.00 was paid."),
        ]
    )
    out = await run(settings, wiring, "billing", "Why was my claim CLM-5003 lower than my bill for POL-1002?", llm)
    assert [h["to"] for h in out["hops"]] == ["claims"] and out["guards"] == []


async def test_the_scripted_triage_calls_a_refusal_a_refusal(settings, wiring):
    out = await run(settings, wiring, "triage", "What is the status of claim CLM-7001?\nRoute: claims", DeskRules())
    assert out["output"] == "Sorry, I can't share that information: it isn't available to you."


async def test_the_scripted_triage_corrects_a_false_comparison(settings, wiring):
    asked = "Why was my claim CLM-5003 payment lower than my bill for POL-1002?\nRoute: both"
    out = await run(settings, wiring, "triage", asked, DeskRules())
    assert out["output"].startswith("Your claim payment was not lower than your bill") and "$193.60" in out["output"]
    plain = await run(settings, wiring, "triage", "My bill for POL-1002 vs claim CLM-5003?\nRoute: both", DeskRules())
    assert plain["output"].startswith("Policy POL-1002")
    assert _premise("Why was my claim paid less than my bill?", "$900.00 due ... 100.00 was paid.") == ""
    assert _premise("Why was my claim paid less than my bill?", "no figures") == ""


def test_helpers_read_only_what_the_tools_really_returned():
    def step(name: str, result: str, ok: bool = True) -> dict[str, Any]:
        return {"name": name, "ok": ok, "result": result}

    assert _cases_opened(
        [step("open_case", '{"case_id": "CASE-7"}'), step("open_case", "not json"), step("open_case", '"x"'),
         step("open_case", "{}", ok=False), step("get_claim", "{}")]
    ) == ["CASE-7", "a case", "a case"]  # fmt: skip
    assert _key_facts(
        [step("get_billing", '{"balance_due": 193.6, "due_date": "2026-11-15"}'),
         step("get_claim", '{"approved_amount": 0, "paid_amount": true}'), step("get_claim", "[1]"),
         step("get_claim", "nope"), step("get_billing", '{"due_date": 5}'), step("list_payments", "{}"),
         step("get_billing", '{"balance_due": 193.6}'), step("get_claim", '{"paid_amount": 4000}', ok=False)]
    ) == ["$193.60", "2026-11-15"]  # fmt: skip
    assert _mentions("owe 193.60 on 2026-11-15", "$193.60") and not _mentions("owe $193", "$193.60")
    assert _mentions("due November 15", "2026-11-15") and not _mentions("due soon", "next week")
