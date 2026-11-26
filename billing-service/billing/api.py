"""HTTP API. Every request is logged with a request id; unexpected errors are logged with their stack trace and
answered with a 500 that carries the same id, so a support ticket can quote it."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .config import SERVICE_ROOT, Settings, load_settings
from .errors import BillingError, Conflict, NotFound
from .money import fmt
from .payments import assess_payment, received_date
from .premiums import quote_premium
from .rates import load_rates
from .refunds import RefundQuote, premium_paid, refund_quote
from .reminders import reminder_date
from .renewals import Standing, notice_date, renewal_date, standing
from .store import Store
from .taxes import premium_tax

log = logging.getLogger("billing")
PORTAL_DIST = SERVICE_ROOT / "portal" / "dist"


class QuoteRequest(BaseModel):
    state: str
    product: str
    coverage: str = "standard"
    driver_age: int | None = None
    policy_count: int = 1
    paperless: bool = False


class PaymentRequest(BaseModel):
    amount: Decimal
    received_at: datetime


class CancelRequest(BaseModel):
    cancel_date: date


def configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for handler in list(log.handlers):
        log.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log.addHandler(handler)
    log.setLevel(logging.INFO)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    rates = load_rates(settings.rates_path)
    store = Store(settings.db_path)
    if not store.is_seeded():
        store.reset(settings, rates)
    configure_logging(settings.log_path)

    app = FastAPI(title="Harbor Mutual billing service", version=__version__)
    app.state.settings, app.state.rates, app.state.store = settings, rates, store

    @app.middleware("http")
    async def request_log(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-Id") or f"req-{uuid.uuid4().hex[:12]}"
        try:
            response: Response = await call_next(request)
        except Exception:
            log.exception("unhandled error on %s %s request_id=%s", request.method, request.url.path, request_id)
            response = JSONResponse({"error": "internal", "request_id": request_id}, status_code=500)
        log.info("%s %s -> %s request_id=%s", request.method, request.url.path, response.status_code, request_id)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(BillingError)
    async def billing_error(request: Request, exc: BillingError) -> JSONResponse:
        return JSONResponse({"error": exc.code, "detail": str(exc)}, status_code=exc.status)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__, "today": settings.today.isoformat()}

    @app.post("/quotes")
    def quote(body: QuoteRequest) -> dict[str, Any]:
        q = quote_premium(rates, **body.model_dump())
        tax = premium_tax(body.state, q.premium)
        return {
            "base_rate": fmt(q.base_rate), "coverage_factor": str(q.coverage_factor), "age_factor": str(q.age_factor),
            "subtotal": fmt(q.subtotal), "discount_rate": str(q.discount_rate), "discount": fmt(q.discount),
            "premium": fmt(q.premium), "premium_tax": fmt(tax), "total": fmt(q.premium + tax),
            "currency": settings.currency,
        }

    @app.get("/customers/{customer_id}")
    def customer(customer_id: str) -> dict[str, Any]:
        return store.customer(customer_id)

    @app.get("/policies/{policy_id}")
    def policy(policy_id: str) -> dict[str, Any]:
        p = store.policy(policy_id)
        effective = date.fromisoformat(p["effective_date"])
        s = _standing(store, settings, p)
        unpaid = [r for r in store.installments(policy_id) if r["paid_on"] is None]
        next_payment = None
        if unpaid and s.status in ("active", "past_due"):
            # what a payment made today must be (BR-11): the next installment, plus the late fee once it's overdue
            inst = store.as_installment(unpaid[0])
            late_fee = settings.late_fee if settings.today > inst.due_date else Decimal("0.00")
            next_payment = {"installment": inst.number, "due_date": inst.due_date.isoformat(),
                            "late_fee": fmt(late_fee), "amount_due": fmt(inst.total + late_fee)}
        return {
            **p,
            "renewal_date": renewal_date(effective).isoformat(),
            "status": s.status,
            "standing": {
                "installment": s.installment,
                "due_date": _iso(s.due_date),
                "grace_ends": _iso(s.grace_ends),
                "lapse_date": _iso(s.lapse_date),
            },
            "next_payment": next_payment,
        }

    @app.get("/policies/{policy_id}/schedule")
    def schedule(policy_id: str) -> dict[str, Any]:
        p = store.policy(policy_id)
        rows = store.installments(policy_id)
        for row in rows:
            row["total"] = fmt(Decimal(row["amount"]) + Decimal(row["fee"]))
        return {"policy_id": policy_id, "plan": p["plan"], "installments": rows}

    @app.get("/policies/{policy_id}/reminders")
    def reminders(policy_id: str) -> dict[str, Any]:
        p = store.policy(policy_id)
        result = []
        if p["cancelled_on"] is None:
            for row in store.installments(policy_id):
                if row["paid_on"] is not None:
                    continue
                remind_on = reminder_date(date.fromisoformat(row["due_date"]), settings.reminder_days)
                result.append({
                    "installment": row["number"], "due_date": row["due_date"], "remind_on": remind_on.isoformat(),
                    "status": "sent" if remind_on <= settings.today else "scheduled",
                })
        return {"policy_id": policy_id, "reminders": result}

    @app.post("/policies/{policy_id}/payments")
    def pay(policy_id: str, body: PaymentRequest) -> dict[str, Any]:
        p = store.policy(policy_id)
        if p["cancelled_on"]:
            raise Conflict(f"policy {policy_id} was cancelled on {p['cancelled_on']}")
        unpaid = [r for r in store.installments(policy_id) if r["paid_on"] is None]
        if not unpaid:
            raise Conflict(f"policy {policy_id} has nothing left to pay")
        installment = store.as_installment(unpaid[0])
        received_on = received_date(body.received_at, settings.timezone)
        outcome = assess_payment(installment, body.amount, received_on, settings.grace_days, settings.late_fee)
        store.record_payment(policy_id, outcome.installment, received_on, outcome.amount_due, outcome.late_fee)
        log.info("payment policy=%s installment=%s received_at=%s received_on=%s late=%s amount=%s",
                 policy_id, outcome.installment, body.received_at.isoformat(), received_on, outcome.late,
                 fmt(outcome.amount_due))
        return {
            "policy_id": policy_id, "installment": outcome.installment, "received_on": received_on.isoformat(),
            "late": outcome.late, "late_fee": fmt(outcome.late_fee), "amount": fmt(outcome.amount_due),
        }

    @app.get("/policies/{policy_id}/renewal")
    def renewal(policy_id: str) -> dict[str, Any]:
        p = store.policy(policy_id)
        renews = renewal_date(date.fromisoformat(p["effective_date"]))
        s = _standing(store, settings, p)
        eligible = s.status == "active"
        premium = None
        if eligible:  # only an active policy is offered a renewal, priced at today's rates (BR-13)
            premium = fmt(quote_premium(
                rates, state=p["state"], product=p["product"], coverage=p["coverage"], driver_age=p["driver_age"],
                policy_count=store.active_policy_count(p["customer_id"]),
                paperless=store.customer(p["customer_id"])["paperless"],
            ).premium)
        return {
            "policy_id": policy_id,
            "renewal_date": renews.isoformat(),
            "notice_date": notice_date(renews, settings.renewal_notice_days).isoformat(),
            "eligible": eligible,
            "reason": None if eligible else f"policy is {s.status}",
            "renewal_premium": premium,
        }

    @app.get("/policies/{policy_id}/refund-quote")
    def refund_preview(policy_id: str, cancel_date: date) -> dict[str, Any]:
        p = store.policy(policy_id)
        return _refund_json(policy_id, _refund(store, settings, p, cancel_date))

    @app.post("/policies/{policy_id}/cancel")
    def cancel(policy_id: str, body: CancelRequest) -> dict[str, Any]:
        p = store.policy(policy_id)
        if p["cancelled_on"]:
            raise Conflict(f"policy {policy_id} was already cancelled on {p['cancelled_on']}")
        quote = _refund(store, settings, p, body.cancel_date)
        store.cancel(policy_id, body.cancel_date, quote.refund)
        log.info("cancel policy=%s cancel_date=%s kind=%s refund=%s", policy_id, body.cancel_date, quote.kind,
                 fmt(quote.refund))
        return {**_refund_json(policy_id, quote), "status": "cancelled"}

    @app.get("/portal", include_in_schema=False)
    def portal() -> Response:
        return _portal_file("index.html")

    @app.get("/portal/{name}", include_in_schema=False)
    def portal_asset(name: str) -> Response:
        return _portal_file(name)

    @app.get("/sandbox/status")
    def sandbox_status(x_sandbox_token: str = Header(default="")) -> Response:
        if denied := _check_token(settings, x_sandbox_token):
            return denied
        return JSONResponse({"today": settings.today.isoformat(), "version": __version__, **store.counts()})

    @app.post("/sandbox/reset")
    def sandbox_reset(x_sandbox_token: str = Header(default="")) -> Response:
        if denied := _check_token(settings, x_sandbox_token):
            return denied
        store.reset(settings, rates)
        return JSONResponse({"reset": True, **store.counts()})

    return app


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value else None


def _standing(store: Store, settings: Settings, p: dict[str, Any]) -> Standing:
    unpaid = [
        (r["number"], date.fromisoformat(r["due_date"])) for r in store.installments(p["id"]) if r["paid_on"] is None
    ]
    return standing(p["cancelled_on"] is not None, unpaid, settings.today, settings.grace_days)


def _refund(store: Store, settings: Settings, p: dict[str, Any], cancel_date: date) -> RefundQuote:
    paid = premium_paid(store.installments(p["id"]))
    return refund_quote(
        annual_premium=Decimal(p["annual_premium"]),
        effective=date.fromisoformat(p["effective_date"]),
        cancel_date=cancel_date,
        paid=paid,
        free_look_days=settings.free_look_days,
        cancellation_fee=settings.cancellation_fee,
    )


def _refund_json(policy_id: str, q: RefundQuote) -> dict[str, Any]:
    return {
        "policy_id": policy_id, "kind": q.kind, "paid": fmt(q.paid), "earned": fmt(q.earned), "fee": fmt(q.fee),
        "refund": fmt(q.refund), "days_in_force": q.days_in_force, "term_days": q.term_days,
    }


def _portal_file(name: str) -> Response:
    path = (PORTAL_DIST / name).resolve()
    if path.parent != PORTAL_DIST.resolve() or not path.is_file():
        if not (PORTAL_DIST / "index.html").is_file():
            return JSONResponse(
                {"error": "portal_not_built", "detail": "run `node portal/build.mjs` to build the portal page"},
                status_code=503,
            )
        raise NotFound(f"no portal file {name}")
    return FileResponse(path)


def _check_token(settings: Settings, given: str) -> Response | None:
    if not settings.sandbox_token or given != settings.sandbox_token:
        return JSONResponse({"error": "forbidden"}, status_code=403)
    return None
