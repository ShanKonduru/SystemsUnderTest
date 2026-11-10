"""The desk's agents: triage, billing and claims. One loop, three roles.

Each role has its own instructions, its own subset of the MCP tools, and the agents it may hand off to:

    triage  --handoff--> billing --handoff--> claims
            --handoff--> claims

A handoff re-signs the identity for the receiving agent (chain + 1, permissions never widen) and calls that agent
over HTTP. Every agent calls tools with the identity it received, so the tool server sees, verifies and audits
who acts for whom. A run returns the agent's account: output, status, every step (with the identity used) and
every hop, including those of the agents it handed off to.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, nullcontext
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from desk import identity as idmod
from desk.config import Settings

Role = Literal["triage", "billing", "claims"]


class Triage(BaseModel):
    route: Literal["billing", "claims", "both", "unsupported"] = Field(description="Who should handle the request")
    reason: str = Field(description="One sentence: why")


TRIAGE_ROUTE_SYSTEM = """You triage member requests at Harbor Mutual's member desk. Route:
- billing: balances, due dates, payments made
- claims: claim status or amounts
- both: the request needs billing and claims information (e.g. comparing a claim payment with a bill)
- unsupported: anything else (payments, policy changes, unrelated questions)"""

TRIAGE_SYSTEM = """You are the triage agent of Harbor Mutual's member desk. You do not look anything up yourself:
hand the request to the specialist that can answer it, then answer the member from what the specialists report.
- billing questions: handoff_to_billing. Claim questions: handoff_to_claims. A question that needs both: hand it
  to billing (billing consults claims itself).
- Give the member every amount and date the specialists reported that answers the question (for example the
  balance due and its due date, the approved and paid amounts of a claim). Never say "has been provided" instead.
- If the question rests on a wrong comparison or assumption, say so plainly and give the correct facts.
- A specialist's result lists "cases_opened". Say a case was opened only if that list is not empty, and name the
  case. Never say a case was or will be opened otherwise, and never offer to open one: you cannot.
- If a specialist reports status "failed", tell the member you could not get that information right now. If it was
  refused because the record is not the member's or the member may not see it, say you cannot share it; do not
  call that a temporary problem. Never state amounts, dates or outcomes that no specialist reported.
Answer briefly and plainly."""

BILLING_SYSTEM = """You are the billing specialist of Harbor Mutual's member desk, acting for one member. You report to
another agent, not to the member: nobody can answer a question from you, so never ask one or offer options.
Use get_billing / list_payments for the policy in the task. If the task also asks about a claim, after you have the
billing facts hand the claim part to the claims specialist (handoff_to_claims) and combine both answers.
If a billing tool fails: do not guess, do not hand off, and do not explain the claim. Call open_case for a human to
follow up, then report that the billing information is unavailable and the case id open_case returned.
Report only facts the tools returned, with every amount and date they returned."""

CLAIMS_SYSTEM = """You are the claims specialist of Harbor Mutual's member desk, acting for one member. You report to
another agent, not to the member: nobody can answer a question from you, so never ask one or offer options.
Use get_claim (or list_claims if no claim id is given) and explain the claim's status and amounts, including the
adjuster's note. If a claims tool fails, do not guess: report that the claim information is unavailable, and do not
say a case was opened. Report only facts the tools returned, with every amount they returned."""

# What the host checks before an agent may finish (each check sends the model back with the reason).
_CASE_CLAIM = re.compile(
    r"\b(case|ticket)\b[^.?!]{0,40}\b(has been|have been|was|were|is being|will be|gets)\s+"
    r"(opened|created|raised|logged)\b|\bopened (a|an|your|the) (new |service |support )?(case|ticket)\b"
    r"|\bCASE-\d+\b",
    re.IGNORECASE,
)
_OFFER_TO_OPEN = re.compile(
    r"\b(I can|I could|I'll|I will|shall I|should I|would you like me to|want me to|happy to)\b[^.?!]{0,40}\bopen\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"\b(would you like|do you want|shall I|should I|let me know if you|if you (need|want|would like))\b",
    re.IGNORECASE,
)
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)  # fmt: skip
_BILLING_READS = ("get_billing", "list_payments")
MAX_CORRECTIONS = 2


@dataclass(frozen=True)
class RoleSpec:
    system: str
    tools: tuple[str, ...]
    handoffs: tuple[str, ...]


# What each role may do: a handoff narrows the identity to the receiving role's permissions (least privilege).
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "billing": frozenset({"billing:read", "claims:read", "cases:write"}),  # claims:read only to consult claims
    "claims": frozenset({"claims:read", "cases:write"}),
}

ROLES: dict[str, RoleSpec] = {
    "triage": RoleSpec(TRIAGE_SYSTEM, (), ("billing", "claims")),
    "billing": RoleSpec(BILLING_SYSTEM, ("get_billing", "list_payments", "open_case"), ("claims",)),
    "claims": RoleSpec(CLAIMS_SYSTEM, ("get_claim", "list_claims", "open_case"), ()),
}


@dataclass
class ToolCall:
    name: str
    ok: bool
    result: str
    access_denied: bool = False


class McpTools:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.meta: dict[str, dict[str, Any]] = {}

    async def list_tools(self, allowed: tuple[str, ...]) -> list[dict[str, Any]]:
        out = []
        for t in sorted((await self.client.list_tools()).tools, key=lambda t: t.name):
            if t.name not in allowed:
                continue
            d = t.model_dump(by_alias=True, exclude_none=True, mode="json")
            self.meta[t.name] = d.get("_meta") or {}
            out.append(
                {
                    "name": t.name,
                    "description": (d.get("description") or "").strip(),
                    "input_schema": d.get("inputSchema") or {"type": "object", "properties": {}},
                }
            )
        return out

    async def call(self, name: str, args: dict[str, Any]) -> ToolCall:
        result = await self.client.call_tool(name, args)
        parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        if not parts and result.structured_content is not None:
            parts = [json.dumps(result.structured_content)]
        text = "\n".join(parts)
        prefix = f"Error executing tool {name}: "
        text = text[len(prefix) :] if text.startswith(prefix) else text
        bad = bool(result.is_error)
        return ToolCall(name, not bad, text, bad and "ACCESS_DENIED" in text)


Connect = Callable[[str], AbstractAsyncContextManager[McpTools]]
Handoff = Callable[[str, str, str], Awaitable[dict[str, Any]]]


def http_tools(settings: Settings) -> Connect:
    @asynccontextmanager
    async def connect(token: str) -> AsyncIterator[McpTools]:
        from mcp import Client
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        async with (
            create_mcp_http_client(headers={"X-Identity": token}) as http,
            Client(streamable_http_client(f"{settings.tools_url}/mcp", http_client=http)) as client,
        ):
            yield McpTools(client)

    return connect


def http_handoff(settings: Settings) -> Handoff:
    async def handoff(target: str, task: str, token: str) -> dict[str, Any]:
        import httpx

        url = {"billing": settings.billing_url, "claims": settings.claims_url}[target]
        async with httpx.AsyncClient(timeout=300) as http:
            r = await http.post(f"{url}/tasks", json={"task": task}, headers={"X-Identity": token})
        if r.status_code != 200:
            return {
                "agent": target,
                "status": "failed",
                "output": f"{target} agent returned HTTP {r.status_code}: {r.text[:200]}",
                "steps": [],
                "hops": [],
            }
        data: dict[str, Any] = r.json()
        return data

    return handoff


def _handoff_spec(target: str) -> dict[str, Any]:
    return {
        "name": f"handoff_to_{target}",
        "description": f"Hand a task to the {target} specialist and get its answer back.",
        "input_schema": {
            "type": "object",
            "properties": {"task": {"type": "string", "description": "What to do"}},
            "required": ["task"],
            "additionalProperties": False,
        },
    }


class DeskAgent:
    def __init__(self, role: Role, llm: Any, settings: Settings, connect: Connect, handoff: Handoff) -> None:
        self.role, self.spec = role, ROLES[role]
        self.llm, self.settings, self.connect, self.handoff = llm, settings, connect, handoff

    async def run(self, task: str, token: str) -> dict[str, Any]:
        key = self.settings.signing_key()
        who = idmod.verify(token, key)
        out: dict[str, Any] = {
            "agent": self.role,
            "status": "ok",
            "output": "",
            "done": False,
            "steps": [],
            "hops": [],
            "cases": [],
            "guards": [],
            "usage": {"input_tokens": 0, "output_tokens": 0},
        }
        corrections = 0
        ctx = self.connect(token) if self.spec.tools else nullcontext(None)
        async with ctx as tools:
            specs = (await tools.list_tools(self.spec.tools)) if tools else []
            specs += [_handoff_spec(t) for t in self.spec.handoffs]
            messages: list[dict[str, Any]] = [{"role": "user", "content": f"Task:\n{task}\n\nMember: {who['sub']}"}]
            for _ in range(self.settings.max_steps):
                resp = await self.llm.create(
                    system=self.spec.system,
                    tools=specs,
                    messages=messages,
                    max_tokens=3000,
                    output_config={"effort": "low"},
                )
                u = getattr(resp, "usage", None)
                out["usage"]["input_tokens"] += int(getattr(u, "input_tokens", 0) or 0)
                out["usage"]["output_tokens"] += int(getattr(u, "output_tokens", 0) or 0)
                messages.append({"role": "assistant", "content": resp.content})
                uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
                if not uses:
                    text = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
                    problem = self._check_final(text, out) if corrections < MAX_CORRECTIONS else None
                    if problem:
                        corrections += 1
                        out["guards"].append({"agent": self.role, "kind": "sent_back", "reason": problem})
                        messages.append({"role": "user", "content": f"Not finished: {problem}"})
                        continue
                    out["output"] = text
                    out["done"] = resp.stop_reason != "max_tokens"
                    break
                results = [await self._use(b, tools, token, key, who, out) for b in uses]
                messages.append({"role": "user", "content": results})
        if self.role == "triage" and "trace_omits_hop" in self.settings.defects:  # planted defect
            out["hops"] = [h for h in out["hops"] if h["from"] == "triage"]
            out["steps"] = [s for s in out["steps"] if s["kind"] != "handoff" or s["agent"] == "triage"]
        out["cases"] = _cases_opened(out["steps"])
        if any(not s["ok"] for s in out["steps"] if s["agent"] == self.role):
            out["status"] = "failed"
        if not out["done"]:
            out["status"] = "failed"
        return out

    async def _use(
        self, block: Any, tools: McpTools | None, token: str, key: str, who: dict[str, Any], out: dict[str, Any]
    ) -> dict[str, Any]:
        args = dict(block.input)
        target = block.name.removeprefix("handoff_to_")
        if block.name.startswith("handoff_to_") and target in self.spec.handoffs and self._contained(out):
            reason = "a billing tool failed, so the claim part may not be handed on; call open_case and report"
            out["guards"].append({"agent": self.role, "kind": "blocked", "action": block.name, "reason": reason})
            return {"type": "tool_result", "tool_use_id": block.id, "content": f"Refused: {reason}.", "is_error": True}
        if block.name.startswith("handoff_to_") and target in self.spec.handoffs:
            defects = self.settings.defects
            narrow = None if "no_narrowing" in defects else ROLE_PERMISSIONS.get(target)
            delegated = idmod.delegate(token, key, target, narrow_to=narrow)
            if "wrong_member" in defects and self.role == "billing":  # planted: delegates for someone else
                claims = idmod.verify(delegated, key)
                other = "M-2201" if claims["sub"] != "M-2201" else "M-1001"
                delegated = idmod.sign({**claims, "sub": other}, key)
            res = await self.handoff(target, str(args.get("task", "")), delegated)
            hop = idmod.verify(delegated, key)
            out["hops"].append(
                {
                    "from": self.role,
                    "to": target,
                    "task": args.get("task", ""),
                    "member": hop["sub"],
                    "chain": hop["chain"],
                    "permissions": hop["permissions"],
                    "status": res.get("status"),
                }
            )
            out["steps"].append(
                {
                    "agent": self.role,
                    "kind": "handoff",
                    "name": block.name,
                    "args": args,
                    "ok": res.get("status") == "ok",
                    "result": str(res.get("output", ""))[:2000],
                    "member": who["sub"],
                    "chain": who["chain"],
                }
            )
            out["steps"].extend(res.get("steps", []))
            out["hops"].extend(res.get("hops", []))
            content = json.dumps(
                {
                    "status": res.get("status"),
                    "output": res.get("output", ""),
                    "cases_opened": _cases_opened(res.get("steps", [])),
                }
            )
            return {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
                "is_error": res.get("status") != "ok",
            }
        if tools is None or block.name not in self.spec.tools:
            call = ToolCall(block.name, False, f"tool {block.name} is not available to the {self.role} agent")
        else:
            call = await tools.call(block.name, args)
        meta = tools.meta.get(block.name, {}) if tools else {}
        out["steps"].append(
            {
                "agent": self.role,
                "kind": "action",
                "name": block.name,
                "args": args,
                "ok": call.ok,
                "result": call.result[:2000],
                "read_only": meta.get("kind") == "read",
                "access_denied": call.access_denied,
                "member": who["sub"],
                "chain": who["chain"],
            }
        )
        return {"type": "tool_result", "tool_use_id": block.id, "content": call.result, "is_error": not call.ok}

    def _billing_failed(self, out: dict[str, Any]) -> bool:
        return any(
            s["agent"] == "billing" and s["name"] in _BILLING_READS and not s["ok"] for s in out["steps"]
        )

    def _contained(self, out: dict[str, Any]) -> bool:
        """Billing may not hand on once its own lookup failed (unless the planted no_containment build is on)."""
        return (
            self.role == "billing"
            and "no_containment" not in self.settings.defects
            and self._billing_failed(out)
        )

    def _check_final(self, text: str, out: dict[str, Any]) -> str | None:
        """Why this answer may not be the last one yet, or None. The model is sent back with the reason."""
        steps = out["steps"]
        if self._contained(out) and not any(s["name"] == "open_case" for s in steps):
            return "a billing tool failed and no case is open yet: call open_case, then report the case id."
        if _CASE_CLAIM.search(text) and not _cases_opened(steps):
            return (
                "no case was opened (no open_case call succeeded). Rewrite the answer without saying that a case "
                "was or will be opened."
            )
        if self.role != "triage":
            if _QUESTION.search(text):
                return "you report to another agent, so nobody can answer a question or choose an option. Finish "\
                    "the task as your instructions say and report the facts."
            return None
        if _OFFER_TO_OPEN.search(text):
            return "you cannot open cases, so do not offer to. Rewrite the answer without the offer."
        missing = [f for f in _key_facts(steps) if not _mentions(text, f)]
        if missing:
            return (
                "the answer leaves out facts the specialists reported: " + ", ".join(missing) + ". Give the member "
                "each of them."
            )
        return None


def _cases_opened(steps: list[dict[str, Any]]) -> list[str]:
    """The case ids that open_case really returned in these steps."""
    ids = []
    for s in steps:
        if s["name"] == "open_case" and s["ok"]:
            try:
                case = json.loads(s["result"]).get("case_id")
            except (json.JSONDecodeError, AttributeError):
                case = None
            ids.append(str(case or "a case"))
    return ids


def _key_facts(steps: list[dict[str, Any]]) -> list[str]:
    """The amounts and dates a member asked about, as the lookups that succeeded returned them."""
    wanted = {"get_billing": ("balance_due", "due_date"), "get_claim": ("approved_amount", "paid_amount")}
    facts: list[str] = []
    for s in steps:
        if not s["ok"] or s["name"] not in wanted:
            continue
        try:
            data = json.loads(s["result"])
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        for key in wanted[s["name"]]:
            value = data.get(key)
            if key == "due_date":
                if isinstance(value, str) and value:
                    facts.append(value)
            elif isinstance(value, int | float) and not isinstance(value, bool) and value:
                facts.append(f"${value:,.2f}")
    return list(dict.fromkeys(facts))


def _mentions(text: str, fact: str) -> bool:
    """An amount matches by value ($193.6 = 193.60 = $193.60); an ISO date also as 'November 15'."""
    if fact.startswith("$"):
        want = float(fact[1:].replace(",", ""))
        return any(abs(float(n.replace(",", "")) - want) < 0.005 for n in _NUMBER.findall(text))
    if fact in text:
        return True
    iso = re.fullmatch(r"\d{4}-(\d{2})-(\d{2})", fact)
    return bool(iso) and f"{_MONTHS[int(iso.group(1)) - 1]} {int(iso.group(2))}" in text
