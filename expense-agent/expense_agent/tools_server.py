"""The expense tools as an MCP server, over streamable HTTP, one server instance per principal.

    python -m expense_agent.tools_server [--host 0.0.0.0] [--port 9700]

Callers name the principal in the ``X-Principal`` header; when ``EXPENSE_TOOLS_TOKEN`` is set, every request
must also carry ``Authorization: Bearer <token>``. Each tool's metadata declares whether it reads or writes and
which permission it needs (``meta.kind``, ``meta.scopes``), so a test framework can build its action registry
from the tool listing.
"""

from __future__ import annotations

import argparse
import hmac
import json
from pathlib import Path
from typing import Annotated, Any, Literal

import anyio
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from expense_agent.backend import AccessDenied, BackendError, ExpenseBackend
from expense_agent.config import Settings

READ = ToolAnnotations(read_only_hint=True)
WRITE = ToolAnnotations(read_only_hint=False, destructive_hint=False)
PRINCIPAL_HEADER = b"x-principal"

ReportId = Annotated[str, Field(description="Expense report id, e.g. ER-3001")]


def build_server(backend: ExpenseBackend) -> MCPServer:
    server = MCPServer(
        "harbor-expenses",
        instructions=f"Expense operations for {backend.principal} ({backend.role}).",
        log_level="WARNING",
    )

    def run(operation: str, fn: Any) -> Any:
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

    @server.tool(annotations=READ, meta=meta("read", "expenses:read"))
    def get_report(report_id: ReportId) -> dict[str, Any]:
        """An expense report: the employee (with status and manager), status, total, approved amount, every line
        (date, category, merchant, amount, cabin for airfare, whether a receipt is attached) and the statuses it may
        move to."""
        return run("get_report", lambda: backend.get_report(report_id))

    @server.tool(annotations=READ, meta=meta("read", "expenses:read"))
    def get_expense_policy() -> dict[str, Any]:
        """The expense policy: category limits, airfare cabin, receipt threshold, auto-approval limit and the
        submission window in days."""
        return run("get_expense_policy", backend.get_expense_policy)

    @server.tool(annotations=READ, meta=meta("read", "expenses:read"))
    def find_duplicates(report_id: ReportId) -> dict[str, Any]:
        """Lines of this report that repeat a line (same date, merchant and amount) already claimed in another,
        earlier report."""
        return run("find_duplicates", lambda: backend.find_duplicates(report_id))

    @server.tool(annotations=WRITE, meta=meta("write", "expenses:update"))
    def update_report(
        report_id: ReportId,
        status: Annotated[Literal["approved", "rejected", "returned", "escalated"], Field(description="New status")],
        reason: Annotated[str | None, Field(description="Why; recorded as a report note")] = None,
        approved_amount: Annotated[
            float | None, Field(description="Required when approving; at most the report total")
        ] = None,
    ) -> dict[str, Any]:
        """Decide a submitted report: approved, rejected, returned (to the employee) or escalated (to a manager)."""
        return run("update_report", lambda: backend.update_report(report_id, status, reason, approved_amount))

    @server.tool(annotations=WRITE, meta=meta("write", "expenses:update"))
    def add_note(report_id: ReportId, text: Annotated[str, Field(description="The note")]) -> dict[str, Any]:
        """Add an internal note to a report."""
        return run("add_note", lambda: backend.add_note(report_id, text))

    @server.tool(annotations=WRITE, meta=meta("write", "notices:write"))
    def send_notice(
        report_id: ReportId,
        kind: Annotated[
            Literal["approval", "rejection", "receipt_request", "escalation"], Field(description="Notice type")
        ],
        body: Annotated[str, Field(description="The notice text for the employee")],
    ) -> dict[str, Any]:
        """Send the employee a notice about a report (saved as a file)."""
        return run("send_notice", lambda: backend.send_notice(report_id, kind, body))

    @server.tool(annotations=WRITE, meta=meta("write", "payments:write"))
    def make_reimbursement(
        report_id: ReportId, amount: Annotated[float, Field(description="At most the approved amount")]
    ) -> dict[str, Any]:
        """Reimburse an approved report (it becomes paid). Needs the payments:write permission (finance only)."""
        return run("make_reimbursement", lambda: backend.make_reimbursement(report_id, amount))

    return server


class PrincipalRouter:
    """ASGI app: checks the bearer token, then routes to the tool server bound to the X-Principal header."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.apps: dict[str, Any] = {}
        self.lock = anyio.Lock()
        self.tg: Any = None
        self.stopping = anyio.Event()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":  # websockets are not served
            return
        headers = dict(scope.get("headers") or [])
        if scope.get("path") == "/healthz":
            await _drain(receive)
            await _plain(send, 200, json.dumps({"status": "ok"}), "application/json")
            return
        token = self.settings.tools_token
        if token and not hmac.compare_digest(headers.get(b"authorization", b"").decode(), f"Bearer {token}"):
            await _drain(receive)
            await _plain(send, 401, "missing or invalid bearer token")
            return
        principal = headers.get(PRINCIPAL_HEADER, b"").decode()
        if not principal:
            await _drain(receive)
            await _plain(send, 400, "missing X-Principal header")
            return
        try:
            app = await self._app_for(principal)
        except BackendError as exc:
            await _drain(receive)
            await _plain(send, 403, str(exc))
            return
        await app(scope, receive, send)

    async def _app_for(self, principal: str) -> Any:
        async with self.lock:
            if principal not in self.apps:
                s = self.settings
                server = build_server(ExpenseBackend(s.db_path, s.out_dir, principal, s.today, s.defects))
                security = TransportSecuritySettings(
                    enable_dns_rebinding_protection=True, allowed_hosts=s.tools_allowed_hosts
                )
                app = server.streamable_http_app(transport_security=security)
                ready = anyio.Event()

                async def own_sessions() -> None:
                    async with server.session_manager.run():
                        ready.set()
                        await self.stopping.wait()

                self.tg.start_soon(own_sessions)
                await ready.wait()
                self.apps[principal] = app
            return self.apps[principal]

    def forget(self) -> None:
        """Drop cached per-principal servers (after a reset); new ones are built on the next request."""
        self.apps.clear()

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


async def _plain(send: Any, status: int, text: str, content_type: str = "text/plain") -> None:
    body = text.encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", content_type.encode()), (b"content-length", str(len(body)).encode())],
        }
    )
    await send({"type": "http.response.body", "body": body})


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    from expense_agent.backend import init_db

    p = argparse.ArgumentParser(description="Harbor Mutual expense tools (MCP over streamable HTTP)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=9700)
    args = p.parse_args(argv)
    settings = Settings()
    if not Path(settings.db_path).exists():
        init_db(settings.db_path, settings.out_dir, settings.today)
    print(f"Harbor Mutual expense tools on http://{args.host}:{args.port}/mcp ({settings.db_path})")
    uvicorn.run(PrincipalRouter(settings), host=args.host, port=args.port, log_level="warning", lifespan="on")


if __name__ == "__main__":
    main()
