"""HTTP API. Every request is logged with a request id; unexpected errors are logged with their stack trace and
answered with a 500 that carries the same id, so a support ticket can quote it."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import requests
from fastapi import FastAPI, Header, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__, letters, receipts
from ._vendor import holidaycal
from .config import SERVICE_ROOT, Settings, load_settings
from .endorsements import endorsement_quote
from .errors import BillingError, Conflict, NotFound
from .money import fmt
from .notify import Gateway
from .payments import assess_payment, late_fee_on, received_date
from .premiums import quote_premium
from .rates import UnknownTerritory, load_rates
from .refunds import RefundQuote, premium_paid, refund_quote
from .reinstatement import Reinstatement, reinstatement_terms
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


class ReinstateRequest(BaseModel):
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
    app.state.gateway = Gateway(settings.notify_url) if settings.notify_url else None
    receipt_key = settings.receipt_key or secrets.token_hex(32)

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
        if body.state not in rates.states():
            raise UnknownTerritory(f"Harbor Mutual doesn't write policies in {body.state!r}; it writes in "
                                   f"{', '.join(rates.states())}")
        q = quote_premium(rates, **body.model_dump())
        tax = premium_tax(body.state, q.premium)
        return {
            "base_rate": fmt(q.base_rate), "coverage_factor": str(q.coverage_factor), "age_factor": str(q.age_factor),
            "subtotal": fmt(q.subtotal), "discount_rate": str(q.discount_rate),
            "multi_policy_discount": fmt(q.multi_policy_discount), "paperless_discount": fmt(q.paperless_discount),
            "discount": fmt(q.discount),
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
            # what a payment made today must be (BR-11)
            inst = store.as_installment(unpaid[0])
            late_fee = late_fee_on(inst, settings.today, settings.late_fee)
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

    @app.post("/reminders/send")
    def send_reminders(on: date | None = None) -> dict[str, Any]:
        """Delivers the reminders due on `on` (default today) through the notification gateway (BR-18)."""
        gateway: Gateway | None = app.state.gateway
        if gateway is None:
            raise Conflict("reminder delivery is not configured: set BILLING_NOTIFY_URL")
        day = (on or settings.today).isoformat()
        sent, failed = [], []
        for policy_id in store.policy_ids():
            for r in reminders(policy_id)["reminders"]:
                if r["remind_on"] != day:
                    continue
                p = store.policy(policy_id)
                c = store.customer(p["customer_id"])
                row = store.installments(policy_id)[r["installment"] - 1]
                message = {
                    "kind": "payment_reminder", "channel": "email" if c["paperless"] else "mail",
                    "customer_id": c["id"], "name": c["name"], "policy_id": policy_id,
                    "installment": r["installment"], "due_date": r["due_date"],
                    "amount_due": fmt(Decimal(row["amount"]) + Decimal(row["fee"])),
                }
                entry = {"policy_id": policy_id, "installment": r["installment"]}
                try:
                    sent.append({**entry, "message_id": gateway.send(message)})
                except requests.RequestException as exc:
                    log.warning("reminder not delivered policy=%s installment=%s: %s", policy_id, r["installment"],
                                exc)
                    failed.append({**entry, "error": type(exc).__name__})
        log.info("reminders on=%s sent=%s failed=%s", day, len(sent), len(failed))
        return {"on": day, "sent": sent, "failed": failed}

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
        result = {
            "policy_id": policy_id, "installment": outcome.installment, "received_on": received_on.isoformat(),
            "late": outcome.late, "late_fee": fmt(outcome.late_fee), "amount": fmt(outcome.amount_due),
        }
        return {**result, "receipt": receipts.issue(result, receipt_key)}

    @app.post("/policies/{policy_id}/installments/{number}/return")
    def return_payment(policy_id: str, number: int) -> dict[str, Any]:
        """The bank returned the payment of an installment: it is unpaid again, with the returned-payment fee (BR-21)."""
        p = store.policy(policy_id)
        if p["cancelled_on"]:
            raise Conflict(f"policy {policy_id} was cancelled on {p['cancelled_on']}")
        rows = {r["number"]: r for r in store.installments(policy_id)}
        if number not in rows:
            raise NotFound(f"policy {policy_id} has no installment {number}")
        if rows[number]["paid_on"] is None:
            raise Conflict(f"installment {number} of policy {policy_id} isn't paid")
        fee = store.return_payment(policy_id, number, settings.returned_payment_fee)
        log.info("returned payment policy=%s installment=%s fee=%s", policy_id, number, fmt(fee))
        return {"policy_id": policy_id, "installment": number, "returned_payment_fee": fmt(settings.returned_payment_fee),
                "fee": fmt(fee), "amount_due": fmt(Decimal(rows[number]["amount"]) + fee)}

    def _reinstatement(p: dict[str, Any], day: date) -> Reinstatement:
        unpaid = [store.as_installment(r) for r in store.installments(p["id"]) if r["paid_on"] is None]
        return reinstatement_terms(_standing(store, settings, p), unpaid, day, settings.reinstatement_days,
                                   settings.reinstatement_fee)

    def _reinstatement_json(policy_id: str, r: Reinstatement) -> dict[str, Any]:
        return {"policy_id": policy_id, "lapse_date": r.lapse_date.isoformat(), "last_day": r.last_day.isoformat(),
                "installments": r.installments, "reinstatement_fee": fmt(r.fee), "amount_due": fmt(r.amount_due)}

    @app.get("/policies/{policy_id}/reinstatement")
    def reinstatement(policy_id: str) -> dict[str, Any]:
        """What reinstating a lapsed policy today takes (BR-22)."""
        return _reinstatement_json(policy_id, _reinstatement(store.policy(policy_id), settings.today))

    @app.post("/policies/{policy_id}/reinstate")
    def reinstate(policy_id: str, body: ReinstateRequest) -> dict[str, Any]:
        """Reinstates a lapsed policy with one payment of everything due plus the reinstatement fee (BR-22)."""
        p = store.policy(policy_id)
        received_on = received_date(body.received_at, settings.timezone)
        terms = _reinstatement(p, received_on)
        if body.amount != terms.amount_due:
            raise BillingError(f"reinstating policy {policy_id} needs exactly {terms.amount_due}, including the "
                               f"{terms.fee} reinstatement fee; received {body.amount}")
        rows = {r["number"]: r for r in store.installments(policy_id)}
        for number in terms.installments:
            inst = store.as_installment(rows[number])
            store.record_payment(policy_id, number, received_on, inst.total, Decimal("0.00"))
        log.info("reinstated policy=%s received_on=%s installments=%s amount=%s", policy_id, received_on,
                 terms.installments, fmt(terms.amount_due))
        return {**_reinstatement_json(policy_id, terms), "received_on": received_on.isoformat(),
                "status": _standing(store, settings, store.policy(policy_id)).status}

    @app.get("/receipts/{token}")
    def receipt(token: str) -> dict[str, Any]:
        """Checks a payment receipt (BR-19) and returns the payment it records."""
        return {"valid": True, **receipts.verify(token, receipt_key)}

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

    @app.get("/policies/{policy_id}/renewal-notice", response_class=HTMLResponse)
    def renewal_notice(policy_id: str) -> HTMLResponse:
        """The renewal notice letter (BR-17), with the terms `GET /policies/{id}/renewal` offers."""
        offer = renewal(policy_id)
        if not offer["eligible"]:
            raise Conflict(f"no renewal notice for policy {policy_id}: {offer['reason']}")
        p = store.policy(policy_id)
        html = letters.render(
            "renewal_notice.html", policy=p, customer=store.customer(p["customer_id"]), renewal=offer,
            notice_days=settings.renewal_notice_days,
        )
        return HTMLResponse(html)

    @app.get("/policies/{policy_id}/endorsement-quote")
    def endorsement(policy_id: str, coverage: str, on: date | None = None) -> dict[str, Any]:
        """What a coverage change from `on` (default today) costs for the rest of the term (BR-20)."""
        p = store.policy(policy_id)
        if p["cancelled_on"]:
            raise Conflict(f"policy {policy_id} was cancelled on {p['cancelled_on']}")
        c = store.customer(p["customer_id"])
        e = endorsement_quote(rates, p, coverage=coverage, on=on or settings.today,
                              policy_count=store.active_policy_count(c["id"]), paperless=c["paperless"])
        return {
            "policy_id": policy_id, "on": (on or settings.today).isoformat(), "coverage_from": e.coverage_from,
            "coverage_to": e.coverage_to, "old_premium": fmt(e.old_premium), "new_premium": fmt(e.new_premium),
            "days_remaining": e.days_remaining, "term_days": e.term_days, "adjustment": fmt(e.adjustment),
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

    @app.get("/calendar/holidays")
    def holidays(year: int) -> dict[str, Any]:
        """The federal holidays the billing calendar skips (BR-14), as observed."""
        if not 2000 <= year <= 2100:
            raise BillingError("year must be between 2000 and 2100")
        days = sorted(holidaycal.holidays(year).items())
        return {"year": year, "holidays": [{"date": day.isoformat(), "name": name} for day, name in days]}

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
