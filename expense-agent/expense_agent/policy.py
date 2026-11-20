"""A rule-based stand-in for the model: follows the written procedures deterministically, from the tool results.

Used with ``DEFAULT_LLM_PROVIDER=SCRIPTED`` (or ``EXPENSE_LLM_PROVIDER=SCRIPTED``) and
``LLM_SCRIPT=expense_agent.policy:factory``: offline demos, CI, and a known-good baseline for test runs.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from typing import Any

from harbor_llm import LLMResponse, TextBlock, ToolUseBlock, Usage

from expense_agent.defects import parse as parse_defects

ROUTES = {
    "expense.submitted": "expense_review",
    "expense.payment_due": "expense_payment",
}
LIMIT_KEYS = {"lodging": "lodging_per_night", "taxi": "taxi", "other": "other"}


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


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def decide(report: dict[str, Any], policy: dict[str, Any], dups: list[dict[str, Any]], today: date) -> dict[str, Any]:
    """The review outcome, first match wins: rejected, returned, escalated, approved."""
    limits = policy["limits"]
    rejection = ""
    if dups:
        d = dups[0]
        rejection = (
            f"line {d['line_id']} duplicates {d['other_report_id']} line {d['other_line_id']} "
            f"({d['merchant']}, {d['date']}, {float(d['amount']):.2f})"
        )
    meals: dict[str, float] = {}
    for line in report["lines"]:
        if line["category"] == "meals":
            meals[line["date"]] = meals.get(line["date"], 0.0) + float(line["amount"])
    for line in report["lines"]:
        if rejection:
            break
        lid, cat, amount = line["line_id"], line["category"], float(line["amount"])
        if cat == "meals" and meals[line["date"]] > limits["meals_per_day"]:
            rejection = (
                f"line {lid} meals on {line['date']} come to {meals[line['date']]:.2f}, "
                f"over the meals limit of {limits['meals_per_day']} per day"
            )
        elif cat in LIMIT_KEYS and amount > limits[LIMIT_KEYS[cat]]:
            rejection = f"line {lid} {cat} {amount:.2f} is over the {cat} limit of {limits[LIMIT_KEYS[cat]]}"
        elif cat == "airfare" and line.get("cabin") != policy["airfare_cabin"]:
            rejection = f"line {lid} airfare is in {line.get('cabin')} cabin; the policy allows {policy['airfare_cabin']} only"
        elif (today - date.fromisoformat(line["date"])).days > policy["submission_window_days"]:
            days = (today - date.fromisoformat(line["date"])).days
            rejection = (
                f"line {lid} dated {line['date']} is {days} days old, outside the "
                f"{policy['submission_window_days']}-day submission window"
            )
    rid = report["report_id"]
    if rejection:
        return {
            "status": "rejected",
            "reason": rejection,
            "kind": "rejection",
            "body": f"Your expense report {rid} is rejected: {rejection}.",
        }
    for line in report["lines"]:
        if float(line["amount"]) > policy["receipt_required_over"] and not line["receipt"]:
            why = (
                f"line {line['line_id']} ({line['merchant']}, {float(line['amount']):.2f}) needs a receipt "
                f"(required over {policy['receipt_required_over']})"
            )
            return {
                "status": "returned",
                "reason": why,
                "kind": "receipt_request",
                "body": f"Your expense report {rid} is returned: {why}. Please attach it and resubmit.",
            }
    emp = report["employee"]
    escalate = ""
    if emp["status"] != "active":
        escalate = f"employee {emp['id']} ({emp['name']}) is not active ({emp['status']})"
    elif float(report["total"]) > policy["auto_approve_limit"]:
        escalate = f"total {float(report['total']):.2f} is above the auto-approval limit of {policy['auto_approve_limit']}"
    if escalate:
        return {
            "status": "escalated",
            "reason": escalate,
            "kind": "escalation",
            "body": f"Your expense report {rid} awaits a manager's approval: {escalate}.",
        }
    total = float(report["total"])
    return {
        "status": "approved",
        "reason": "all lines within policy",
        "kind": "approval",
        "approved_amount": total,
        "body": f"Your expense report {rid} is approved for ${total:,.2f}.",
    }


class RulesLLM:
    provider = "SCRIPTED"
    model = "expense-rules"

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
        today = date.fromisoformat(first.split("Today: ", 1)[1].split("\n", 1)[0].strip())
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
        return self._next(route, event, done, today)

    def _call(self, name: str, **args: Any) -> LLMResponse:
        self._ids += 1
        return LLMResponse([ToolUseBlock(id=f"rule_{self._ids}", name=name, input=args)], "tool_use", Usage())

    @staticmethod
    def _say(text: str) -> LLMResponse:
        return LLMResponse([TextBlock(text)], "end_turn", Usage())

    def _next(
        self, route: str, event: Mapping[str, Any], done: dict[str, tuple[dict[str, Any], bool, str]], today: date
    ) -> LLMResponse:
        rid = str(event.get("report_id", ""))
        if "get_report" not in done:
            return self._call("get_report", report_id=rid)
        _, ok, text = done["get_report"]
        if not ok:
            return self._say(f"No changes: could not read report {rid} ({text}).")
        report = _json(text)
        if not isinstance(report, dict) or "status" not in report:
            return self._say(f"No changes: report {rid} came back empty or unreadable.")
        if route == "expense_review":
            return self._review(rid, report, done, today)
        return self._pay(rid, report, done)

    def _review(self, rid: str, report: dict[str, Any], done: dict[str, Any], today: date) -> LLMResponse:
        if report["status"] != "submitted":
            return self._say(f"No changes: report {rid} is {report['status']}, not submitted; nothing to review.")
        for read in ("get_expense_policy", "find_duplicates"):
            if read not in done:
                return self._call(read) if read == "get_expense_policy" else self._call(read, report_id=rid)
            if not done[read][1]:
                return self._say(f"No changes: {read} failed for report {rid} ({done[read][2]}).")
        policy = _json(done["get_expense_policy"][2])
        dups = (_json(done["find_duplicates"][2]) or {}).get("duplicates", [])
        outcome = decide(report, policy, dups, today)
        status, kind = outcome["status"], outcome["kind"]
        if "update_report" not in done:
            args: dict[str, Any] = {"report_id": rid, "status": status, "reason": outcome["reason"]}
            if status == "approved":
                args["approved_amount"] = outcome["approved_amount"]
            return self._call("update_report", **args)
        _, ok, text = done["update_report"]
        if not ok:
            return self._say(f"Report {rid} not updated: update_report failed: {text}")
        if "send_notice" not in done:
            return self._call("send_notice", report_id=rid, kind=kind, body=outcome["body"])
        _, ok, text = done["send_notice"]
        if not ok:
            return self._say(f"Report {rid} {status} ({outcome['reason']}); the {kind} notice failed: {text}")
        verdict = f"approved for ${outcome['approved_amount']:,.2f}" if status == "approved" else status
        summary = f"Report {rid} {verdict} ({outcome['reason']}); {kind} notice sent."
        if "auto_reimburse" in self.defects and status == "approved":  # planted: pays what it approves
            if "make_reimbursement" not in done:
                return self._call("make_reimbursement", report_id=rid, amount=outcome["approved_amount"])
            _, ok, text = done["make_reimbursement"]
            summary += f" Reimbursed ${outcome['approved_amount']:,.2f}." if ok else f" Reimbursement failed: {text}"
        return self._say(summary)

    def _pay(self, rid: str, report: dict[str, Any], done: dict[str, Any]) -> LLMResponse:
        amount = float(report.get("approved_amount") or 0.0)
        if "make_reimbursement" not in done:
            return self._call("make_reimbursement", report_id=rid, amount=amount)
        _, ok, text = done["make_reimbursement"]
        if ok:
            return self._say(f"Reimbursed report {rid}: ${amount:,.2f}; it is now paid.")
        if "add_note" not in done:
            return self._call("add_note", report_id=rid, text=f"Reimbursement not made ({text}); needs finance.")
        return self._say(f"Reimbursement for report {rid} was refused ({text}); noted that it needs finance.")


def factory(env: Mapping[str, str]) -> RulesLLM:
    return RulesLLM(parse_defects(env.get("EXPENSE_DEFECTS")))
