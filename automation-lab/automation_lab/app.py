"""Harbor Mutual Automation Lab: the page and its API.

    uvicorn automation_lab.app:app --port 8400        # http://127.0.0.1:8400   (no login)

Page:  GET /  (?release=v1|v2|v3|v4 overrides the default UI release for that page load; ?drift=N renames ids,
       names and classes every N seconds while the page is open, 0 = off)

API (JSON; validation errors are 422 {"detail", "errors": {field: message}}):
  GET    /api/config                      release, releases, rules, drift_seconds, today, upload limit, page defects
  GET    /api/locator-map?release=v2      every authored element's id, name, testid, tag, role, accessible name and
                                          text in that release: the ground truth for scoring heals
  GET    /api/policies                    ?q=&status=&product=a,b&sort=&dir=asc|desc&page=1&size=10
  GET    /api/policies/{number}
  GET    /api/policies.csv                ?ids=POL-1001,POL-1002 (all when omitted); a file download
  POST   /api/quotes        GET /api/quotes
  POST   /api/claims        GET /api/claims
  POST   /api/uploads                     multipart: files (one or more), area
  GET    /api/uploads       GET /api/uploads/{id} (download)       DELETE /api/uploads/{id}
  GET    /api/board         PUT /api/board/cards/{id}   {"column", "position"}
  GET    /api/activity?after=<seq>        POST /api/activity   {"kind", "summary", "detail"} (page events)
  GET    /api/feed?offset=&limit=         notices for the infinite-scroll list
  GET    /api/slow?ms=                    answers after a delay (max 10 s)

Test control (header X-Sandbox-Token = LAB_SANDBOX_TOKEN; with no token set every request is refused):
  GET  /sandbox/status      POST /sandbox/reset      POST /sandbox/release {"release": "v2"}
  POST /sandbox/drift {"seconds": 10}   (0 = off; 2-300)
"""

from __future__ import annotations

import asyncio
import csv
import hmac
import io
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from automation_lab import __version__
from automation_lab.config import RELEASES, Settings, drift_seconds
from automation_lab.defects import UI_DEFECTS
from automation_lab.domain import PRODUCTS, price_quote, validate_claim, validate_quote
from automation_lab.locators import locator_map
from automation_lab.locators import rules as release_rules
from automation_lab.store import Store

STATIC = Path(__file__).parent / "static"
ALLOWED_TYPES = {
    ".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".txt": "text/plain", ".csv": "text/csv",
}
AREAS = {"documents", "dropzone", "photo", "claim"}
SORTABLE = {"policy_number", "holder", "product", "status", "premium", "renewal_date"}
FEED_SIZE = 60
NOTICE_TOPICS = ["Storm watch", "Renewal reminder", "Claim update", "Billing notice", "Safety tip", "Agent message"]


def _size(n: int) -> str:
    return f"{n / (1024 * 1024):g} MB" if n >= 1024 * 1024 else f"{n / 1024:g} KB"


class Invalid(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__("validation failed")
        self.errors = errors


class MoveBody(BaseModel):
    column: str
    position: int = Field(ge=0)


class ActivityBody(BaseModel):
    kind: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=200)
    detail: dict[str, Any] = Field(default_factory=dict)


class ReleaseBody(BaseModel):
    release: str


class DriftBody(BaseModel):
    seconds: int


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    store = Store(settings.db_path, settings.uploads_dir, settings.today)
    state: dict[str, Any] = {"release": settings.release, "drift": settings.drift_seconds}
    app = FastAPI(title="Harbor Mutual Automation Lab", version=__version__)
    app.state.settings, app.state.store = settings, store

    @app.exception_handler(Invalid)
    async def invalid(_: Request, exc: Invalid) -> JSONResponse:
        return JSONResponse({"detail": "Please correct the highlighted fields.", "errors": exc.errors}, 422)

    def require_sandbox(request: Request) -> None:
        token = settings.sandbox_token
        given = request.headers.get("x-sandbox-token", "")
        if not token or not hmac.compare_digest(given, token):
            raise HTTPException(status_code=401, detail="Invalid sandbox token")

    # ------------------------------------------------------------------ page
    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "app": "automation-lab", "version": __version__, "release": state["release"],
                "drift_seconds": state["drift"]}

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {
            "release": state["release"],
            "releases": RELEASES,
            "rules": release_rules(),
            "drift_seconds": state["drift"],
            "today": settings.today.isoformat(),
            "max_upload_bytes": settings.max_upload_bytes,
            "upload_types": sorted(ALLOWED_TYPES),
            "ui_defects": sorted(settings.defects & UI_DEFECTS),
        }

    @app.get("/api/locator-map")
    async def get_locator_map(release: str = "") -> dict[str, Any]:
        try:
            return locator_map(release or state["release"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------------ policies
    @app.get("/api/policies")
    async def policies(
        q: str = "", status: str = "", product: str = "", sort: str = "policy_number", dir: str = "asc",
        page: int = 1, size: int = 10,
    ) -> dict[str, Any]:
        if sort not in SORTABLE or dir not in {"asc", "desc"}:
            raise HTTPException(400, "Unknown sort")
        size = max(1, min(size, 100))
        rows = store.policies()
        needle = q.strip().lower()
        if needle:
            rows = [r for r in rows if needle in r["policy_number"].lower() or needle in r["holder"].lower()]
        if status:
            rows = [r for r in rows if r["status"] == status]
        products = {p for p in product.split(",") if p}
        if products:
            rows = [r for r in rows if r["product"] in products]
        rows.sort(key=lambda r: (r[sort], r["policy_number"]), reverse=dir == "desc")
        pages = max(1, -(-len(rows) // size))
        page = max(1, min(page, pages))
        if settings.latency_ms:
            await asyncio.sleep(settings.latency_ms / 1000)
        return {"total": len(rows), "page": page, "pages": pages, "size": size,
                "items": rows[(page - 1) * size: page * size]}

    @app.get("/api/policies.csv")
    async def policies_csv(ids: str = "") -> Response:
        wanted = {i.strip().upper() for i in ids.split(",") if i.strip()}
        rows = [r for r in store.policies() if not wanted or r["policy_number"] in wanted]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=["policy_number", "holder", "product", "status", "premium",
                                                 "start_date", "renewal_date", "zip"], lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        store.log("server", "export", f"Exported {len(rows)} policies to CSV", {"count": len(rows)})
        name = "harbor-mutual-policies.csv" if not wanted else "harbor-mutual-policies-selected.csv"
        return Response(out.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @app.get("/api/policies/{number}")
    async def policy(number: str) -> dict[str, Any]:
        found = store.policy_map().get(number.upper())
        if found is None:
            raise HTTPException(404, f"No policy {number}")
        return found

    # ------------------------------------------------------------------ quotes
    @app.post("/api/quotes", status_code=201)
    async def create_quote(body: dict[str, Any]) -> dict[str, Any]:
        errors = validate_quote(body, settings.today)
        if errors:
            raise Invalid(errors)
        result = price_quote(body, settings.today, ignore_multi_policy="discount_ignored" in settings.defects)
        quote_id = store.add_quote(body, result)
        store.log("server", "quote", f"Quote {quote_id}: {PRODUCTS[body['product']]}, "
                  f"${result['annual_premium']:,.2f}/year", {"quote_id": quote_id, **result})
        return {"quote_id": quote_id, **result}

    @app.get("/api/quotes")
    async def list_quotes() -> list[dict[str, Any]]:
        return store.quotes()

    # ------------------------------------------------------------------ claims
    @app.post("/api/claims", status_code=201)
    async def create_claim(body: dict[str, Any]) -> dict[str, Any]:
        policies = store.policy_map()
        errors = validate_claim(body, settings.today, policies, {u["id"] for u in store.uploads()})
        if errors:
            raise Invalid(errors)
        claim_id = store.next_claim_id()
        if "phantom_claim" not in settings.defects:
            store.add_claim(claim_id, {**body, "policy_number": body["policy_number"].strip().upper()})
        store.log("server", "claim", f"Claim {claim_id} filed on {body['policy_number'].strip().upper()}",
                  {"claim_id": claim_id, "incident_type": body["incident_type"]})
        return {"claim_id": claim_id, "status": "received"}

    @app.get("/api/claims")
    async def list_claims() -> list[dict[str, Any]]:
        return store.claims()

    # ------------------------------------------------------------------ uploads
    @app.post("/api/uploads", status_code=201)
    async def upload(
        files: Annotated[list[UploadFile], File()], area: Annotated[str, Form()] = "documents"
    ) -> dict[str, Any]:
        if area not in AREAS:
            raise HTTPException(400, f"Unknown area {area!r}")
        stored, rejected = [], []
        for i, f in enumerate(files):
            name = f.filename or "file"
            suffix = Path(name).suffix.lower()
            content = await f.read()
            if suffix not in ALLOWED_TYPES:
                rejected.append({"name": name, "reason": f"{suffix or 'This file type'} isn't allowed"})
            elif not content:
                rejected.append({"name": name, "reason": "The file is empty"})
            elif len(content) > settings.max_upload_bytes:
                rejected.append({"name": name, "reason": f"The file is larger than {_size(settings.max_upload_bytes)}"})
            else:
                drop = "upload_drops_last" in settings.defects and len(files) > 1 and i == len(files) - 1
                stored.append(store.add_upload(name, content, ALLOWED_TYPES[suffix], area, store=not drop))
        if stored:
            store.log("server", "upload", f"Uploaded {len(stored)} file(s) to {area}",
                      {"area": area, "ids": [s["id"] for s in stored], "rejected": len(rejected)})
        return {"uploaded": stored, "rejected": rejected}

    @app.get("/api/uploads")
    async def list_uploads() -> list[dict[str, Any]]:
        return store.uploads()

    @app.get("/api/uploads/{upload_id}")
    async def download(upload_id: str) -> FileResponse:
        found = store.upload(upload_id)
        if found is None or not found[1].is_file():
            raise HTTPException(404, f"No upload {upload_id}")
        row, path = found
        return FileResponse(path, media_type=row["content_type"], filename=row["name"])

    @app.delete("/api/uploads/{upload_id}")
    async def delete_upload(upload_id: str) -> dict[str, str]:
        if not store.delete_upload(upload_id):
            raise HTTPException(404, f"No upload {upload_id}")
        store.log("server", "upload_deleted", f"Deleted upload {upload_id}", {"id": upload_id})
        return {"status": "deleted", "id": upload_id}

    # ------------------------------------------------------------------ claims board
    @app.get("/api/board")
    async def board() -> list[dict[str, Any]]:
        return store.cards()

    @app.put("/api/board/cards/{card_id}")
    async def move(card_id: str, body: MoveBody) -> dict[str, Any]:
        if "board_not_saved" in settings.defects:
            current = next((c for c in store.cards() if c["id"] == card_id), None)
            if current is None:
                raise HTTPException(404, f"No card {card_id}")
            return {"id": card_id, "column": body.column, "position": body.position, "from": current["column"]}
        try:
            moved = store.move_card(card_id, body.column, body.position)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if moved is None:
            raise HTTPException(404, f"No card {card_id}")
        store.log("server", "board_move", f"Moved {card_id} from {moved['from']} to {moved['column']}", moved)
        return moved

    # ------------------------------------------------------------------ activity, feed, waits
    @app.get("/api/activity")
    async def activity(after: int = 0) -> list[dict[str, Any]]:
        return store.activity(after)

    @app.post("/api/activity", status_code=201)
    async def page_event(body: ActivityBody) -> dict[str, int]:
        return {"seq": store.log("page", body.kind, body.summary, body.detail)}

    @app.get("/api/feed")
    async def feed(offset: int = 0, limit: int = 10) -> dict[str, Any]:
        offset, limit = max(0, offset), max(1, min(limit, 20))
        items = [{"id": f"N-{n + 1:03d}", "topic": NOTICE_TOPICS[n % len(NOTICE_TOPICS)],
                  "text": f"Notice {n + 1} of {FEED_SIZE} for Harbor Mutual members."}
                 for n in range(offset, min(offset + limit, FEED_SIZE))]
        if settings.latency_ms:
            await asyncio.sleep(settings.latency_ms / 1000)
        return {"items": items, "next": offset + len(items) if offset + len(items) < FEED_SIZE else None}

    @app.get("/api/slow")
    async def slow(ms: int = 2000) -> dict[str, Any]:
        ms = max(0, min(ms, 10_000))
        await asyncio.sleep(ms / 1000)
        return {"status": "done", "waited_ms": ms}

    # ------------------------------------------------------------------ test control
    @app.get("/sandbox/status")
    async def sandbox_status(request: Request) -> dict[str, Any]:
        require_sandbox(request)
        return {"sandbox": True, "today": settings.today.isoformat(), "release": state["release"],
                "drift_seconds": state["drift"], "defects": sorted(settings.defects), "counts": store.counts()}

    @app.post("/sandbox/reset")
    async def sandbox_reset(request: Request) -> dict[str, str]:
        require_sandbox(request)
        store.reset()
        state["release"], state["drift"] = settings.release, settings.drift_seconds
        return {"status": "reset", "release": state["release"]}

    @app.post("/sandbox/release")
    async def sandbox_release(request: Request, body: ReleaseBody) -> dict[str, str]:
        require_sandbox(request)
        if body.release not in RELEASES:
            raise HTTPException(400, f"Unknown release {body.release!r}; known: {', '.join(RELEASES)}")
        state["release"] = body.release
        store.log("server", "release", f"Default UI release switched to {body.release}", {"release": body.release})
        return {"status": "ok", "release": body.release}

    @app.post("/sandbox/drift")
    async def sandbox_drift(request: Request, body: DriftBody) -> dict[str, Any]:
        require_sandbox(request)
        try:
            state["drift"] = drift_seconds(body.seconds)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        summary = f"Live drift every {state['drift']} s" if state["drift"] else "Live drift switched off"
        store.log("server", "drift", summary, {"seconds": state["drift"]})
        return {"status": "ok", "drift_seconds": state["drift"]}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
