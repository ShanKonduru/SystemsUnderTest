"""Factory that wires an MCP connection, the Claude client and the engine together."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from insurance_copilot.config import Settings
from insurance_copilot.core.engine import ConfirmFn, CopilotSession, EventFn
from insurance_copilot.core.llm import LLM, make_llm
from insurance_copilot.core.toolbox import connect_toolbox


@asynccontextmanager
async def open_session(
    *,
    customer_id: str,
    confirm: ConfirmFn,
    on_event: EventFn | None = None,
    settings: Settings | None = None,
    llm: LLM | None = None,
) -> AsyncIterator[CopilotSession]:
    settings = settings or Settings()
    async with connect_toolbox(settings, customer_id) as toolbox:
        yield CopilotSession(
            llm=llm or make_llm(settings),
            toolbox=toolbox,
            settings=settings,
            customer_id=customer_id,
            confirm=confirm,
            on_event=on_event,
        )
