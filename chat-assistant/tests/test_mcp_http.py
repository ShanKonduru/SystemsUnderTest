"""The MCP tool server over streamable HTTP: each customer is served from its own header, and the app connects by URL."""

import asyncio
import socket
import threading
import time
import urllib.error
import urllib.request
from datetime import date
from types import SimpleNamespace

import uvicorn

from insurance_copilot.backend.db import init_db
from insurance_copilot.core.toolbox import connect_toolbox
from insurance_copilot.mcp_server.http import CustomerRouter


def test_http_tool_server_serves_each_customer_from_its_header_and_the_app_connects_by_url(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    db = tmp_path / "s.db"
    init_db(db, date(2026, 11, 3))
    server = uvicorn.Server(
        uvicorn.Config(
            CustomerRouter(db, date(2026, 11, 3)), host="127.0.0.1", port=port, log_level="warning", lifespan="on"
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{port}/mcp"
    settings = SimpleNamespace(mcp_transport="stdio", mcp_url=url)

    async def policies(customer):
        async with connect_toolbox(settings, customer) as tb:
            assert "list_policies" in tb.tools
            return (await tb.call("list_policies", {})).text

    async def both():
        return await asyncio.gather(policies("CUST-001"), policies("CUST-002"))

    def status(headers):
        req = urllib.request.Request(url, data=b"{}", headers={"Content-Type": "application/json", **headers})
        try:
            return urllib.request.urlopen(req).status
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        shan, indu = asyncio.run(both())
        missing, unknown = status({}), status({"X-Customer-Id": "CUST-999"})
    finally:
        server.should_exit = True
    assert "POL-1001" in shan and "POL-1001" not in indu and "POL-2001" in indu  # each sees only their own
    assert (missing, unknown) == (400, 403)
