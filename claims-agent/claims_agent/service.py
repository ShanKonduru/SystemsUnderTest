"""HTTP service: back-office systems post events; the agent handles each one and returns its account of it.

POST /events           {"event": {"type": "claim.document_received", "claim_id": "CLM-5003", ...}}
                       header X-Principal: adjuster-07   -> the episode (route, steps, output, done)
GET  /episodes/{id}    a past episode
GET  /healthz          liveness, plus which LLM provider/model is configured
GET  /sandbox/status   versions a test run needs to compare runs        (needs X-Sandbox-Token)
POST /sandbox/reset    restore the seeded claims database and letters  (needs X-Sandbox-Token)
"""

from __future__ import annotations

import hashlib
import hmac
from collections import OrderedDict
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from claims_agent import agent as agent_mod
from claims_agent.agent import ClaimsAgent, Connect
from claims_agent.backend import init_db
from claims_agent.config import Settings

APP_PROVIDER_VAR = "CLAIMS_LLM_PROVIDER"
MAX_EPISODES = 500


class EventBody(BaseModel):
    event: dict[str, Any] = Field(description="The back-office event; must have a 'type'")


def _llm_identity(llm: Any) -> dict[str, str]:
    return {"provider": str(getattr(llm, "provider", "?")), "model": str(getattr(llm, "model", "?"))}


def create_app(settings: Settings | None = None, llm: Any = None, connect: Connect | None = None) -> FastAPI:
    settings = settings or Settings()
    app = FastAPI(title="Harbor Mutual claims agent", version="0.1.0")
    episodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    state: dict[str, Any] = {"llm": llm}

    def get_llm() -> Any:
        if state["llm"] is None:
            from harbor_llm import make_llm

            state["llm"] = make_llm(override_var=APP_PROVIDER_VAR)
        return state["llm"]

    def require_sandbox(request: Request) -> None:
        token = settings.sandbox_token
        given = request.headers.get("x-sandbox-token", "")
        if not token or not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="Invalid sandbox token")

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        try:
            identity = _llm_identity(get_llm())
        except Exception as exc:  # a misconfigured .env must not break liveness
            identity = {"provider": "unconfigured", "model": str(exc)[:200]}
        return {"status": "ok", "llm": identity}

    @app.post("/events")
    async def post_event(body: EventBody, x_principal: str = Header(default="")) -> dict[str, Any]:
        if not x_principal:
            raise HTTPException(status_code=400, detail="missing X-Principal header")
        if not body.event.get("type"):
            raise HTTPException(status_code=422, detail="event.type is required")
        result = await ClaimsAgent(get_llm(), settings, connect).handle(body.event, x_principal)
        data = result.to_dict()
        episodes[result.episode_id] = data
        while len(episodes) > MAX_EPISODES:
            episodes.popitem(last=False)
        return data

    @app.get("/episodes/{episode_id}")
    async def get_episode(episode_id: str) -> dict[str, Any]:
        if episode_id not in episodes:
            raise HTTPException(status_code=404, detail="no such episode")
        return episodes[episode_id]

    @app.get("/sandbox/status", dependencies=[Depends(require_sandbox)])
    async def sandbox_status() -> dict[str, Any]:
        prompts = agent_mod.TRIAGE_SYSTEM + agent_mod.AGENT_SYSTEM
        tools = (Path(agent_mod.__file__).parent / "tools_server.py").read_text(encoding="utf-8")
        return {
            "sandbox": True,
            "today": settings.today.isoformat(),
            "llm": _llm_identity(get_llm()),
            "prompts": hashlib.sha256(prompts.encode()).hexdigest()[:12],
            "tools": hashlib.sha256(tools.encode()).hexdigest()[:12],
            "episodes": len(episodes),
        }

    @app.post("/sandbox/reset", dependencies=[Depends(require_sandbox)])
    async def sandbox_reset() -> dict[str, str]:
        init_db(settings.db_path, settings.letters_dir, settings.today)
        episodes.clear()
        return {"status": "reset"}

    return app


app = create_app()
