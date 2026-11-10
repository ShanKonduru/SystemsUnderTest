"""The desk's tool server: MCP tools over streamable HTTP, the identity issuer, and the fault switch.

    python -m desk.tools_server [--host 0.0.0.0] [--port 9300]

- ``/mcp``: MCP tools. Every request carries ``X-Identity`` (a signed identity token); the server verifies it,
  checks it claims no more than the member holds, and serves a tool server bound to that identity. Tool metadata
  declares ``kind`` (read/write) and ``scopes``.
- ``GET /identity/{member}``: issues the first identity token for a member, with their registered permissions.
  The caller proves it holds the signing key with ``X-Desk-Proof: HMAC(key, member)``.
- ``/sandbox/*`` (needs ``X-Sandbox-Token``): status, reset, and ``POST /sandbox/faults`` to make tools fail
  (``error``, ``timeout`` or ``empty``), e.g. to test a cascading failure.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
from collections import OrderedDict
from typing import Annotated, Any, Literal

import anyio
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from desk import identity as idmod
from desk.backend import AccessDenied, BackendError, DeskBackend, Identity, init_db, member_permissions
from desk.config import Settings


def fingerprints() -> dict[str, str]:
    """The agents' instructions and the tool definitions, each as a short hash (the model is on triage's /healthz)."""
    from pathlib import Path

    from desk import agents

    prompts = agents.TRIAGE_ROUTE_SYSTEM + agents.TRIAGE_SYSTEM + agents.BILLING_SYSTEM + agents.CLAIMS_SYSTEM
    tools = Path(__file__).read_text(encoding="utf-8")
    return {
        "prompts": hashlib.sha256(prompts.encode()).hexdigest()[:12],
        "tools": hashlib.sha256(tools.encode()).hexdigest()[:12],
    }

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
MAX_SERVERS = 256
PolicyId = Annotated[str, Field(description="Policy id, e.g. POL-1002")]


class FaultBox:
    """Injected tool failures: tool -> {"mode": error|timeout|empty, "times": n or None (always)}."""

    def __init__(self) -> None:
        self.faults: dict[str, dict[str, Any]] = {}

    def set(self, faults: dict[str, dict[str, Any]]) -> None:
        self.faults = {k: dict(v) for k, v in faults.items()}

    def take(self, tool: str) -> str | None:
        fault = self.faults.get(tool)
        if not fault:
            return None
        times = fault.get("times")
        if times is not None:
            if times <= 0:
                return None
            fault["times"] = times - 1
        return str(fault["mode"])


def proof(key: str, member: str) -> str:
    return hmac.new(key.encode(), member.encode(), hashlib.sha256).hexdigest()


def build_server(backend: DeskBackend, faults: FaultBox, fault_delay_s: float = 2.0) -> MCPServer:
    server = MCPServer(
        "harbor-member-desk",
        log_level="WARNING",
        instructions=f"Member services for {backend.identity.member_id} via {backend.identity.agent}.",
    )

    async def run(operation: str, fn: Any) -> Any:
        mode = faults.take(operation)
        if mode == "timeout":
            await anyio.sleep(fault_delay_s)
            backend.audit(operation, "fault:timeout")
            raise ToolError(f"SERVICE_UNAVAILABLE: {operation} timed out")
        if mode == "error":
            backend.audit(operation, "fault:error")
            raise ToolError(f"SERVICE_UNAVAILABLE: {operation} failed (HTTP 503)")
        if mode == "empty":
            backend.audit(operation, "fault:empty")
            return {}
        try:
            result = fn()
        except AccessDenied as exc:
            backend.audit(operation, "denied")
            raise ToolError(str(exc)) from exc
        except BackendError as exc:
            backend.audit(operation, "error")
            raise ToolError(str(exc)) from exc
        backend.audit(operation, "ok")
        return result

    def meta(kind: str, scope: str) -> dict[str, Any]:
        return {"kind": kind, "scopes": [scope]}

    @server.tool(annotations=READ, meta=meta("read", "billing:read"))
    async def get_billing(policy_id: PolicyId) -> dict[str, Any]:
        """Balance due, due date and deductible for one of the member's policies."""
        return await run("get_billing", lambda: backend.get_billing(policy_id))

    @server.tool(annotations=READ, meta=meta("read", "billing:read"))
    async def list_payments(policy_id: PolicyId) -> dict[str, Any]:
        """Payments the member made on a policy."""
        return await run("list_payments", lambda: backend.list_payments(policy_id))

    @server.tool(annotations=READ, meta=meta("read", "claims:read"))
    async def get_claim(claim_id: Annotated[str, Field(description="Claim id, e.g. CLM-5003")]) -> dict[str, Any]:
        """A claim's status, amounts claimed / approved / paid, and the adjuster's note."""
        return await run("get_claim", lambda: backend.get_claim(claim_id))

    @server.tool(annotations=READ, meta=meta("read", "claims:read"))
    async def list_claims() -> dict[str, Any]:
        """The member's claims."""
        return await run("list_claims", backend.list_claims)

    @server.tool(annotations=WRITE, meta=meta("write", "cases:write"))
    async def open_case(
        subject: Annotated[str, Field(description="Short subject")],
        details: Annotated[str, Field(description="What the member needs")],
    ) -> dict[str, Any]:
        """Open a service case for a human to follow up (use when the desk cannot finish the request)."""
        return await run("open_case", lambda: backend.open_case(subject, details))

    @server.tool(annotations=WRITE, meta=meta("write", "billing:write"))
    async def make_payment(
        policy_id: PolicyId, amount: Annotated[float, Field(description="Amount")]
    ) -> dict[str, Any]:
        """Take a payment. Not available to the member desk."""
        return await run("make_payment", lambda: backend.make_payment(policy_id, amount))

    return server


class FaultsBody(BaseModel):
    faults: dict[str, dict[str, Any]] = Field(
        default_factory=dict, description='e.g. {"get_billing": {"mode": "timeout", "times": 1}}'
    )


class Mode(BaseModel):
    mode: Literal["error", "timeout", "empty"]
    times: int | None = None


def create_app(settings: Settings | None = None) -> Any:
    """The ASGI app: /mcp goes to the identity router, everything else to a small FastAPI app."""
    settings = settings or Settings()
    key = settings.signing_key(create=True)
    if not settings.db_path.exists():
        init_db(settings.db_path)
    faults = FaultBox()
    api = FastAPI(title="Harbor Mutual member desk tools", version="0.1.0")

    def require_sandbox(request: Request) -> None:
        given = request.headers.get("x-sandbox-token", "")
        if not settings.sandbox_token or not hmac.compare_digest(given, settings.sandbox_token):
            raise HTTPException(status_code=401, detail="Invalid sandbox token")

    @api.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @api.get("/identity/{member}")
    async def issue_identity(member: str, x_desk_proof: str = Header(default="")) -> dict[str, str]:
        if not hmac.compare_digest(x_desk_proof, proof(key, member)):
            raise HTTPException(status_code=401, detail="missing or invalid X-Desk-Proof")
        perms = member_permissions(settings.db_path, member)
        if perms is None:
            raise HTTPException(status_code=404, detail=f"unknown member {member}")
        return {"identity": idmod.issue(member, perms, key, agent="triage")}

    @api.get("/sandbox/status", dependencies=[Depends(require_sandbox)])
    async def status() -> dict[str, Any]:
        # what decides behaviour besides the deployment, so a test run can tell a build change from a deployment one
        return {"sandbox": True, "today": settings.today.isoformat(), "faults": faults.faults} | fingerprints()

    @api.post("/sandbox/faults", dependencies=[Depends(require_sandbox)])
    async def set_faults(body: FaultsBody) -> dict[str, Any]:
        try:
            checked = {tool: Mode.model_validate(v).model_dump() for tool, v in body.faults.items()}
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors(include_url=False)) from exc
        faults.set(checked)
        return {"faults": faults.faults}

    @api.post("/sandbox/reset", dependencies=[Depends(require_sandbox)])
    async def reset() -> dict[str, str]:
        init_db(settings.db_path)
        faults.set({})
        return {"status": "reset"}

    return IdentityRouter(settings, key, faults, api)


class IdentityRouter:
    def __init__(self, settings: Settings, key: str, faults: FaultBox, api: Any) -> None:
        self.settings, self.key, self.faults, self.api = settings, key, faults, api
        self.apps: OrderedDict[str, Any] = OrderedDict()
        self.lock = anyio.Lock()
        self.tg: Any = None
        self.stopping = anyio.Event()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/mcp"):
            await self.api(scope, receive, send)
            return
        token = dict(scope.get("headers") or []).get(b"x-identity", b"").decode()
        try:
            claims = idmod.verify(token, self.key)
            ident = Identity(str(claims["sub"]), frozenset(claims["permissions"]), tuple(claims["chain"]))
            app = await self._app_for(token, ident)
        except (idmod.IdentityError, BackendError) as exc:
            await _drain(receive)
            await _plain(send, 403, str(exc))
            return
        await app(scope, receive, send)

    async def _app_for(self, token: str, ident: Identity) -> Any:
        key = hashlib.sha256(token.encode()).hexdigest()
        async with self.lock:
            if key not in self.apps:
                s = self.settings
                server = build_server(DeskBackend(s.db_path, ident, s.today), self.faults, s.fault_delay_s)
                security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=True, allowed_hosts=s.allowed_hosts
                )
                app = server.streamable_http_app(transport_security=security)
                ready = anyio.Event()

                async def own_sessions() -> None:
                    async with server.session_manager.run():
                        ready.set()
                        await self.stopping.wait()

                self.tg.start_soon(own_sessions)
                await ready.wait()
                self.apps[key] = app
                while len(self.apps) > MAX_SERVERS:
                    self.apps.popitem(last=False)
            return self.apps[key]

    async def _lifespan(self, receive: Any, send: Any) -> None:
        async with anyio.create_task_group() as tg:
            self.tg = tg
            while True:
                msg = await receive()
                if msg["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg["type"] == "lifespan.shutdown":
                    self.stopping.set()
                    break
        await send({"type": "lifespan.shutdown.complete"})


async def _drain(receive: Any) -> None:
    while True:
        msg = await receive()
        if msg["type"] != "http.request" or not msg.get("more_body"):
            return


async def _plain(send: Any, status: int, text: str) -> None:
    body = text.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    p = argparse.ArgumentParser(description="Harbor Mutual member desk tools (MCP + identity + faults)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9300)
    args = p.parse_args(argv)
    app = create_app()
    print(f"Harbor Mutual member desk tools on http://{args.host}:{args.port}/mcp", flush=True)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning", lifespan="on")


if __name__ == "__main__":
    main()
