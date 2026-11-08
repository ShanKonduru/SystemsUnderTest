"""Permission narrowing on handoffs, robustness to empty tool results, and the planted defects (buggy build)."""

from __future__ import annotations

import runpy
import sys

import pytest

from desk import defects
from desk.agents import DeskAgent
from desk.config import Settings
from desk.policy import DeskRules, factory

BOTH = "Why was my claim CLM-5003 payment lower than my bill for POL-1002?\nRoute: both"


async def run(settings, wiring, task, member="M-1001", role="triage"):
    return await DeskAgent(role, wiring["llm"], settings, wiring["connect"], wiring["handoff"]).run(
        task, wiring["token_for"](member)
    )


def buggy(settings, wiring, *names):
    """Switch the planted defects on for both the agents (settings) and the scripted model."""
    settings.defects = frozenset(names)
    wiring["llm"].defects = frozenset(names)


async def test_handoffs_narrow_permissions_to_the_receiving_role(settings, wiring):
    out = await run(settings, wiring, BOTH)
    to_billing, to_claims = out["hops"]
    assert to_billing["permissions"] == ["billing:read", "cases:write", "claims:read"]
    assert to_claims["permissions"] == ["cases:write", "claims:read"]  # billing:read dropped for claims
    no_claims = await run(settings, wiring, "What happened with claim CLM-5003?\nRoute: claims", member="M-3001")
    assert no_claims["hops"][0]["permissions"] == [] and no_claims["steps"][1]["access_denied"]


async def test_empty_tool_results_are_reported_as_unavailable(settings, wiring, faults):
    faults.set({"get_billing": {"mode": "empty"}, "get_claim": {"mode": "empty"}})
    bill = await run(settings, wiring, "How much do I owe on POL-1002?\nRoute: billing")
    assert "returned no data" in bill["output"] and "CASE-1" in bill["output"]
    claim = await run(settings, wiring, "What happened with claim CLM-5003?\nRoute: claims")
    assert "unavailable" in claim["output"] and "CLM-5003 is" not in claim["output"]


def test_defect_names_are_checked(monkeypatch):
    assert defects.parse("") == frozenset() and defects.parse("wrong_member, no_narrowing") == {
        "wrong_member",
        "no_narrowing",
    }
    with pytest.raises(ValueError, match="unknown DESK_DEFECTS"):
        defects.parse("typo")
    monkeypatch.setenv("DESK_DEFECTS", "handoff_order")
    assert Settings().defects == {"handoff_order"}
    assert factory({"DESK_DEFECTS": "no_containment"}).defects == {"no_containment"}
    assert DeskRules().defects == frozenset()


async def test_handoff_order_defect_asks_claims_first(settings, wiring):
    buggy(settings, wiring, "handoff_order")
    out = await run(settings, wiring, BOTH)
    assert [(h["from"], h["to"]) for h in out["hops"]] == [
        ("triage", "claims"),
        ("triage", "billing"),
        ("billing", "claims"),
    ]
    assert "$193.60" in out["output"]


async def test_no_containment_defect_hands_on_after_a_failure(settings, wiring, faults):
    buggy(settings, wiring, "no_containment")
    faults.set({"get_billing": {"mode": "error"}})
    out = await run(settings, wiring, BOTH)
    assert [(h["from"], h["to"]) for h in out["hops"]] == [("triage", "billing"), ("billing", "claims")]
    assert "open_case" not in [s["name"] for s in out["steps"]]


async def test_wrong_member_defect_delegates_for_someone_else(settings, wiring):
    buggy(settings, wiring, "wrong_member")
    out = await run(settings, wiring, BOTH)
    assert out["hops"][1]["member"] == "M-2201"
    assert out["steps"][-1]["access_denied"]  # CLM-5003 is not M-2201's claim
    other = await run(
        settings, wiring, "Why was my claim CLM-7001 lower than my bill for POL-2001?\nRoute: both", member="M-2201"
    )
    assert other["hops"][1]["member"] == "M-1001"


async def test_no_narrowing_and_trace_omits_hop_defects(settings, wiring):
    buggy(settings, wiring, "no_narrowing", "trace_omits_hop")
    out = await run(settings, wiring, BOTH)
    (hop,) = out["hops"]  # billing's hop to claims is left out of triage's account
    assert hop["to"] == "billing" and [s["name"] for s in out["steps"]] == [
        "handoff_to_billing",
        "get_billing",
        "get_claim",
    ]
    billing = await run(settings, wiring, "Explain my bill for POL-1002 and claim CLM-5003", role="billing")
    assert billing["hops"][0]["permissions"] == ["billing:read", "cases:write", "claims:read"]  # not narrowed


def test_settings_load_without_python_dotenv(monkeypatch):
    from desk import config

    monkeypatch.setitem(sys.modules, "dotenv", None)
    namespace = runpy.run_path(config.__file__, run_name="config_without_dotenv")
    assert namespace["PROJECT_ROOT"] == config.PROJECT_ROOT
