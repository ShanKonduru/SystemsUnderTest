"""The insurance MCP server over streamable HTTP, for every customer from one process.

    python -m insurance_copilot.mcp_server --http 9000 [--host 127.0.0.1]

The app connects to http://host:port/mcp and names the signed-in customer in the X-Customer-Id header of every
request; each customer gets their own tool server instance (bound to that customer, exactly as over stdio).
COPILOT_DB and COPILOT_TODAY come from the environment. COPILOT_MCP_ALLOWED_HOSTS (comma-separated, default
"localhost:*,127.0.0.1:*,chat-tools:*") lists the Host headers the server accepts (DNS-rebinding protection), so a
recording proxy in another container can reach it as chat-tools:9000.

Sandbox/demo only: the server trusts the X-Customer-Id header, so it must only be reachable by the app (it binds to
127.0.0.1 by default). A production deployment would authenticate the app and derive the customer from a verified
token instead.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import anyio

from insurance_copilot.backend.db import BackendError, InsuranceBackend
from insurance_copilot.mcp_server.server import build_server

CUSTOMER_HEADER = b"x-customer-id"


def allowed_hosts() -> list[str]:
    raw = os.environ.get("COPILOT_MCP_ALLOWED_HOSTS", "localhost:*,127.0.0.1:*,chat-tools:*")
    return [h.strip() for h in raw.split(",") if h.strip()]


class CustomerRouter:
    """ASGI app: routes each request to the tool server bound to the customer in its X-Customer-Id header."""

    def __init__(self, db_path: Path | str, today: date | None):
        self.db_path, self.today = db_path, today
        self.apps: dict[str, Any] = {}
        self.lock = anyio.Lock()
        self.tg: Any = None
        self.stopping = anyio.Event()

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        customer = dict(scope.get("headers") or []).get(CUSTOMER_HEADER, b"").decode()
        if not customer:
            await _drain(receive)
            await _plain(send, 400, "missing X-Customer-Id header")
            return
        try:
            app = await self._app_for(customer)
        except BackendError as exc:
            await _drain(receive)
            await _plain(send, 403, str(exc))
            return
        await app(scope, receive, send)

    async def _app_for(self, customer: str) -> Any:
        async with self.lock:
            if customer not in self.apps:
                server = build_server(InsuranceBackend(self.db_path, customer, self.today))
                from mcp.server.transport_security import TransportSecuritySettings

                security = TransportSecuritySettings(enable_dns_rebinding_protection=True, allowed_hosts=allowed_hosts())
                app = server.streamable_http_app(transport_security=security)
                ready = anyio.Event()

                async def own_sessions() -> None:   # the session manager's task group lives as long as the server
                    async with server.session_manager.run():
                        ready.set()
                        await self.stopping.wait()

                self.tg.start_soon(own_sessions)
                await ready.wait()
                self.apps[customer] = app
            return self.apps[customer]

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
    """Read the rest of a request body before refusing it, so the client isn't cut off mid-send."""
    while True:
        msg = await receive()
        if msg["type"] != "http.request" or not msg.get("more_body"):
            return


async def _plain(send: Any, status: int, text: str) -> None:
    body = text.encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


def serve(port: int, host: str = "127.0.0.1") -> None:
    import uvicorn

    db = os.environ.get("COPILOT_DB") or str(Path(__file__).resolve().parents[2] / "data" / "insurance.db")
    today = date.fromisoformat(os.environ["COPILOT_TODAY"]) if os.environ.get("COPILOT_TODAY") else None
    print(f"Insurance MCP server (HTTP) on http://{host}:{port}/mcp · {db}")
    uvicorn.run(CustomerRouter(db, today), host=host, port=port, log_level="warning", lifespan="on")
