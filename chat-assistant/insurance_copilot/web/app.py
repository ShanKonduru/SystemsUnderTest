"""Web chat with login and saved conversations. Same engine as the CLI.

    uvicorn insurance_copilot.web.app:app --reload
    open http://127.0.0.1:8000      (customers: shan / indu / sri; representatives: rivera / chen / patel)

HTTP API (cookie session after login):
  POST   /api/login                        {"username", "password"}
  POST   /api/guest                        continue as a guest (prospective customer, no account)
  POST   /api/logout
  GET    /api/me
  GET    /api/conversations                list the user's chats (newest first)
  POST   /api/conversations                start a new chat
  GET    /api/conversations/{id}/messages  chat history
  DELETE /api/conversations/{id}
  GET    /api/conversations/{id}/traces    5-stage traces per turn (for the testing agent)

WebSocket /ws?conversation=<id>:
  client -> server: {"type": "user", "text": "..."} | {"type": "confirm", "key": "...", "approved": bool}
  server -> client: {"type": <event>, "data": {...}}   (ready, awaiting_confirmation, assistant_message, ...)
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from insurance_copilot.backend.db import MAX_DOCUMENT_BYTES, BackendError, InsuranceBackend, init_db
from insurance_copilot.config import Settings
from insurance_copilot.core.attachments import compose_user_message
from insurance_copilot.core.session import open_session
from insurance_copilot.core.trace import ConfirmationRequest, Event
from insurance_copilot.web.store import SESSION_HOURS, AppStore

log = logging.getLogger("insurance_copilot.web")
STATIC = Path(__file__).parent / "static"
TURN_FINISH_TIMEOUT = 180  # seconds to let an in-flight turn finish (and be saved) after a disconnect

settings = Settings()
# Browsers share cookies across ports on the same host, so the Docker app (localhost:8000) and the
# dev server (localhost:8001) would overwrite each other's login. A name tied to this deployment's
# database keeps the sessions apart.
COOKIE = "copilot_session_" + hashlib.sha256(str(settings.db_path.resolve()).encode()).hexdigest()[:8]
store = AppStore(settings.db_path)
if not store.has_app_schema():  # new install, or a database from before logins existed: seed fresh demo data
    init_db(settings.db_path, settings.today, queue=True)
store.ensure_schema()

app = FastAPI(title="Harbor Mutual assistant")


# ------------------------------------------------------------------ auth
class LoginBody(BaseModel):
    username: str
    password: str


def current_user(request: Request) -> dict[str, Any]:
    user = store.user_for_token(request.cookies.get(COOKIE))
    if user is None:
        raise HTTPException(status_code=401, detail="Not signed in")
    return user


def owned_conversation(conversation_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
    conv = store.get_conversation(user["username"], conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ------------------------------------------------------------------ sandbox control (test harness)
def require_sandbox(request: Request) -> None:
    """Sandbox endpoints exist only in sandbox mode and need the sandbox token."""
    if not settings.sandbox_mode:
        raise HTTPException(status_code=404, detail="Not found")
    if not settings.sandbox_token or request.headers.get("x-sandbox-token") != settings.sandbox_token:
        raise HTTPException(status_code=401, detail="Invalid sandbox token")


def _fingerprint() -> dict[str, Any]:
    """Versions a harness needs to tell whether two runs are comparable."""
    from insurance_copilot.core import prompts
    from insurance_copilot.mcp_server import server as tool_server

    def short_hash(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()[:12]

    return {
        **_llm_identity(),
        # Customer-facing prompts only, so the fingerprint (and harness comparability) ignores representative ones.
        "prompts": short_hash(prompts.ROUTER_SYSTEM + prompts.PLANNER_SYSTEM + prompts.EXECUTOR_SYSTEM),
        "tools": short_hash(Path(tool_server.__file__).read_text(encoding="utf-8") + _rest_api_source()),
    }


def _llm_identity() -> dict[str, str]:
    """Provider and model from the LLM factory (no keys or endpoints); 'unconfigured' with the reason if it fails."""
    from insurance_copilot.core.llm import describe, make_llm

    try:
        return describe(make_llm(settings))
    except Exception as exc:  # misconfigured .env must not break the status endpoint
        return {"provider": "unconfigured", "model": str(exc)[:200]}


def _rest_api_source() -> str:
    """In REST mode the REST layer is part of what is tested, so it is part of the tools fingerprint."""
    if not settings.rest_url:
        return ""
    from insurance_copilot import rest_api

    return Path(rest_api.__file__).read_text(encoding="utf-8")


@app.get("/sandbox/status", dependencies=[Depends(require_sandbox)])
async def sandbox_status() -> dict[str, Any]:
    return {
        "sandbox": True,
        "clock": settings.today.isoformat(),
        "versions": _fingerprint(),
        "toggles": {"traces": True, "tool_proxy": bool(settings.mcp_command) or settings.tool_proxy, "thinking": False,
                    "txn_policy": settings.txn_policy},
        # how the app reaches its tools: "rest" (REST API), "http" (MCP streamable HTTP) or "stdio"/"inprocess" (MCP)
        "transport": _transport(),
        "mcp_transport": _transport(),     # older name of the same field, kept for existing harness versions
    }


def _transport() -> str:
    if settings.rest_url:
        return "rest"
    return "http" if settings.mcp_url else settings.mcp_transport


@app.post("/sandbox/reset", dependencies=[Depends(require_sandbox)])
async def sandbox_reset() -> dict[str, str]:
    init_db(settings.db_path, settings.today, queue=True)
    store.ensure_schema()
    return {"status": "reset", "clock": settings.today.isoformat()}


@app.post("/api/login")
async def login(body: LoginBody, response: Response) -> dict[str, Any]:
    user = store.authenticate(body.username, body.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    response.set_cookie(COOKIE, store.create_session(user["username"]), httponly=True, samesite="lax",
                        max_age=SESSION_HOURS * 3600)
    return user


@app.post("/api/guest")
async def guest(response: Response) -> dict[str, Any]:
    user = store.create_guest()
    response.set_cookie(COOKIE, store.create_session(user["username"]), httponly=True, samesite="lax",
                        max_age=SESSION_HOURS * 3600)
    return user


@app.post("/api/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    store.delete_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE)
    return {"status": "signed out"}


@app.get("/api/me")
async def me(user: dict = Depends(current_user)) -> dict[str, Any]:
    return user


# ------------------------------------------------------------------ conversations
@app.get("/api/conversations")
async def list_conversations(user: dict = Depends(current_user)) -> list[dict[str, Any]]:
    return store.list_conversations(user["username"])


@app.post("/api/conversations")
async def new_conversation(user: dict = Depends(current_user)) -> dict[str, Any]:
    return store.create_conversation(user["username"])


@app.get("/api/conversations/{conversation_id}/messages")
async def conversation_messages(conv: dict = Depends(owned_conversation)) -> list[dict[str, Any]]:
    return store.messages(conv["id"])


@app.get("/api/conversations/{conversation_id}/traces")
async def conversation_traces(conv: dict = Depends(owned_conversation)) -> list[dict[str, Any]]:
    return store.traces(conv["id"])


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conv: dict = Depends(owned_conversation),
                              user: dict = Depends(current_user)) -> dict[str, str]:
    store.delete_conversation(user["username"], conv["id"])
    return {"status": "deleted"}


# ------------------------------------------------------------------ proof documents
def backend_for(user: dict[str, Any]) -> InsuranceBackend:
    """Customers: their own account. Representatives: the customer they have verified and opened, if any."""
    return InsuranceBackend(settings.db_path, user["customer_id"], settings.today)


def _doc_summary(doc: dict[str, Any]) -> dict[str, Any]:
    return {"document_id": doc.get("document_id") or doc["id"], "filename": doc["filename"],
            "content_type": doc["content_type"], "size_bytes": doc["size_bytes"]}


@app.post("/api/conversations/{conversation_id}/documents")
async def upload_document(request: Request, filename: str, conv: dict = Depends(owned_conversation),
                          user: dict = Depends(current_user)) -> dict[str, Any]:
    """Raw file body; Content-Type header gives the file type. The proof is stored in the database."""
    body = await request.body()
    if len(body) > MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Files can be at most 5 MB")
    try:
        return backend_for(user).store_document(filename, request.headers.get("content-type", ""), body,
                                                conversation_id=conv["id"])
    except BackendError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.get("/api/documents/{document_id}")
async def download_document(document_id: str, user: dict = Depends(current_user)) -> Response:
    try:
        doc = backend_for(user).get_document(document_id)
    except BackendError:  # not found or someone else's: same answer
        raise HTTPException(status_code=404, detail="Document not found") from None
    return Response(doc["content"], media_type=doc["content_type"],
                    headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'})


# ------------------------------------------------------------------ chat
@app.websocket("/ws")
async def ws_chat(ws: WebSocket) -> None:
    user = store.user_for_token(ws.cookies.get(COOKIE))
    conv = store.get_conversation(user["username"], ws.query_params.get("conversation", "")) if user else None
    if user is None or conv is None:
        await ws.close(code=4401 if user is None else 4404)
        return
    await ws.accept()

    conv_id = conv["id"]
    pending: dict[str, asyncio.Future[bool]] = {}
    counter = [0]
    connected = [True]
    send_lock = asyncio.Lock()

    async def send(event_type: str, data: dict[str, Any]) -> None:
        if not connected[0]:
            return  # browser gone: the turn keeps running and is still saved
        try:
            async with send_lock:
                await ws.send_json({"type": event_type, "data": data})
        except Exception:  # closed mid-send
            connected[0] = False

    async def on_event(event: Event) -> None:
        if event.type in ("awaiting_confirmation", "confirmation_result", "assistant_message", "error"):
            await send(event.type, event.data)
        elif event.type == "tool_call":
            await send("status", {"text": f"Working on it: {event.data['name'].replace('_', ' ')}…"})

    async def confirm(req: ConfirmationRequest) -> bool:
        if not connected[0]:
            return False  # nobody to approve it
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        counter[0] += 1
        key = f"confirm-{counter[0]}"
        pending[key] = fut
        await send("awaiting_confirmation", {"key": key, "summary": req.summary(), "destructive": req.destructive,
                                             "level": req.level, "effect": req.effect,
                                             "disclosure": req.disclosure})
        try:
            return await fut
        finally:
            pending.pop(key, None)

    async with open_session(customer_id=user["customer_id"], confirm=confirm, on_event=on_event,
                            settings=settings) as session:
        session.restore(conv["engine_state"])

        async def run_turn(text: str) -> None:
            store.add_message(conv_id, "user", text)
            trace = await session.send(text)
            store.add_message(conv_id, "assistant", trace.reply)
            store.save_turn(conv_id, session.export_state(), trace.to_dict())
            await send("turn_completed", {"turn": trace.turn})

        turn_task: asyncio.Task | None = None
        try:
            await send("ready", {"conversation_id": conv_id, "display_name": user["display_name"]})
            while True:
                msg = await ws.receive_json()
                if msg.get("type") == "user" and (msg.get("text", "").strip() or msg.get("attachments")):
                    if turn_task and not turn_task.done():
                        await send("error", {"message": "Please wait for the current reply."})
                        continue
                    try:  # attachments must be this customer's own uploaded documents
                        docs = [_doc_summary(backend_for(user).get_document(d)) for d in msg.get("attachments", [])]
                    except BackendError as exc:
                        await send("error", {"message": str(exc)})
                        continue
                    turn_task = asyncio.create_task(run_turn(compose_user_message(msg.get("text", ""), docs)))
                elif msg.get("type") == "confirm":
                    fut = pending.get(msg.get("key", ""))
                    if fut and not fut.done():
                        fut.set_result(bool(msg.get("approved")))
        except WebSocketDisconnect:
            pass
        except Exception:
            log.exception("websocket loop failed (conversation %s)", conv_id)
        finally:
            connected[0] = False
            for fut in pending.values():  # an unanswered approval counts as declined
                if not fut.done():
                    fut.set_result(False)
            if turn_task and not turn_task.done():
                # Let the turn finish so its reply and state are saved; the user sees it on reconnect.
                with contextlib.suppress(BaseException):
                    await asyncio.wait_for(turn_task, TURN_FINISH_TIMEOUT)
