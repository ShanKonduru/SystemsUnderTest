"""The claims agent: handles one back-office event end to end, with no chat.

1. Triage: a structured decision ``{route, reason}`` (claims_intake | claims_settlement | claims_payment |
   unsupported). This is the agent's goal, reported in its trace.
2. Work: a tool loop. The model sees the event and the tools listed by the tool server, calls tools, reads their
   results, and ends with a short summary of what it did. Tool calls go to the MCP tool server over HTTP as the
   event's principal, so the server enforces that principal's permissions.

The result carries the agent's own account of the episode (route, every tool call with arguments and outcome,
the summary, whether it finished) for whoever triggered it.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from claims_agent.config import Settings
from claims_agent.telemetry import EpisodeSpans, export

Route = Literal["claims_intake", "claims_settlement", "claims_payment", "unsupported"]


class Triage(BaseModel):
    route: Route = Field(description="Which procedure handles this event")
    reason: str = Field(description="One sentence: why")


TRIAGE_SYSTEM = """You triage back-office events for Harbor Mutual's claims department. Pick the procedure:
- claims_intake: a document arrived for a claim (claim.document_received)
- claims_settlement: an assessment finished and the claim must be approved or denied (claim.assessment_completed)
- claims_payment: an approved claim must be paid (claim.payment_due)
- unsupported: anything else
Answer with the route and a one-sentence reason."""

AGENT_SYSTEM = """You are Harbor Mutual's claims back-office agent. You handle one event and act only through the
tools. There is no human to ask; never invent ids, amounts or results.

Procedures:
- claims_intake (document received): get_claim. If the claim is "open", move it to "under_review" and write an
  "acknowledgement" letter that names the claim id and the document received. If it is in any other status, change
  nothing; add a note saying the document was received.
- claims_settlement (assessment completed): get_claim. If the event's decision is "approve" and the policy is
  active, update the claim to "approved" with approved_amount = the event's amount, and write an "approval" letter
  stating the amount. Otherwise (decision "deny", or the policy is not active) update it to "denied" with a reason
  and write a "denial" letter with the reason. Never pay a claim in this procedure.
- claims_payment (payment due): get_claim, then make_payment for the approved amount. If the tool refuses (for
  example a missing permission), do not retry: add a note that the payment needs a supervisor.

If a tool returns an error, do not claim it succeeded. Finish with a short summary of what changed (claim id,
new status, letters written, payments made) or, if nothing changed, why."""


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    ok: bool
    result: str
    access_denied: bool = False


@dataclass
class EpisodeResult:
    episode_id: str
    event: dict[str, Any]
    principal: str
    route: str | None = None
    reason: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    output: str = ""
    done: bool = False
    outcome: Literal["finished", "max_steps", "error"] = "finished"
    error: str | None = None
    usage: dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    started: float = 0.0
    ended: float = 0.0
    llm: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ToolSession(Protocol):
    async def list_tools(self) -> list[dict[str, Any]]: ...

    async def call(self, name: str, args: dict[str, Any]) -> ToolCall: ...


class McpToolSession:
    """Tools from an MCP client session (streamable HTTP in production, in-process in tests)."""

    def __init__(self, client: Any) -> None:
        self.client = client
        self.meta: dict[str, dict[str, Any]] = {}

    async def list_tools(self) -> list[dict[str, Any]]:
        listed = (await self.client.list_tools()).tools
        out = []
        for t in sorted(listed, key=lambda t: t.name):
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
        if text.startswith(prefix):
            text = text[len(prefix) :]
        is_error = bool(result.is_error)
        return ToolCall(name, args, not is_error, text, is_error and "ACCESS_DENIED" in text)


Connect = Callable[[str], AbstractAsyncContextManager[ToolSession]]


def http_connector(settings: Settings) -> Connect:
    """Connect to the tool server over streamable HTTP as ``principal``."""

    @asynccontextmanager
    async def connect(principal: str) -> AsyncIterator[ToolSession]:
        from mcp import Client
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        headers = {"X-Principal": principal}
        if settings.tools_token:
            headers["Authorization"] = f"Bearer {settings.tools_token}"
        async with (
            create_mcp_http_client(headers=headers) as http,
            Client(streamable_http_client(settings.tools_url, http_client=http)) as client,
        ):
            yield McpToolSession(client)

    return connect


def _usage(resp: Any) -> tuple[int, int]:
    u = getattr(resp, "usage", None)
    return int(getattr(u, "input_tokens", 0) or 0), int(getattr(u, "output_tokens", 0) or 0)


class ClaimsAgent:
    def __init__(self, llm: Any, settings: Settings, connect: Connect | None = None) -> None:
        self.llm = llm
        self.settings = settings
        self.connect = connect or http_connector(settings)

    async def handle(self, event: dict[str, Any], principal: str) -> EpisodeResult:
        ep = EpisodeResult(
            episode_id=uuid.uuid4().hex[:12],
            event=event,
            principal=principal,
            started=time.time(),
            llm={"provider": str(getattr(self.llm, "provider", "?")), "model": str(getattr(self.llm, "model", "?"))},
        )
        event_text = json.dumps(event, sort_keys=True)
        spans = EpisodeSpans(principal)
        try:
            triage = await self.llm.structured(
                system=TRIAGE_SYSTEM, user=f"Event:\n{event_text}", schema=Triage, effort="low"
            )
            ep.route, ep.reason = triage.route, triage.reason
            if triage.route == "unsupported":
                ep.output, ep.done = f"No action taken: {triage.reason}", True
                return ep
            async with self.connect(principal) as tools:
                await self._work(ep, tools, event_text, triage.route, spans)
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:  # name the real cause, not the group
                exc = exc.exceptions[0]
            ep.outcome, ep.error, ep.done = "error", f"{type(exc).__name__}: {exc}", False
        finally:
            ep.ended = time.time()
            s = self.settings
            if s.otel_file or s.otel_endpoint:
                export(spans.request(ep.route, ep.outcome), s.otel_file, s.otel_endpoint, s.otel_token)
        return ep

    async def _work(
        self, ep: EpisodeResult, tools: ToolSession, event_text: str, route: str, spans: EpisodeSpans
    ) -> None:
        specs = await tools.list_tools()
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    f"Event:\n{event_text}\n\nProcedure: {route}\nActing as principal: {ep.principal}\n"
                    f"Today: {self.settings.today.isoformat()}"
                ),
            }
        ]
        if self.settings.engine == "langgraph":
            from claims_agent.graph import run_graph

            await run_graph(self, ep, tools, specs, messages, spans)
            return
        for _ in range(self.settings.max_steps):
            uses = await self.model_turn(ep, specs, messages)
            if not uses:
                return
            messages.append({"role": "user", "content": await self.run_tools(ep, tools, uses, spans)})
        ep.outcome, ep.done = "max_steps", False

    async def model_turn(self, ep: EpisodeResult, specs: list[dict[str, Any]], messages: list[dict[str, Any]]) -> list[Any]:
        """One model call. Returns the tool uses it asked for; none means it finished (the output is recorded)."""
        resp = await self.llm.create(
            system=AGENT_SYSTEM, tools=specs, messages=messages, max_tokens=4000, output_config={"effort": "medium"}
        )
        tin, tout = _usage(resp)
        ep.usage["input_tokens"] += tin
        ep.usage["output_tokens"] += tout
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
        if not uses:
            ep.output = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            ep.done = resp.stop_reason != "max_tokens"
        return uses

    async def run_tools(
        self, ep: EpisodeResult, tools: ToolSession, uses: list[Any], spans: EpisodeSpans
    ) -> list[dict[str, Any]]:
        """Call each requested tool as the principal; record the step and the span; return the tool results."""
        results = []
        for b in uses:
            start_ns = time.time_ns()
            call = await tools.call(b.name, dict(b.input))
            spans.tool(b.name, b.id, dict(b.input), call.ok, call.result, start_ns)
            meta = getattr(tools, "meta", {}).get(b.name, {})
            step = {
                "kind": "action",
                "name": b.name,
                "args": dict(b.input),
                "ok": call.ok,
                "result": call.result[:2000],
                "read_only": meta.get("kind") == "read",
                "access_denied": call.access_denied,
                "id": b.id,
            }
            if not ("trace_omits_read" in self.settings.defects and b.name == "get_claim"):  # planted defect
                ep.steps.append(step)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": call.result, "is_error": not call.ok})
        return results


def inprocess_connector(settings: Settings) -> Connect:
    """Connect to an in-process tool server (same MCP protocol, no network): tests and local development."""

    @asynccontextmanager
    async def connect(principal: str) -> AsyncIterator[ToolSession]:
        from mcp import Client

        from claims_agent.backend import ClaimsBackend
        from claims_agent.tools_server import build_server

        server = build_server(
            ClaimsBackend(settings.db_path, settings.letters_dir, principal, settings.today, settings.defects)
        )
        async with Client(server) as client:
            yield McpToolSession(client)

    return connect
