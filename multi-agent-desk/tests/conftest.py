from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from desk import identity as idmod
from desk.agents import DeskAgent, McpTools
from desk.backend import DeskBackend, Identity, init_db
from desk.config import Settings
from desk.policy import DeskRules
from desk.tools_server import FaultBox, build_server


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.delenv("DESK_SIGNING_KEY", raising=False)
    s = Settings()
    s.data_dir, s.today, s.sandbox_token, s.fault_delay_s, s.max_steps = tmp_path / "data", date(2026, 11, 3), "", 0, 8
    s.signing_key_env = ""
    s.signing_key(create=True)
    init_db(s.db_path)
    return s


@pytest.fixture
def faults() -> FaultBox:
    return FaultBox()


@pytest.fixture
def wiring(settings: Settings, faults: FaultBox) -> dict[str, Any]:
    """In-process connector and handoff: the real MCP server and the real agents, no network."""
    llm = DeskRules()

    @asynccontextmanager
    async def connect(token: str) -> AsyncIterator[McpTools]:
        c = idmod.verify(token, settings.signing_key())
        ident = Identity(c["sub"], frozenset(c["permissions"]), tuple(c["chain"]))
        async with Client(build_server(DeskBackend(settings.db_path, ident, settings.today), faults, 0)) as client:
            yield McpTools(client)

    async def handoff(target: str, task: str, token: str) -> dict[str, Any]:
        return await DeskAgent(target, llm, settings, connect, handoff).run(task, token)  # type: ignore[arg-type]

    def token_for(member: str) -> str:
        from desk.backend import member_permissions

        return idmod.issue(member, member_permissions(settings.db_path, member) or [], settings.signing_key())

    return {"llm": llm, "connect": connect, "handoff": handoff, "token_for": token_for}
