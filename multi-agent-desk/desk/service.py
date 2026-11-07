"""HTTP services for the three agents (one image; the compose file picks the app per container).

    triage   POST /requests {"message": "..."}  header X-Principal: M-1001  -> the episode
             GET  /episodes/{id}, GET /healthz
    billing  POST /tasks {"task": "..."}  header X-Identity: <token whose chain ends with billing>
    claims   POST /tasks {"task": "..."}  header X-Identity: <token whose chain ends with claims>

The triage service gets the member's first identity token from the tool server (it proves it holds the signing
key), routes the request, and runs the triage agent, which hands off to the specialists over HTTP.
"""

from __future__ import annotations

import time
import uuid
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from desk import identity as idmod
from desk.agents import TRIAGE_ROUTE_SYSTEM, Connect, DeskAgent, Handoff, Role, Triage, http_handoff, http_tools
from desk.config import Settings

APP_PROVIDER_VAR = "DESK_LLM_PROVIDER"
MAX_EPISODES = 500
IdentityFor = Callable[[str], Awaitable[str]]


class RequestBody(BaseModel):
    message: str


class TaskBody(BaseModel):
    task: str


def _lazy_llm(llm: Any) -> Callable[[], Any]:
    state = {"llm": llm}

    def get() -> Any:
        if state["llm"] is None:
            from harbor_llm import make_llm

            state["llm"] = make_llm(override_var=APP_PROVIDER_VAR)
        return state["llm"]

    return get


def _identity(llm: Any) -> dict[str, str]:
    return {"provider": str(getattr(llm, "provider", "?")), "model": str(getattr(llm, "model", "?"))}


def _health(get_llm: Callable[[], Any], role: str) -> Callable[[], Awaitable[dict[str, Any]]]:
    async def healthz() -> dict[str, Any]:
        try:
            ident = _identity(get_llm())
        except Exception as exc:  # a misconfigured .env must not break liveness
            ident = {"provider": "unconfigured", "model": str(exc)[:200]}
        return {"status": "ok", "agent": role, "llm": ident}

    return healthz


def http_identity(settings: Settings) -> IdentityFor:
    async def identity_for(member: str) -> str:
        import httpx

        from desk.tools_server import proof

        key = settings.signing_key()
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.get(f"{settings.tools_url}/identity/{member}", headers={"X-Desk-Proof": proof(key, member)})
        if r.status_code == 404:
            raise HTTPException(status_code=403, detail=f"unknown member {member}")
        r.raise_for_status()
        token: str = r.json()["identity"]
        return token

    return identity_for


def create_triage_app(
    settings: Settings | None = None,
    llm: Any = None,
    connect: Connect | None = None,
    handoff: Handoff | None = None,
    identity_for: IdentityFor | None = None,
) -> FastAPI:
    settings = settings or Settings()
    get_llm = _lazy_llm(llm)
    connect = connect or http_tools(settings)
    handoff = handoff or http_handoff(settings)
    identity_for = identity_for or http_identity(settings)
    app = FastAPI(title="Harbor Mutual member desk - triage", version="0.1.0")
    episodes: OrderedDict[str, dict[str, Any]] = OrderedDict()
    app.get("/healthz")(_health(get_llm, "triage"))

    @app.post("/requests")
    async def request(body: RequestBody, x_principal: str = Header(default="")) -> dict[str, Any]:
        if not x_principal:
            raise HTTPException(status_code=400, detail="missing X-Principal header")
        llm_ = get_llm()
        ep: dict[str, Any] = {
            "episode_id": uuid.uuid4().hex[:12],
            "request": body.message,
            "member": x_principal,
            "route": None,
            "reason": "",
            "output": "",
            "done": False,
            "status": "ok",
            "steps": [],
            "hops": [],
            "usage": {},
            "llm": _identity(llm_),
            "error": None,
            "started": time.time(),
        }
        token = await identity_for(x_principal)
        try:
            triage = await llm_.structured(system=TRIAGE_ROUTE_SYSTEM, user=body.message, schema=Triage, effort="low")
            ep["route"], ep["reason"] = triage.route, triage.reason
            if triage.route == "unsupported":
                ep.update(output="Sorry, the member desk can't help with that request.", done=True)
            else:
                run = await DeskAgent("triage", llm_, settings, connect, handoff).run(
                    f"{body.message}\nRoute: {triage.route}", token
                )
                ep.update({k: run[k] for k in ("output", "done", "status", "steps", "hops", "usage")})
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            ep.update(status="failed", done=False, error=f"{type(exc).__name__}: {exc}")
        ep["ended"] = time.time()
        episodes[ep["episode_id"]] = ep
        while len(episodes) > MAX_EPISODES:
            episodes.popitem(last=False)
        return ep

    @app.get("/episodes/{episode_id}")
    async def episode(episode_id: str) -> dict[str, Any]:
        if episode_id not in episodes:
            raise HTTPException(status_code=404, detail="no such episode")
        return episodes[episode_id]

    return app


def create_specialist_app(
    role: Role,
    settings: Settings | None = None,
    llm: Any = None,
    connect: Connect | None = None,
    handoff: Handoff | None = None,
) -> FastAPI:
    settings = settings or Settings()
    get_llm = _lazy_llm(llm)
    connect = connect or http_tools(settings)
    handoff = handoff or http_handoff(settings)
    app = FastAPI(title=f"Harbor Mutual member desk - {role}", version="0.1.0")
    app.get("/healthz")(_health(get_llm, role))

    @app.post("/tasks")
    async def task(body: TaskBody, x_identity: str = Header(default="")) -> dict[str, Any]:
        try:
            claims = idmod.verify(x_identity, settings.signing_key())
        except idmod.IdentityError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        if not claims.get("chain") or claims["chain"][-1] != role:
            raise HTTPException(status_code=403, detail=f"identity was not delegated to the {role} agent")
        try:
            return await DeskAgent(role, get_llm(), settings, connect, handoff).run(body.task, x_identity)
        except Exception as exc:
            while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
                exc = exc.exceptions[0]
            return {
                "agent": role,
                "status": "failed",
                "output": f"{role} agent error: {type(exc).__name__}: {exc}",
                "done": False,
                "steps": [],
                "hops": [],
                "usage": {},
            }

    return app


def triage_app() -> FastAPI:
    return create_triage_app()


def billing_app() -> FastAPI:
    return create_specialist_app("billing")


def claims_app() -> FastAPI:
    return create_specialist_app("claims")
