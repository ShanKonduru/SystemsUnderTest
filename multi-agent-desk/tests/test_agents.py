"""The three agents together over the real MCP server (in-process): routing, handoffs, identity chain, faults."""

from __future__ import annotations

import sqlite3
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from desk.agents import DeskAgent
from desk.policy import DeskRules, factory, route_for


async def run(settings, wiring, role: str, task: str, member: str = "M-1001", llm: Any = None):
    agent = DeskAgent(role, llm or wiring["llm"], settings, wiring["connect"], wiring["handoff"])  # type: ignore[arg-type]
    return await agent.run(task, wiring["token_for"](member))


def audit(settings):
    con = sqlite3.connect(settings.db_path)
    try:
        return con.execute("SELECT agent, chain, operation, outcome FROM audit_log ORDER BY id").fetchall()
    finally:
        con.close()


async def test_billing_question_goes_triage_to_billing_with_the_identity(settings, wiring):
    out = await run(settings, wiring, "triage", "How much do I owe on POL-1002?\nRoute: billing")
    assert out["status"] == "ok" and out["done"] and "$193.60 due on 2026-11-15" in out["output"]
    assert [(s["agent"], s["name"]) for s in out["steps"]] == [
        ("triage", "handoff_to_billing"),
        ("billing", "get_billing"),
    ]
    (hop,) = out["hops"]
    assert hop["from"] == "triage" and hop["to"] == "billing" and hop["member"] == "M-1001"
    assert hop["chain"] == ["triage", "billing"] and hop["status"] == "ok"
    assert out["steps"][1]["chain"] == ["triage", "billing"] and out["steps"][1]["read_only"]
    assert audit(settings) == [("billing", '["triage", "billing"]', "get_billing", "ok")]


async def test_a_question_needing_both_goes_triage_billing_claims(settings, wiring):
    out = await run(
        settings, wiring, "triage", "Why was my claim CLM-5003 payment lower than my bill for POL-1002?\nRoute: both"
    )
    assert [(h["from"], h["to"]) for h in out["hops"]] == [("triage", "billing"), ("billing", "claims")]
    assert out["hops"][1]["chain"] == ["triage", "billing", "claims"]
    assert "deductible was subtracted" in out["output"] and "$193.60" in out["output"]
    assert [a[:3] for a in audit(settings)] == [
        ("billing", '["triage", "billing"]', "get_billing"),
        ("claims", '["triage", "billing", "claims"]', "get_claim"),
    ]


async def test_claims_only_and_list_claims(settings, wiring):
    out = await run(settings, wiring, "triage", "What happened with claim CLM-7001?\nRoute: claims", member="M-2201")
    assert out["hops"][0]["to"] == "claims" and "excluded" in out["output"]
    listed = await run(settings, wiring, "claims", "Which claims do I have?", member="M-2201")
    assert listed["output"] == "Your claims: CLM-7001 (denied)."


async def test_cascading_failure_is_contained_and_never_reported_as_success(settings, wiring, faults):
    faults.set({"get_billing": {"mode": "error", "times": None}})
    out = await run(
        settings, wiring, "triage", "Why was my claim CLM-5003 payment lower than my bill for POL-1002?\nRoute: both"
    )
    names = [(s["agent"], s["name"], s["ok"]) for s in out["steps"]]
    assert names == [
        ("triage", "handoff_to_billing", False),
        ("billing", "get_billing", False),
        ("billing", "open_case", True),
    ]
    assert [h["to"] for h in out["hops"]] == ["billing"]  # billing did not hand off to claims
    assert out["status"] == "failed" and out["hops"][0]["status"] == "failed"
    assert "couldn't" in out["output"] and "193.60" not in out["output"] and "CASE-1" in out["output"]


async def test_timeouts_empty_results_and_one_off_faults(settings, wiring, faults):
    faults.set({"get_claim": {"mode": "timeout", "times": 1}})
    first = await run(settings, wiring, "claims", "Explain claim CLM-5003.")
    second = await run(settings, wiring, "claims", "Explain claim CLM-5003.")
    assert "timed out" in first["output"] and first["status"] == "failed"
    assert second["status"] == "ok" and "4000.00 was paid" in second["output"]
    faults.set({"list_payments": {"mode": "empty"}})
    assert faults.take("list_payments") == "empty" and faults.take("other") is None
    faults.set({"x": {"mode": "error", "times": 0}})
    assert faults.take("x") is None


async def test_someone_elses_policy_is_refused_and_a_case_opened(settings, wiring):
    out = await run(settings, wiring, "billing", "What do I owe on POL-1002?", member="M-2201")
    assert out["steps"][0]["access_denied"] and out["steps"][1]["name"] == "open_case"
    assert out["status"] == "failed"


async def test_member_without_claims_permission_gets_no_case(settings, wiring):
    out = await run(settings, wiring, "billing", "Balance on POL-1002?", member="M-3001")
    assert out["steps"][0]["access_denied"] and not out["steps"][1]["ok"]  # no cases:write either
    assert "no case" in out["output"]


async def test_unsupported_and_missing_policy(settings, wiring):
    assert (await run(settings, wiring, "triage", "Change my address\nRoute: unsupported"))["steps"] == []
    assert "which policy" in (await run(settings, wiring, "billing", "How much do I owe?"))["output"]
    assert (await run(settings, wiring, "triage", "How much do I owe on POL-1002?"))["hops"][0]["to"] == "billing"


class Rogue(DeskRules):
    """Calls a tool outside its role, and hands off to an agent it may not reach."""

    def __init__(self, calls: list[tuple[str, dict[str, Any]]]) -> None:
        super().__init__()
        self.calls = calls

    async def create(self, **_: Any) -> LLMResponse:
        if self.calls:
            name, args = self.calls.pop(0)
            return LLMResponse([ToolUseBlock(id=name, name=name, input=args)], "tool_use", Usage(3, 1))
        return LLMResponse([TextBlock("done")], "end_turn", Usage(1, 1))


async def test_tools_and_handoffs_outside_the_role_are_refused(settings, wiring):
    out = await run(
        settings,
        wiring,
        "claims",
        "x",
        llm=Rogue([("get_billing", {"policy_id": "POL-1002"}), ("handoff_to_billing", {"task": "x"})]),
    )
    assert [(s["name"], s["ok"]) for s in out["steps"]] == [("get_billing", False), ("handoff_to_billing", False)]
    assert "not available to the claims agent" in out["steps"][0]["result"]
    assert out["usage"] == {"input_tokens": 7, "output_tokens": 3} and out["status"] == "failed"
    triage = await run(settings, wiring, "triage", "x", llm=Rogue([("get_claim", {"claim_id": "CLM-5003"})]))
    assert triage["steps"][0]["result"] == "tool get_claim is not available to the triage agent"


class Endless(DeskRules):
    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse([ToolUseBlock(id="e", name="list_claims", input={})], "tool_use", Usage())


class Cut(DeskRules):
    async def create(self, **_: Any) -> LLMResponse:
        return LLMResponse([TextBlock("Your bal")], "max_tokens", Usage())


async def test_step_limit_and_truncation_fail_the_run(settings, wiring):
    settings.max_steps = 2
    loop = await run(settings, wiring, "claims", "x", llm=Endless())
    assert not loop["done"] and loop["status"] == "failed" and len(loop["steps"]) == 2
    cut = await run(settings, wiring, "claims", "x", llm=Cut())
    assert cut["output"] == "Your bal" and cut["status"] == "failed"


async def test_structured_only_tool_results(settings):
    from desk.agents import McpTools

    class Res:
        def __init__(self) -> None:
            self.content: list[Any] = []
            self.structured_content = {"ok": 1}
            self.is_error = False

    class FakeClient:
        async def call_tool(self, name: str, args: dict[str, Any]) -> Any:
            return Res()

    assert (await McpTools(FakeClient()).call("x", {})).result == '{"ok": 1}'


def test_routing_keywords_and_factory():
    assert route_for("How much do I owe?") == "billing"
    assert route_for("Status of CLM-5003") == "claims"
    assert route_for("Why is my claim payment lower than my bill?") == "both"
    assert route_for("Change my address") == "unsupported"
    assert isinstance(factory({}), DeskRules)
