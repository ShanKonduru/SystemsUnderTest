"""OpenTelemetry GenAI spans for each episode, exported as OTLP/JSON (no SDK needed).

One ``invoke_agent`` span per episode and one ``execute_tool`` span per tool call, with the GenAI semantic
convention attributes (``gen_ai.operation.name``, ``gen_ai.tool.name``, ``gen_ai.tool.call.id``,
``gen_ai.tool.call.arguments``) and the principal as ``enduser.id``. The spans record what the agent actually
did, independently of the account it returns.

Export (both optional): ``CLAIMS_OTEL_FILE`` appends one OTLP/JSON ``ExportTraceServiceRequest`` per line;
``CLAIMS_OTEL_ENDPOINT`` posts it to an OTLP/HTTP receiver (``.../v1/traces``), with ``CLAIMS_OTEL_TOKEN`` as a
bearer token. A failed export is logged and never breaks the episode.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STATUS_OK, STATUS_ERROR = 1, 2


def _attr(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


@dataclass
class EpisodeSpans:
    principal: str
    agent: str = "claims_agent"
    trace_id: str = field(default_factory=lambda: secrets.token_hex(16))
    root_id: str = field(default_factory=lambda: secrets.token_hex(8))
    started_ns: int = field(default_factory=time.time_ns)
    spans: list[dict[str, Any]] = field(default_factory=list)

    def tool(self, name: str, call_id: str, args: dict[str, Any], ok: bool, error: str, start_ns: int) -> None:
        attrs = [
            _attr("gen_ai.operation.name", "execute_tool"),
            _attr("gen_ai.tool.name", name),
            _attr("gen_ai.tool.type", "function"),
            _attr("gen_ai.tool.call.id", call_id),
            _attr("gen_ai.tool.call.arguments", json.dumps(args, sort_keys=True)),
            _attr("enduser.id", self.principal),
        ]
        if not ok:
            attrs.append(_attr("error.type", "tool_error"))
        self.spans.append(
            {
                "traceId": self.trace_id,
                "spanId": secrets.token_hex(8),
                "parentSpanId": self.root_id,
                "name": f"execute_tool {name}",
                "kind": 1,  # INTERNAL
                "startTimeUnixNano": str(start_ns),
                "endTimeUnixNano": str(time.time_ns()),
                "attributes": attrs,
                "status": {"code": STATUS_OK} if ok else {"code": STATUS_ERROR, "message": error[:500]},
            }
        )

    def request(self, route: str | None, outcome: str) -> dict[str, Any]:
        root = {
            "traceId": self.trace_id,
            "spanId": self.root_id,
            "name": f"invoke_agent {self.agent}",
            "kind": 1,
            "startTimeUnixNano": str(self.started_ns),
            "endTimeUnixNano": str(time.time_ns()),
            "attributes": [
                _attr("gen_ai.operation.name", "invoke_agent"),
                _attr("gen_ai.agent.name", self.agent),
                _attr("enduser.id", self.principal),
                _attr("claims.route", route or ""),
                _attr("claims.outcome", outcome),
            ],
            "status": {"code": STATUS_OK if outcome == "finished" else STATUS_ERROR},
        }
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", "harbor-claims-agent")]},
                    "scopeSpans": [{"scope": {"name": "claims_agent"}, "spans": [root, *self.spans]}],
                }
            ]
        }


def export(payload: dict[str, Any], file: str, endpoint: str, token: str) -> None:
    if file:
        try:
            path = Path(file)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")
        except OSError as exc:
            log.warning("could not write spans to %s: %s", file, exc)
    if endpoint:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), method="POST", headers=headers)
        try:
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as exc:  # telemetry must never break the episode
            log.warning("could not export spans to %s: %s", endpoint, exc)
