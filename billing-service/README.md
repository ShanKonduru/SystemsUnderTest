# Harbor Mutual billing service

Prices policies, builds installment schedules, takes payments, and works out renewals and cancellation refunds for
Harbor Mutual's auto, home and renters policies. A FastAPI service over SQLite, plus the member portal's billing page,
written in TypeScript.

```
 member portal (/portal, TypeScript) ──► billing service (FastAPI :8500) ──► billing.db (SQLite), logs/billing.log
                                           premiums · installments · payments · renewals · refunds
```

The business rules are in [`docs/specs/`](docs/specs/): [billing rules](docs/specs/billing-rules.md) (BR-xx),
[refund rules](docs/specs/refund-rules.md) (RF-xx) and [portal display rules](docs/specs/portal-display.md) (PD-xx).
Owning teams are in [`CODEOWNERS`](CODEOWNERS); vendored code in [`THIRD_PARTY.md`](THIRD_PARTY.md).

## Layout

| Path | What |
|---|---|
| `billing/premiums.py` | Quotes: base rate x coverage x driver age, multi-policy and paperless discounts, half-up rounding |
| `billing/installments.py`, `billing/dates.py` | Installment plans, amounts and due dates |
| `billing/payments.py` | The billing day (Eastern time), exact amounts, late fees |
| `billing/reminders.py` | Payment reminder dates |
| `billing/renewals.py` | Standing (active / past due / lapsed), renewal dates, renewal notice dates |
| `billing/refunds.py` | Free-look and pro-rata refunds |
| `billing/rates.py`, `reference/rate_tables.csv` | The rate table |
| `billing/store.py` | SQLite schema and seed data |
| `billing/api.py` | HTTP endpoints, request ids, error logging |
| `billing/_vendor/holidaycal/` | US federal holiday calendar (third-party, pinned) |
| `config/billing.yaml` | Business settings: timezone, grace days, free-look days, fees |
| `portal/src/` | Money and date formatting, payment-form validation, the billing page script |
| `portal/page/` | The billing page's HTML and CSS |

## Run it

Python 3.11+ and Node 24+.

```bat
cd billing-service
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt pytest pytest-cov httpx ruff
node portal\build.mjs                 :: builds portal\dist for the /portal page
.venv\Scripts\python -m billing       :: http://127.0.0.1:8500/docs, http://127.0.0.1:8500/portal?policy=P-5001
```

| Variable | Default | Meaning |
|---|---|---|
| `BILLING_PORT` | `8500` | Port (the service binds to 127.0.0.1) |
| `BILLING_DATA` | `billing-service/data` | Where `billing.db` and `logs/billing.log` go; the DB is seeded on first start |
| `BILLING_TODAY` | `2026-11-03` | The service's "today", fixed so results are reproducible |
| `BILLING_TIMEZONE` | from `config/billing.yaml` | Overrides the billing timezone |
| `BILLING_CONFIG`, `BILLING_RATES` | `config/billing.yaml`, `reference/rate_tables.csv` | Alternative config or rate table |
| `BILLING_SANDBOX_TOKEN` | empty | Token for `/sandbox/status` and `/sandbox/reset` (header `X-Sandbox-Token`); empty refuses both |

## API

| Method and path | What it does |
|---|---|
| `GET /health` | Status, version and the service's today |
| `POST /quotes` | `{state, product, coverage, driver_age?, policy_count, paperless}` → premium breakdown |
| `GET /customers/{id}` | Customer and their policy ids |
| `GET /policies/{id}` | Policy, renewal date, status, standing (installment, due date, grace end, lapse date) and `next_payment`: what a payment made today must be, late fee included |
| `GET /policies/{id}/schedule` | Installments: due date, amount, fee, total, paid on, late fee |
| `POST /policies/{id}/payments` | `{amount, received_at}` (ISO 8601 with offset) → pays the earliest unpaid installment |
| `GET /policies/{id}/reminders` | Payment reminders for the unpaid installments: remind on, sent or scheduled |
| `GET /policies/{id}/renewal` | Renewal date, notice date, eligibility, renewal premium |
| `GET /policies/{id}/refund-quote?cancel_date=` | What a cancellation on that date would refund |
| `POST /policies/{id}/cancel` | `{cancel_date}` → cancels and records the refund |
| `GET /portal?policy=P-5001` | The member billing page |

Money is sent as strings with two decimals. Refused requests answer 4xx with `{error, detail}`. Any other error
answers 500 with `{error: "internal", request_id}`; the same request id is in the `X-Request-Id` header and, with the
stack trace, in `logs/billing.log`.

## Seed data

Today is 2026-11-03.

| Policy | Customer | Product | Plan | Effective | Premium | Standing |
|---|---|---|---|---|---|---|
| P-5001 | C-1001 Asha Rao (NJ, paperless) | auto, standard, age 34 | monthly | 2026-01-31 | 1411.60 | active |
| P-5002 | C-1001 Asha Rao | home, plus | quarterly | 2026-04-01 | 1276.66 | active |
| P-5003 | C-1002 Ben Ortiz (NY) | auto, basic, age 22 | annual | 2026-06-15 | 2251.21 | active |
| P-5004 | C-1003 Chen Li (PA) | auto, standard, age 41 | monthly | 2026-08-20 | 1127.45 | lapsed (Oct 20 unpaid) |
| P-5005 | C-1004 Dana Park (NJ, paperless) | auto, plus, age 67 | semiannual | 2026-05-01 | 1920.08 | past due (Nov 1 unpaid) |
| P-5006 | C-1004 Dana Park | home, standard | annual | 2026-05-01 | 966.42 | active |
| P-5007 | C-1004 Dana Park | renters, basic | annual | 2026-09-10 | 148.59 | active |
| P-5008 | C-1005 Eli Novak (CT) | renters, standard | semiannual | 2026-10-28 | 214.30 | active, in free-look |

## Tests

```bat
.venv\Scripts\ruff check billing tests
.venv\Scripts\python -m pytest -q --cov        :: 95% branch-coverage gate
cd portal && npm test                          :: node --test, no packages to install
```
