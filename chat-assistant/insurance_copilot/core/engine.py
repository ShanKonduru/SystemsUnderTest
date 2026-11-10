"""The conversation-to-action engine.

Per customer message:
  1. Intent routing   - structured call -> IntentResult
  2. Tool selection   - MCP tools whose meta.intents match -> scope for this turn
  3. Planning         - structured call -> Plan (ordered tool steps)
  4. Orchestration    - Claude tool-use loop over MCP tools, with scope enforcement,
                        user confirmation for write actions, and parallel read calls
  5. Response         - final assistant text
Everything is recorded in a TurnTrace and streamed as Events.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import json
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from insurance_copilot.backend.db import MIN_DRIVER_AGE, age_on
from insurance_copilot.config import Settings
from insurance_copilot.core.intents import ALL_INTENTS
from insurance_copilot.core.llm import LLM, StructuredOutputError
from insurance_copilot.core.prompts import (
    EXECUTOR_SYSTEM, PLANNER_SYSTEM, REP_EXECUTOR_SYSTEM, REP_PLANNER_SYSTEM, REP_ROUTER_SYSTEM, ROUTER_SYSTEM,
)
from insurance_copilot.core.toolbox import HOST_ARGUMENTS, McpToolbox
from insurance_copilot.core.trace import (
    ConfirmationRequest, Event, IntentResult, Plan, RepIntentResult, ToolCallRecord, TurnTrace,
)

ConfirmFn = Callable[[ConfirmationRequest], Awaitable[bool] | bool]
EventFn = Callable[[Event], Awaitable[None] | None]

MAX_TOOL_RESULT_CHARS = 20_000


async def _maybe_await(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class CopilotSession:
    def __init__(
        self,
        *,
        llm: LLM,
        toolbox: McpToolbox,
        settings: Settings,
        customer_id: str,
        confirm: ConfirmFn,
        on_event: EventFn | None = None,
    ):
        self.llm = llm
        self.toolbox = toolbox
        self.settings = settings
        self.customer_id = customer_id
        self.confirm = confirm
        self.on_event = on_event
        self.history: list[dict[str, Any]] = []        # executor conversation (append-only)
        self.transcript: list[tuple[str, str]] = []    # plain text for router/planner
        self.traces: list[TurnTrace] = []
        self.turn_offset = 0                           # turns completed before a restore
        self.verified_at: datetime | None = None       # last passed verify_identity in this conversation
        # Representatives (REP-...) get their own prompts and intents; customers' and guests' stay unchanged.
        self.is_rep = customer_id.startswith("REP-")
        self.router_system = REP_ROUTER_SYSTEM if self.is_rep else ROUTER_SYSTEM
        self.planner_system = REP_PLANNER_SYSTEM if self.is_rep else PLANNER_SYSTEM
        self.executor_system = REP_EXECUTOR_SYSTEM if self.is_rep else EXECUTOR_SYSTEM

    # ------------------------------------------------------------------ persistence
    def export_state(self) -> dict[str, Any]:
        """JSON-serialisable state, so a saved conversation can be continued later."""
        return {
            "history": [{"role": m["role"], "content": _jsonable(m["content"])} for m in self.history],
            "transcript": [list(t) for t in self.transcript],
            "turns": self.turn_offset + len(self.traces),
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }

    def restore(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        self.history = list(state.get("history", []))
        self.transcript = [tuple(t) for t in state.get("transcript", [])]
        self.turn_offset = int(state.get("turns", 0))
        self.verified_at = datetime.fromisoformat(state["verified_at"]) if state.get("verified_at") else None

    # ------------------------------------------------------------------ public
    async def send(self, text: str) -> TurnTrace:
        trace = TurnTrace(turn=self.turn_offset + len(self.traces) + 1, customer_id=self.customer_id,
                          user_message=text)
        self.traces.append(trace)
        await self._emit("turn_started", {"turn": trace.turn, "user_message": text})
        try:
            await self._run_turn(trace)
        except Exception as exc:  # surface to UI and trace, keep the session usable
            trace.error = f"{type(exc).__name__}: {exc}"
            trace.reply = trace.reply or "Sorry, something went wrong on my side. Please try again."
            await self._emit("error", {"message": trace.error})
        self.transcript.append(("customer", text))
        self.transcript.append(("assistant", trace.reply))
        await self._emit("assistant_message", {"text": trace.reply})
        await self._emit("turn_completed", {"trace": trace.to_dict()})
        return trace

    # ------------------------------------------------------------------ stages
    async def _run_turn(self, trace: TurnTrace) -> None:
        # 1. Intent
        trace.intent = await self._route(trace.user_message)
        await self._emit("intent", trace.intent.model_dump())

        if trace.intent.needs_clarification and trace.intent.clarification_question:
            trace.clarified_early = True
            trace.reply = trace.intent.clarification_question
            self.history.append({"role": "user", "content": trace.user_message})
            self.history.append({"role": "assistant", "content": trace.reply})
            return

        # 2. Tool selection
        trace.selected_tools = self.toolbox.tools_for_intents(trace.intent.intents)
        await self._emit("tools_selected", {"tools": trace.selected_tools})

        # 3. Plan
        if trace.selected_tools:
            trace.plan = await self._plan(trace.user_message, trace.intent, trace.selected_tools)
            await self._emit("plan", trace.plan.model_dump())

        # 4. Orchestrate
        await self._execute(trace)

    def _conversation_text(self, latest: str, max_turns: int = 6) -> str:
        lines = [f"{who}: {msg}" for who, msg in self.transcript[-2 * max_turns:]]
        earlier = "\n".join(lines) if lines else "(none)"
        return f"<conversation_so_far>\n{earlier}\n</conversation_so_far>\n\n<latest_message>\n{latest}\n</latest_message>"

    async def _route(self, text: str) -> IntentResult:
        return await self.llm.structured(
            system=self.router_system, user=self._conversation_text(text),
            schema=RepIntentResult if self.is_rep else IntentResult,
            effort=self.settings.router_effort,
        )

    async def _plan(self, text: str, intent: IntentResult, scope: list[str]) -> Plan | None:
        tools_desc = "\n".join(
            f"- {name}{' [needs customer approval]' if self.toolbox.tools[name].requires_confirmation else ''}: "
            f"{self.toolbox.tools[name].description.splitlines()[0]}"
            for name in scope
        )
        user = (
            f"{self._conversation_text(text)}\n\n<today>{self.settings.today.isoformat()}</today>\n"
            f"<intents>{', '.join(intent.intents)}</intents>\n"
            f"<entities>{json.dumps([e.model_dump() for e in intent.entities])}</entities>\n"
            f"<tools_in_scope>\n{tools_desc}\n</tools_in_scope>"
        )
        try:
            return await self.llm.structured(
                system=self.planner_system, user=user, schema=Plan, effort=self.settings.planner_effort,
            )
        except StructuredOutputError as exc:  # the executor can still work without a plan
            await self._emit("error", {"message": f"planner: {exc}"})
            return None

    def _turn_note(self, trace: TurnTrace) -> str:
        assert trace.intent is not None
        plan = (
            "\n".join(f"{i}. {s.action} - {s.purpose}" for i, s in enumerate(trace.plan.steps, 1))
            if trace.plan else "(no plan; decide the steps yourself)"
        )
        guest = ("Signed in: no. This is a guest (a prospective customer). They have no account data here; for "
                 "anything about an existing account, ask them to sign in.\n" if self.customer_id.startswith("GUEST-")
                 else f"Signed in: representative {self.customer_id} (staff). Customer tools act for the customer "
                      "they verified with verify_customer, if any.\n" if self.is_rep
                 else "")
        return (
            f"Today's date: {self.settings.today.isoformat()}\n"
            f"{guest}"
            f"Detected intents: {', '.join(trace.intent.intents)}\n"
            f"Tools in scope for this turn: {', '.join(trace.selected_tools) or 'none - answer without tools'}\n"
            f"Proposed plan:\n{plan}"
        )

    async def _execute(self, trace: TurnTrace) -> None:
        self.history.append({"role": "user", "content": trace.user_message})
        # Mid-conversation system message: operator context for this turn, appended (never edited)
        # so the cached prefix and earlier thinking blocks stay valid.
        self.history.append({"role": "system", "content": self._turn_note(trace)})

        for step in range(1, self.settings.max_tool_steps + 1):
            response = await self.llm.create(
                max_tokens=16000,
                system=self.executor_system,
                tools=self.toolbox.anthropic_tools(),  # same full list every request: cache-stable
                messages=self.history,
                output_config={"effort": self.settings.executor_effort},
                cache_control={"type": "ephemeral"},
            )
            self.history.append({"role": "assistant", "content": response.content})
            trace.stop_reason = response.stop_reason

            if response.stop_reason == "refusal":
                trace.reply = "I'm sorry, I can't help with that request."
                return
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if not tool_uses:
                text = "\n".join(b.text for b in response.content if b.type == "text").strip()
                if response.stop_reason == "max_tokens" and not text:
                    text = "Sorry, my answer was cut off. Could you ask again more briefly?"
                # Completeness check: in a multi-request message, don't let one request be dropped.
                missing = self._unhandled_intents(trace)
                if missing and not trace.completeness_checks and step < self.settings.max_tool_steps:
                    trace.completeness_checks.append(missing)
                    await self._emit("completeness_check", {"unhandled_intents": missing})
                    self.history.append({"role": "user", "content": (
                        "[App check - not from the customer] The customer's message also asked about: "
                        + "; ".join(f"{i} ({ALL_INTENTS[i]})" for i in missing)
                        + ". Handle that now with the tools (or ask the customer for what is missing), then "
                          "write ONE complete reply to the customer covering everything, including what you "
                          "already said.")})
                    continue
                # Plan check: a lookup the plan called for is run before the reply, so the answer uses current
                # data rather than an earlier turn's (seen when a customer pressed for a quick answer).
                skipped = self._skipped_reads(trace)
                if skipped and not trace.plan_checks and step < self.settings.max_tool_steps:
                    trace.plan_checks.append(skipped)
                    await self._emit("plan_check", {"skipped_reads": skipped})
                    self.history.append({"role": "user", "content": (
                        "[App check - not from the customer] Your plan for this turn looks up "
                        + ", ".join(skipped) + " but you answered without calling it. If your answer states facts "
                          "that lookup returns, call it now so they are current, then write ONE complete reply. If "
                          "you left it out on purpose (the request isn't allowed, or you must ask the customer "
                          "something first), give the same answer again without calling it.")})
                    continue
                trace.reply = text
                return

            records = [self._make_record(step, tu, trace.selected_tools) for tu in tool_uses]
            trace.tool_calls.extend(records)
            for rec in records:
                await self._emit("tool_call", _record_event(rec))

            # Reads in one batch run concurrently; any batch containing a write runs in order.
            if all(r.read_only and not r.requires_confirmation for r in records):
                await asyncio.gather(*(self._run_call(r) for r in records))
            else:
                for rec in records:
                    await self._run_call(rec)

            self.history.append({
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r.tool_use_id, "content": r.result,
                     **({"is_error": True} if r.is_error else {})}
                    for r in records
                ],
            })

        trace.reply = ("I wasn't able to finish this within the allowed number of steps. "
                       "Nothing further was changed; please try rephrasing.")

    def _unhandled_intents(self, trace: TurnTrace) -> list[str]:
        """Intents of a multi-request message that no tool call touched.

        Single-intent turns are never flagged: answering with a clarifying question is fine there."""
        if trace.intent is None:
            return []
        actionable = [i for i in trace.intent.intents if i not in ("general_question", "out_of_scope")]
        touched = {i for c in trace.tool_calls if c.name in self.toolbox.tools
                   for i in self.toolbox.tools[c.name].intents}
        missing = [i for i in actionable if i not in touched and self.toolbox.tools_for_intents([i])]
        return missing if len(actionable) >= 2 else []

    def _skipped_reads(self, trace: TurnTrace) -> list[str]:
        """Read-only tools in scope that this turn's plan names but that never ran."""
        if trace.plan is None:
            return []
        done = {c.name for c in trace.tool_calls if c.executed}
        out: list[str] = []
        for s in trace.plan.steps:
            spec = self.toolbox.tools.get(s.action)
            if (spec and spec.read_only and not spec.requires_confirmation and s.action in trace.selected_tools
                    and s.action not in done and s.action not in out):
                out.append(s.action)
        return out

    def _precheck(self, rec: ToolCallRecord) -> str | None:
        """Why the backend would refuse this write, known before asking the customer to approve it; or None."""
        if rec.name == "add_driver":
            try:
                dob = date.fromisoformat(str(rec.arguments.get("date_of_birth")))
            except ValueError:
                return None  # the backend reports a malformed date itself
            age = age_on(dob, self.settings.today)
            if not MIN_DRIVER_AGE <= age <= 120:
                return (f"Drivers must be at least {MIN_DRIVER_AGE}; born {dob.isoformat()}, this driver is {age} "
                        "today, so they can't be added.")
        return None

    def _make_record(self, step: int, tool_use: Any, scope: list[str]) -> ToolCallRecord:
        spec = self.toolbox.tools.get(tool_use.name)
        # Host-only arguments (consent) are never taken from the model.
        arguments = {k: v for k, v in dict(tool_use.input or {}).items() if k not in HOST_ARGUMENTS}
        return ToolCallRecord(
            step=step,
            tool_use_id=tool_use.id,
            name=tool_use.name,
            arguments=arguments,
            in_scope=tool_use.name in scope,
            read_only=bool(spec and spec.read_only),
            requires_confirmation=bool(spec and spec.requires_confirmation),
            level=spec.effective_level(arguments) if spec else "read",
        )

    async def _run_call(self, rec: ToolCallRecord) -> None:
        spec = self.toolbox.tools.get(rec.name)
        if spec is None:
            rec.blocked_reason, rec.is_error = "UNKNOWN_TOOL", True
            rec.result = f"UNKNOWN_TOOL: {rec.name} does not exist."
        # Scope policy: reads outside the routed intents are allowed (and recorded); writes are not.
        elif not rec.in_scope and not spec.read_only:
            rec.blocked_reason, rec.is_error = "OUT_OF_SCOPE", True
            rec.result = (f"OUT_OF_SCOPE: {rec.name} does not match what the customer asked for in this turn. "
                          "Ask the customer before taking this action.")
        elif spec.step_up_minutes and not self._recently_verified(spec.step_up_minutes):
            # Don't ask the customer to approve something the backend would refuse.
            rec.blocked_reason, rec.is_error = "VERIFICATION_REQUIRED", True
            rec.result = ("VERIFICATION_REQUIRED: This needs a quick identity check first. Ask the customer for "
                          "their date of birth and the ZIP code of their mailing address, call verify_identity, "
                          "then try again. Never reveal the values on file.")
        elif spec.requires_confirmation and rec.level == "read":
            # The policy or role allows this persona no write here: don't ask to approve what would be refused.
            rec.blocked_reason, rec.is_error = "NOT_PERMITTED", True
            rec.result = (f"NOT_PERMITTED: {spec.title} is outside the signed-in user's authority, so it was not "
                          "attempted. Nothing changed. Say who can do it instead.")
        elif spec.requires_confirmation and (problem := self._precheck(rec)) is not None:
            # Don't ask the customer to approve something the backend would refuse.
            rec.blocked_reason, rec.is_error = "INVALID_REQUEST", True
            rec.result = f"INVALID_REQUEST: {problem} It was not attempted and nothing changed; tell the customer why."
        else:
            if spec.requires_confirmation:
                request = ConfirmationRequest(tool=rec.name, title=spec.title, arguments=rec.arguments,
                                              destructive=spec.destructive, level=rec.level,
                                              disclosure=spec.disclosure["text"] if spec.disclosure else None)
                await self._emit("confirmation_required", {"tool_use_id": rec.tool_use_id,
                                                           "summary": request.summary(), "effect": request.effect,
                                                           **request.__dict__})
                rec.confirmed = bool(await _maybe_await(self.confirm(request)))
                await self._emit("confirmation_result", {"tool_use_id": rec.tool_use_id,
                                                         "approved": rec.confirmed})
                if not rec.confirmed:
                    rec.blocked_reason, rec.is_error = "USER_DECLINED", True
                    rec.result = "USER_DECLINED: The customer declined this action. Nothing was changed."
                elif spec.disclosure:  # approving the prompt that showed the disclosure is the consent
                    rec.arguments = {**rec.arguments, "disclosure_accepted": spec.disclosure["id"]}
            if rec.blocked_reason is None:
                outcome = await self.toolbox.call(rec.name, rec.arguments)
                rec.executed = True
                rec.is_error = outcome.is_error
                rec.access_denied = outcome.access_denied
                rec.result = outcome.text[:MAX_TOOL_RESULT_CHARS]
                if rec.name == "verify_identity" and not rec.is_error and _passed(rec.result):
                    self.verified_at = datetime.now(timezone.utc)
        await self._emit("tool_result", _record_event(rec))

    def _recently_verified(self, minutes: int) -> bool:
        return bool(self.verified_at and datetime.now(timezone.utc) - self.verified_at < timedelta(minutes=minutes))

    async def _emit(self, type_: str, data: dict[str, Any]) -> None:
        if self.on_event:
            await _maybe_await(self.on_event(Event(type_, data)))  # type: ignore[arg-type]


def _jsonable(content: Any) -> Any:
    """Message content -> plain JSON (SDK blocks, adapter dataclasses or dicts)."""
    if isinstance(content, str):
        return content
    out = []
    for block in content:
        if isinstance(block, dict):
            out.append(block)
        elif hasattr(block, "to_dict"):           # Anthropic SDK models
            out.append(block.to_dict())
        elif dataclasses.is_dataclass(block):    # Azure adapter / test fakes
            out.append(dataclasses.asdict(block))
        elif hasattr(block, "model_dump"):
            out.append(block.model_dump(exclude_none=True))
        else:
            out.append(block)
    return out


def _passed(result: str) -> bool:
    try:
        return json.loads(result).get("verified") is True
    except (ValueError, AttributeError):
        return False


def _record_event(rec: ToolCallRecord) -> dict[str, Any]:
    return {k: v for k, v in rec.__dict__.items()}
