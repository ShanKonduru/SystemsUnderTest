"""Rule-based stand-ins for the model, one per role, following the written instructions deterministically.

``DESK_LLM_PROVIDER=SCRIPTED`` with ``LLM_SCRIPT=desk.policy:factory``: offline demos, CI, a known-good baseline.
One object serves all roles; it tells them apart by the system prompt it is given.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from desk.agents import BILLING_SYSTEM, CLAIMS_SYSTEM, TRIAGE_SYSTEM

POLICY = re.compile(r"POL-\d+")
CLAIM = re.compile(r"CLM-\d+")


def route_for(text: str) -> str:
    low = text.lower()
    claim = "claim" in low or bool(CLAIM.search(text))
    bill = any(w in low for w in ("bill", "owe", "balance", "due", "paid", "payments"))
    if claim and bill:
        return "both"
    if claim:
        return "claims"
    return "billing" if bill else "unsupported"


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") for b in content)


class DeskRules:
    provider = "SCRIPTED"
    model = "desk-rules"

    def __init__(self) -> None:
        self._n = 0

    async def structured(self, *, system: str, user: str, schema: Any, effort: str = "medium") -> Any:
        route = route_for(user)
        return schema(route=route, reason=f"keywords in the request point to {route}")

    async def create(self, *, system: str, messages: list[dict[str, Any]], **_: Any) -> LLMResponse:
        task = _text(messages[0]["content"]).split("Task:\n", 1)[1].rsplit("\n\nMember:", 1)[0]
        done: dict[str, tuple[bool, str, dict[str, Any]]] = {}
        names: dict[str, tuple[str, dict[str, Any]]] = {}
        for msg in messages[1:]:
            for b in msg["content"] if isinstance(msg["content"], list) else []:
                kind = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                if kind == "tool_use":
                    names[b.id] = (b.name, dict(b.input))
                elif kind == "tool_result":
                    name, args = names[b["tool_use_id"]]
                    done[name] = (not b.get("is_error"), str(b["content"]), args)
        if system == TRIAGE_SYSTEM:
            return self._triage(task, done)
        if system == BILLING_SYSTEM:
            return self._billing(task, done)
        assert system == CLAIMS_SYSTEM, "unknown role"
        return self._claims(task, done)

    def _call(self, name: str, **args: Any) -> LLMResponse:
        self._n += 1
        return LLMResponse([ToolUseBlock(id=f"rule_{self._n}", name=name, input=args)], "tool_use", Usage())

    @staticmethod
    def _say(text: str) -> LLMResponse:
        return LLMResponse([TextBlock(text)], "end_turn", Usage())

    def _triage(self, task: str, done: dict[str, Any]) -> LLMResponse:
        route = task.split("Route: ", 1)[1].split("\n", 1)[0] if "Route: " in task else route_for(task)
        target = "claims" if route == "claims" else "billing"
        tool = f"handoff_to_{target}"
        if route == "unsupported":
            return self._say("Sorry, the member desk can't help with that request.")
        if tool not in done:
            return self._call(tool, task=task.split("\nRoute: ", 1)[0])
        ok, content, _ = done[tool]
        reply = json.loads(content)
        if not ok:
            return self._say("Sorry, I couldn't get that information right now. " + reply.get("output", ""))
        return self._say(reply["output"])

    def _billing(self, task: str, done: dict[str, Any]) -> LLMResponse:
        pol = POLICY.search(task)
        if pol is None:
            return self._say("Please tell me which policy (e.g. POL-1002) the question is about.")
        if "get_billing" not in done:
            return self._call("get_billing", policy_id=pol.group())
        ok, text, _ = done["get_billing"]
        if not ok:
            if "open_case" not in done:
                return self._call(
                    "open_case",
                    subject=f"Billing lookup failed for {pol.group()}",
                    details=f"The member asked: {task}. The billing system reported: {text}",
                )
            case = json.loads(done["open_case"][1]).get("case_id", "a case") if done["open_case"][0] else "no case"
            return self._say(
                f"Billing information for {pol.group()} is unavailable right now ({text}); "
                f"opened {case} for a follow-up."
            )
        bill = json.loads(text)
        facts = (
            f"Policy {bill['policy_id']}: ${bill['balance_due']:,.2f} due on {bill['due_date']} "
            f"(deductible ${bill['deductible']:,.2f})."
        )
        claim = CLAIM.search(task)
        if claim and "handoff_to_claims" not in done:
            return self._call("handoff_to_claims", task=f"Explain claim {claim.group()} amounts.")
        if claim:
            ok2, content, _ = done["handoff_to_claims"]
            other = json.loads(content).get("output", "")
            return self._say(f"{facts} {other if ok2 else 'Claim details are unavailable right now.'}")
        return self._say(facts)

    def _claims(self, task: str, done: dict[str, Any]) -> LLMResponse:
        claim = CLAIM.search(task)
        tool = "get_claim" if claim else "list_claims"
        if tool not in done:
            return self._call(tool, **({"claim_id": claim.group()} if claim else {}))
        ok, text, _ = done[tool]
        if not ok:
            return self._say(f"Claim information is unavailable right now ({text}).")
        data = json.loads(text)
        if claim:
            return self._say(f"Claim {data['id']} is {data['status']}: {data['note']}")
        return self._say("Your claims: " + ", ".join(f"{c['id']} ({c['status']})" for c in data["claims"]) + ".")


def factory(env: Mapping[str, str]) -> DeskRules:
    return DeskRules()
