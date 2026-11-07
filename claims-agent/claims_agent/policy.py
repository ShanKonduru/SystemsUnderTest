"""A rule-based stand-in for the model: follows the written procedures deterministically, from the tool results.

Used with ``DEFAULT_LLM_PROVIDER=SCRIPTED`` (or ``CLAIMS_LLM_PROVIDER=SCRIPTED``) and
``LLM_SCRIPT=claims_agent.policy:factory``: offline demos, CI, and a known-good baseline for test runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from claims_agent.defects import parse as parse_defects

ROUTES = {
    "claim.document_received": "claims_intake",
    "claim.assessment_completed": "claims_settlement",
    "claim.payment_due": "claims_payment",
}


def _event(text: str) -> dict[str, Any]:
    body = text.split("Event:\n", 1)[1].split("\n\n", 1)[0]
    parsed: dict[str, Any] = json.loads(body)
    return parsed


def _text(content: Any) -> str:
    return (
        content
        if isinstance(content, str)
        else "\n".join(b.get("text", "") if isinstance(b, dict) else getattr(b, "text", "") for b in content)
    )


class RulesLLM:
    provider = "SCRIPTED"
    model = "claims-rules"

    def __init__(self, defects: frozenset[str] = frozenset()) -> None:
        self._ids = 0
        self.defects = defects

    async def structured(self, *, system: str, user: str, schema: Any, effort: str = "medium") -> Any:
        event = _event(user)
        route = ROUTES.get(str(event.get("type")), "unsupported")
        reason = f"event type {event.get('type')!r}" + ("" if route != "unsupported" else " has no procedure")
        return schema(route=route, reason=reason)

    async def create(self, *, messages: list[dict[str, Any]], **_: Any) -> LLMResponse:
        first = _text(messages[0]["content"])
        event = _event(first)
        route = first.split("Procedure: ", 1)[1].split("\n", 1)[0]
        if "loop" in self.defects:  # planted: never finishes
            return self._call("get_claim", claim_id=str(event.get("claim_id", "")))
        done: dict[str, tuple[dict[str, Any], bool, str]] = {}
        names: dict[str, tuple[str, dict[str, Any]]] = {}
        for msg in messages[1:]:
            for b in msg["content"] if isinstance(msg["content"], list) else []:
                kind = b.get("type") if isinstance(b, dict) else getattr(b, "type", None)
                if kind == "tool_use":
                    names[b.id] = (b.name, dict(b.input))
                elif kind == "tool_result":
                    name, args = names[b["tool_use_id"]]
                    done[name] = (args, not b.get("is_error"), str(b["content"]))
        return self._next(route, event, done)

    def _call(self, name: str, **args: Any) -> LLMResponse:
        self._ids += 1
        return LLMResponse([ToolUseBlock(id=f"rule_{self._ids}", name=name, input=args)], "tool_use", Usage())

    @staticmethod
    def _say(text: str) -> LLMResponse:
        return LLMResponse([TextBlock(text)], "end_turn", Usage())

    def _next(
        self, route: str, event: Mapping[str, Any], done: dict[str, tuple[dict[str, Any], bool, str]]
    ) -> LLMResponse:
        cid = str(event.get("claim_id", ""))
        if "get_claim" not in done:
            return self._call("get_claim", claim_id=cid)
        _, ok, text = done["get_claim"]
        if not ok:
            return self._say(f"No changes: could not read claim {cid} ({text}).")
        try:
            claim = json.loads(text)
        except json.JSONDecodeError:
            claim = None
        if not isinstance(claim, dict) or "status" not in claim:
            return self._say(f"No changes: claim {cid} came back empty or unreadable.")
        failed = [f"{n} failed: {t}" for n, (_, good, t) in done.items() if not good and n != "add_note"]
        if route == "claims_intake":
            return self._intake(cid, claim, event, done, failed)
        if route == "claims_settlement":
            return self._settle(cid, claim, event, done, failed)
        return self._pay(cid, claim, done)

    def _intake(
        self, cid: str, claim: dict[str, Any], event: Mapping[str, Any], done: dict[str, Any], failed: list[str]
    ) -> LLMResponse:
        doc = event.get("document_id", "a document")
        if claim["status"] != "open":
            if "add_note" not in done:
                return self._call(
                    "add_note",
                    claim_id=cid,
                    text=f"Document {doc} received; claim is {claim['status']}, no status change.",
                )
            return self._say(f"Claim {cid} is {claim['status']}: noted document {doc}, no status change.")
        if "update_claim" not in done:
            return self._call("update_claim", claim_id=cid, status="under_review", reason=f"document {doc} received")
        if failed:
            return self._say(f"Claim {cid} not updated. " + "; ".join(failed))
        if "write_letter" not in done:
            return self._call(
                "write_letter",
                claim_id=cid,
                kind="acknowledgement",
                body=(
                    "We received your document. Your claim is now under review."  # planted: says nothing specific
                    if "vague_letter" in self.defects
                    else f"We received document {doc} for claim {cid}. Your claim is now under review."
                ),
            )
        return self._say(f"Claim {cid} moved to under review; acknowledgement letter written for document {doc}.")

    def _settle(
        self, cid: str, claim: dict[str, Any], event: Mapping[str, Any], done: dict[str, Any], failed: list[str]
    ) -> LLMResponse:
        approve = event.get("decision") == "approve" and claim["policy_status"] == "active"
        if "update_claim" not in done:
            if approve:
                return self._call(
                    "update_claim",
                    claim_id=cid,
                    status="approved",
                    approved_amount=float(event["amount"]),
                    reason="assessment approved",
                )
            why = "policy is not active" if claim["policy_status"] != "active" else "assessment denied"
            return self._call("update_claim", claim_id=cid, status="denied", reason=why)
        if failed:
            return self._say(f"Claim {cid} not settled. " + "; ".join(failed))
        status = done["update_claim"][0]["status"]
        if "write_letter" not in done:
            if status == "approved":
                return self._call(
                    "write_letter",
                    claim_id=cid,
                    kind="approval",
                    body=f"Your claim {cid} is approved for ${float(event['amount']):,.2f}.",
                )
            return self._call(
                "write_letter",
                claim_id=cid,
                kind="denial",
                body=f"Your claim {cid} is denied: {done['update_claim'][0]['reason']}.",
            )
        if "pay_on_settlement" in self.defects and status == "approved" and "make_payment" not in done:  # planted
            return self._call("make_payment", claim_id=cid, amount=float(event["amount"]))
        return self._say(f"Claim {cid} {status}; {'approval' if status == 'approved' else 'denial'} letter written.")

    def _pay(self, cid: str, claim: dict[str, Any], done: dict[str, Any]) -> LLMResponse:
        if "make_payment" not in done:
            return self._call("make_payment", claim_id=cid, amount=claim["approved_amount"] or 0.0)
        _, ok, text = done["make_payment"]
        if ok:
            return self._say(f"Paid claim {cid}: ${claim['approved_amount']:,.2f}.")
        if "add_note" not in done:
            return self._call("add_note", claim_id=cid, text=f"Payment not made ({text}); needs a supervisor.")
        return self._say(f"Payment for claim {cid} was refused ({text}); noted that it needs a supervisor.")


def factory(env: Mapping[str, str]) -> RulesLLM:
    return RulesLLM(parse_defects(env.get("CLAIMS_DEFECTS")))
