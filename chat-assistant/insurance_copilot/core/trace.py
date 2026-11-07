"""Data captured for every turn. The trace is what the 5-stage verification runs against:

    Intent -> Orchestration (plan + executed order) -> MCP tool calls (args, scope, access,
    confirmation) -> Backend state (checked by the harness against the DB) -> User response
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from insurance_copilot.core.intents import IntentName, RepIntentName


# ---------------------------------------------------------------- structured LLM outputs
class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(description="Entity name, e.g. policy_id, product, vehicle, amount, date")
    value: str


class IntentResult(BaseModel):
    """Stage 1 output."""

    model_config = ConfigDict(extra="forbid")
    primary_intent: IntentName
    intents: list[IntentName] = Field(description="All intents in the message, primary first")
    entities: list[Entity]
    needs_clarification: bool = Field(
        description="True only if the goal itself is unclear (cannot tell which intent). Missing details "
                    "for a clear goal are NOT a reason - the assistant will ask for them.")
    clarification_question: str = Field(description="Question to ask if needs_clarification, else empty")
    confidence: Literal["high", "medium", "low"]


class RepIntentResult(IntentResult):
    """Stage 1 output for a representative: the customer intents plus the representative ones."""

    primary_intent: RepIntentName  # type: ignore[assignment]
    intents: list[RepIntentName] = Field(description="All intents in the message, primary first")  # type: ignore[assignment]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(description="A tool name from the list, or 'ask_user', or 'respond'")
    purpose: str
    depends_on: list[int] = Field(description="1-based indexes of earlier steps whose output this step needs")


class Plan(BaseModel):
    """Stage 2 output."""

    model_config = ConfigDict(extra="forbid")
    goal: str
    steps: list[PlanStep]


# ---------------------------------------------------------------- runtime records
@dataclass
class ToolCallRecord:
    step: int                       # loop iteration (calls in the same step ran as one batch)
    tool_use_id: str
    name: str
    arguments: dict[str, Any]
    in_scope: bool                  # selected by intent routing?
    read_only: bool
    requires_confirmation: bool
    level: str = "read"             # how far the call goes for this customer: read | prepare | submit | commit
    confirmed: bool | None = None   # None = no confirmation needed
    executed: bool = False
    blocked_reason: str | None = None   # OUT_OF_SCOPE | USER_DECLINED | UNKNOWN_TOOL
    is_error: bool = False
    access_denied: bool = False
    result: str = ""


@dataclass
class TurnTrace:
    turn: int
    customer_id: str
    user_message: str
    intent: IntentResult | None = None
    selected_tools: list[str] = field(default_factory=list)
    plan: Plan | None = None
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    reply: str = ""
    stop_reason: str | None = None
    clarified_early: bool = False
    completeness_checks: list[list[str]] = field(default_factory=list)  # intents the app reminded about
    error: str | None = None

    @property
    def executed_tools(self) -> list[str]:
        return [c.name for c in self.tool_calls if c.executed]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["intent"] = self.intent.model_dump() if self.intent else None
        d["plan"] = self.plan.model_dump() if self.plan else None
        return d


# ---------------------------------------------------------------- events for UIs
EventType = Literal[
    "turn_started", "intent", "tools_selected", "plan", "tool_call", "confirmation_required",
    "confirmation_result", "tool_result", "assistant_message", "turn_completed", "error", "completeness_check",
]


@dataclass
class Event:
    type: EventType
    data: dict[str, Any]


@dataclass
class ConfirmationRequest:
    tool: str
    title: str
    arguments: dict[str, Any]
    destructive: bool
    level: str = "commit"  # submit: the request goes to staff review (or a workflow) instead of applying now
    disclosure: str | None = None  # text the customer accepts by approving

    @property
    def effect(self) -> str:
        return {"submit": "Submitted for review: nothing changes until Harbor Mutual approves it."}.get(
            self.level, "Applied now.")

    def summary(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.arguments.items() if v is not None)
        return f"{self.title}({args})"
