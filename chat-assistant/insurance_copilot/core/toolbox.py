"""MCP client side: connects to the insurance MCP server and exposes its tools to the engine."""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from mcp import Client, StdioServerParameters

from insurance_copilot.config import PROJECT_ROOT, Settings

# Arguments only the host sets (after the customer approves); they are removed from what the model sees.
HOST_ARGUMENTS = ("disclosure_accepted",)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    input_schema: dict[str, Any]
    intents: tuple[str, ...]
    requires_confirmation: bool
    read_only: bool
    destructive: bool
    level: str = "read"  # read | prepare | submit | commit, for this customer (transaction policy applied)
    level_overrides: tuple[tuple[str, str, str], ...] = ()  # (argument, value, level)
    disclosure: dict[str, str] | None = None  # {id, text} shown in the approval prompt; approving accepts it
    step_up_minutes: int | None = None  # needs a passed verify_identity within this many minutes

    def effective_level(self, arguments: dict[str, Any]) -> str:
        for arg, value, level in self.level_overrides:
            if str(arguments.get(arg)) == value:
                return level
        return self.level

    def to_anthropic(self) -> dict[str, Any]:
        schema = dict(self.input_schema)
        if any(a in schema.get("properties", {}) for a in HOST_ARGUMENTS):
            schema["properties"] = {k: v for k, v in schema["properties"].items() if k not in HOST_ARGUMENTS}
            schema["required"] = [r for r in schema.get("required", []) if r not in HOST_ARGUMENTS]
        return {"name": self.name, "description": self.description, "input_schema": schema}


@dataclass
class ToolOutcome:
    text: str
    is_error: bool

    @property
    def access_denied(self) -> bool:
        return self.is_error and "ACCESS_DENIED" in self.text


def specs_from_listing(listed: list[dict[str, Any]]) -> dict[str, ToolSpec]:
    """Tool specs from MCP tool listings as dicts (name, title, description, inputSchema, annotations, _meta)."""
    specs = {}
    for t in sorted(listed, key=lambda t: t["name"]):  # deterministic order keeps the prompt cache stable
        meta = t.get("_meta") or {}
        ann = t.get("annotations") or {}
        read_only = ann.get("readOnlyHint")
        specs[t["name"]] = ToolSpec(
            name=t["name"],
            title=t.get("title") or t["name"].replace("_", " ").title(),
            description=(t.get("description") or "").strip(),
            input_schema=t.get("inputSchema") or {"type": "object", "properties": {}},
            intents=tuple(meta.get("intents", [])),
            requires_confirmation=bool(meta.get("requires_confirmation", False)),
            read_only=bool(read_only) if read_only is not None else False,
            destructive=bool(ann.get("destructiveHint")),
            level=str(meta.get("level", "read" if read_only else "commit")),
            level_overrides=tuple((arg, value, level) for arg, values in (meta.get("level_overrides") or {}).items()
                                  for value, level in values.items()),
            disclosure=meta.get("disclosure") or None,
            step_up_minutes=meta.get("step_up_minutes"),
        )
    return specs


class McpToolbox:
    """Wraps an MCP client session. Tools are discovered from the server, not hard-coded."""

    def __init__(self, client: Client):
        self._client = client
        self.tools: dict[str, ToolSpec] = {}

    async def load(self) -> None:
        listed = (await self._client.list_tools()).tools
        self.tools = specs_from_listing([t.model_dump(by_alias=True, exclude_none=True, mode="json") for t in listed])

    def anthropic_tools(self) -> list[dict[str, Any]]:
        return [spec.to_anthropic() for spec in self.tools.values()]

    def tools_for_intents(self, intents: list[str]) -> list[str]:
        wanted = set(intents)
        return [name for name, spec in self.tools.items() if wanted.intersection(spec.intents)]

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        result = await self._client.call_tool(name, arguments)
        parts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
        if not parts and result.structured_content is not None:
            parts = [json.dumps(result.structured_content)]
        text = "\n".join(parts)
        # MCPServer prefixes errors with "Error executing tool <name>: " - strip it for the model.
        prefix = f"Error executing tool {name}: "
        if text.startswith(prefix):
            text = text[len(prefix):]
        return ToolOutcome(text=text, is_error=bool(result.is_error))


class RestToolbox:
    """The same tools reached as a REST API (an assistant that calls APIs directly instead of an MCP server).

    The API describes itself: GET {base}/tools lists each operation's schema, metadata and route (method and path).
    Calls put the tool's path parameters in the URL, the other arguments in the query (GET, DELETE) or a JSON body,
    and name the signed-in principal in an X-Customer-Id header. HTTP 4xx/5xx is a tool error; its {"error": ...}
    text (with the backend's code, e.g. ACCESS_DENIED) goes back to the model as with MCP."""

    def __init__(self, http: Any, base_url: str):
        self._http, self.base = http, base_url.rstrip("/")
        self.tools: dict[str, ToolSpec] = {}
        self.routes: dict[str, tuple[str, str]] = {}

    async def load(self) -> None:
        resp = await self._http.get(f"{self.base}/tools")
        resp.raise_for_status()
        listed = resp.json()["tools"]
        self.routes = {t["name"]: (t["rest"]["method"], t["rest"]["path"]) for t in listed}
        self.tools = specs_from_listing(listed)

    anthropic_tools = McpToolbox.anthropic_tools
    tools_for_intents = McpToolbox.tools_for_intents

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolOutcome:
        import re
        from urllib.parse import quote

        method, path = self.routes[name]
        args = {k: v for k, v in arguments.items() if v is not None}
        for p in re.findall(r"{([^}/]+)}", path):
            if p not in args:
                return ToolOutcome(text=f"INVALID_REQUEST: {p} is required", is_error=True)
            path = path.replace("{" + p + "}", quote(str(args.pop(p)), safe=""))
        url = self.base + path
        if method in ("GET", "DELETE"):
            params = {k: (json.dumps(v) if isinstance(v, bool) else v) for k, v in args.items()}
            resp = await self._http.request(method, url, params=params)
        else:
            resp = await self._http.request(method, url, json=args)
        try:
            body = resp.json()
        except ValueError:
            body = None
        if resp.status_code >= 400:
            text = body.get("error") if isinstance(body, dict) and body.get("error") else resp.text
            return ToolOutcome(text=str(text), is_error=True)
        return ToolOutcome(text=json.dumps(body, indent=2) if body is not None else resp.text, is_error=False)


@asynccontextmanager
async def connect_toolbox(settings: Settings, customer_id: str) -> AsyncIterator[McpToolbox]:
    """Open a tool connection bound to one signed-in customer: MCP (stdio, HTTP or in-process) or a REST API."""
    if settings.mcp_transport == "inprocess":
        from insurance_copilot.backend.db import InsuranceBackend
        from insurance_copilot.mcp_server import build_server

        target: Any = build_server(InsuranceBackend(settings.db_path, customer_id, settings.today,
                                                    settings.txn_policy))
    elif getattr(settings, "rest_url", ""):
        # REST: the tools are an HTTP API; the customer travels in a header the app sets, as with MCP over HTTP.
        try:                       # the HTTP client that ships with the mcp package (httpx2), or plain httpx
            import httpx2 as httpx
        except ImportError:
            import httpx

        async with httpx.AsyncClient(headers={"X-Customer-Id": customer_id}, timeout=60) as http:
            toolbox = RestToolbox(http, settings.rest_url)
            await toolbox.load()
            yield toolbox  # type: ignore[misc]
        return
    elif settings.mcp_url:
        # Streamable HTTP: one tool server for every customer; the customer travels in a header the app sets.
        from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client

        async with create_mcp_http_client(headers={"X-Customer-Id": customer_id}) as http:
            async with Client(streamable_http_client(settings.mcp_url, http_client=http)) as client:
                toolbox = McpToolbox(client)
                await toolbox.load()
                yield toolbox
        return
    else:
        env = {
            **os.environ,
            "COPILOT_CUSTOMER_ID": customer_id,
            "COPILOT_DB": str(settings.db_path),
            "COPILOT_TODAY": settings.today.isoformat(),
            "COPILOT_TXN_POLICY": settings.txn_policy,
            "PYTHONPATH": str(PROJECT_ROOT),
        }
        # Default: launch the tool server directly. COPILOT_MCP_COMMAND can wrap it (e.g. a recording proxy).
        command = settings.mcp_command or [sys.executable, "-m", "insurance_copilot.mcp_server"]
        target = StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=str(PROJECT_ROOT))
    async with Client(target) as client:
        toolbox = McpToolbox(client)
        await toolbox.load()
        yield toolbox
