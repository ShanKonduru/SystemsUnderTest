"""SQLite schema, seed data and the customer-scoped insurance backend service.

The backend is the "system of record" the chatbot acts on. Every method is scoped to one
authenticated customer: the customer id is fixed when the service is created, never taken
from the model, so the model cannot act on someone else's policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

import re

from insurance_copilot.backend.pdf import text_pdf
from insurance_copilot.backend.policy import (
    DISCLOSURES, DUPLICATE_GUARDED, DUPLICATE_WINDOW_MINUTES, OPERATION_LABELS, OPERATION_LEVELS, OPERATION_METHODS,
    REP_PREFIX, ROLES, SERVICE_CONTEXT_MINUTES, STEP_UP_MAX_FAILURES, STEP_UP_MINUTES, TransactionPolicy, rank,
)

DRIVERS_DDL = """CREATE TABLE IF NOT EXISTS drivers (
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, customer_id TEXT NOT NULL, full_name TEXT NOT NULL,
    date_of_birth TEXT NOT NULL, relationship TEXT NOT NULL, added_on TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', removed_on TEXT  -- status: active | removed
)"""

POLICY_RIDERS_DDL = """CREATE TABLE IF NOT EXISTS policy_riders (
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, customer_id TEXT NOT NULL, rider_code TEXT NOT NULL,
    monthly_cost REAL NOT NULL, status TEXT NOT NULL,  -- active | removed
    added_on TEXT NOT NULL, coverage_starts TEXT NOT NULL, removed_on TEXT
)"""

DOCUMENTS_DDL = """CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, conversation_id TEXT, filename TEXT NOT NULL,
    content_type TEXT NOT NULL, size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, content BLOB NOT NULL,
    uploaded_at TEXT NOT NULL, used_for_change TEXT,
    source TEXT NOT NULL DEFAULT 'upload'  -- upload (from the customer) | generated (issued by Harbor Mutual)
)"""

PROFILE_CHANGES_DDL = """CREATE TABLE IF NOT EXISTS profile_changes (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, change_type TEXT NOT NULL,
    old_value TEXT NOT NULL, new_value TEXT NOT NULL, document_id TEXT NOT NULL, document_type TEXT NOT NULL,
    reason TEXT, effective_date TEXT, status TEXT NOT NULL, submitted_on TEXT NOT NULL
)"""

# Every submit- or commit-level request, whether applied at once or held for review (see backend/policy.py).
TRANSACTIONS_DDL = """CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, operation TEXT NOT NULL, arguments TEXT NOT NULL,
    level TEXT NOT NULL,   -- level the request reached: submit | commit
    status TEXT NOT NULL,  -- committed | submitted | pending_review | rejected | failed
    reference TEXT,        -- business id of what was created or changed (PAY-, CLM-, POL-, ...)
    result TEXT,           -- JSON: the system's response, or the validated preview while pending
    actor TEXT NOT NULL, created_on TEXT NOT NULL, created_at TEXT NOT NULL,
    decided_on TEXT, decided_by TEXT, decision_note TEXT,
    consent TEXT           -- disclosure id the customer accepted in the approval prompt, if one was required
)"""

# Prospective customers (persona 1, not signed in) and their applications. A prospect is not a customer:
# a representative reviews the application (and any possible match with an existing customer) first.
PROSPECTS_DDL = """CREATE TABLE IF NOT EXISTS prospects (
    id TEXT PRIMARY KEY, guest_id TEXT NOT NULL, full_name TEXT NOT NULL, email TEXT NOT NULL, phone TEXT,
    date_of_birth TEXT NOT NULL, address TEXT NOT NULL, created_on TEXT NOT NULL,
    match_status TEXT NOT NULL  -- new | possible_existing_customer (internal only, never shown to the applicant)
)"""

APPLICATIONS_DDL = """CREATE TABLE IF NOT EXISTS applications (
    id TEXT PRIMARY KEY, prospect_id TEXT NOT NULL, guest_id TEXT NOT NULL, quote_id TEXT NOT NULL,
    product_code TEXT NOT NULL, status TEXT NOT NULL,  -- submitted | approved | declined | issued
    submitted_on TEXT NOT NULL, decided_on TEXT, decided_by TEXT, decision_note TEXT
)"""

# Requests to talk to a person: the representative queue (persona 2 works it).
HANDOFFS_DDL = """CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, topic TEXT NOT NULL, summary TEXT NOT NULL,
    channel TEXT NOT NULL, contact TEXT NOT NULL, callback_time TEXT,
    status TEXT NOT NULL,  -- open | assigned | closed
    created_on TEXT NOT NULL, assigned_to TEXT, closed_on TEXT, resolution TEXT
)"""

# Outcome of each identity check. The answers themselves are never stored.
IDENTITY_CHECKS_DDL = """CREATE TABLE IF NOT EXISTS identity_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT NOT NULL, method TEXT NOT NULL,
    outcome TEXT NOT NULL,  -- passed | failed
    checked_at TEXT NOT NULL
)"""

# Harbor Mutual representatives (persona 2). book: JSON list of customer ids they serve, or "*" for everyone.
REPRESENTATIVES_DDL = """CREATE TABLE IF NOT EXISTS representatives (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, book TEXT NOT NULL
)"""

# The customer a representative is working for: opened only by a passed verify_customer, one at a time per
# representative, closed by end_customer_session or SERVICE_CONTEXT_MINUTES after verification. Reading it never
# writes, so read-only tool calls leave the database untouched.
SERVICE_CONTEXTS_DDL = """CREATE TABLE IF NOT EXISTS service_contexts (
    id TEXT PRIMARY KEY, rep_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    opened_at TEXT NOT NULL, last_used_at TEXT NOT NULL, closed_at TEXT
)"""

# Internal interaction notes and follow-up tasks. Never shown to customers.
NOTES_DDL = """CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, rep_id TEXT NOT NULL,
    kind TEXT NOT NULL,    -- note | task
    text TEXT NOT NULL, due_on TEXT,
    status TEXT NOT NULL,  -- recorded (notes) | open | done (tasks)
    created_on TEXT NOT NULL, completed_on TEXT
)"""

APP_TABLES_DDL = [DRIVERS_DDL, POLICY_RIDERS_DDL, DOCUMENTS_DDL, PROFILE_CHANGES_DDL, TRANSACTIONS_DDL,
                  PROSPECTS_DDL, APPLICATIONS_DDL, HANDOFFS_DDL, IDENTITY_CHECKS_DDL, REPRESENTATIVES_DDL,
                  SERVICE_CONTEXTS_DDL, NOTES_DDL]

REPRESENTATIVES = [
    ("REP-201", "J. Rivera", "service_rep", ["CUST-001", "CUST-003"]),
    ("REP-202", "A. Chen", "licensed_agent", "*"),
    ("REP-203", "M. Patel", "supervisor", "*"),
]


def _seed_representatives(con: sqlite3.Connection) -> None:
    con.executemany("INSERT OR IGNORE INTO representatives VALUES (?,?,?,?)",
                    [(i, n, r, b if b == "*" else json.dumps(b)) for i, n, r, b in REPRESENTATIVES])

SCHEMA = """
CREATE TABLE customers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT NOT NULL,
    address TEXT NOT NULL, date_of_birth TEXT NOT NULL
);
CREATE TABLE products (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
    base_monthly_premium REAL NOT NULL, required_fields TEXT NOT NULL  -- JSON list
);
CREATE TABLE quotes (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, product_code TEXT NOT NULL,
    coverage_level TEXT NOT NULL, deductible INTEGER NOT NULL, insured_item TEXT NOT NULL,
    monthly_premium REAL NOT NULL, created_on TEXT NOT NULL, expires_on TEXT NOT NULL,
    status TEXT NOT NULL  -- open | purchased
);
CREATE TABLE policies (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL, product_code TEXT NOT NULL,
    status TEXT NOT NULL,  -- active | cancelled
    coverage_level TEXT NOT NULL, deductible INTEGER NOT NULL, insured_item TEXT NOT NULL,
    monthly_premium REAL NOT NULL, start_date TEXT NOT NULL, end_date TEXT NOT NULL,
    balance_due REAL NOT NULL, next_due_date TEXT NOT NULL,
    cancelled_on TEXT, cancel_reason TEXT, quote_id TEXT
);
CREATE TABLE claims (
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    incident_date TEXT NOT NULL, claim_type TEXT NOT NULL, description TEXT NOT NULL,
    estimated_amount REAL NOT NULL, status TEXT NOT NULL, filed_on TEXT NOT NULL
);
CREATE TABLE payments (
    id TEXT PRIMARY KEY, policy_id TEXT NOT NULL, customer_id TEXT NOT NULL,
    amount REAL NOT NULL, method TEXT NOT NULL, paid_on TEXT NOT NULL
);
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT NOT NULL, tool TEXT NOT NULL,
    arguments TEXT NOT NULL, outcome TEXT NOT NULL,  -- ok | denied | error
    detail TEXT,
    actor TEXT  -- who made the call: customer:CUST-.., guest:GUEST-.., rep:REP-..
);
"""

COVERAGE_FACTORS = {"basic": 0.8, "standard": 1.0, "premium": 1.35}
DEDUCTIBLE_FACTORS = {250: 1.15, 500: 1.0, 1000: 0.88, 2000: 0.78}
CLAIM_TYPES = ["collision", "theft", "weather", "fire", "water_damage", "liability", "medical", "trip_cancellation", "other"]
PAYMENT_METHODS = ["card_on_file", "bank_account"]
DRIVER_RELATIONSHIPS = ["spouse", "partner", "child", "parent", "sibling", "other_household_member"]
MIN_DRIVER_AGE = 16
MAX_ADDITIONAL_DRIVERS = 4

# Optional add-on covers. "prices" holds the monthly price per product: a rider is only offered
# for the products listed there. Extra eligibility rules live in InsuranceBackend._rider_eligibility.
RIDERS: list[dict[str, Any]] = [
    {"code": "BURGLARY", "name": "Burglary & Theft Cover",
     "description": "Covers belongings stolen in a break-in, plus repairs to doors and windows damaged by the burglar.",
     "good_for": "homes with valuable contents, ground-floor or street-facing properties, homes often left empty",
     "coverage_limit": "$25,000 per incident", "prices": {"HOME": 6.0, "RENTERS": 3.5}},
    {"code": "FLOOD", "name": "Flood Cover",
     "description": "Covers damage from rising water (river, surface water and storm-surge flooding), which standard "
                    "home and renters policies exclude.",
     "good_for": "properties near rivers, lakes or the coast, low-lying areas, places with heavy seasonal rain",
     "coverage_limit": "$250,000 building / $100,000 contents", "waiting_period_days": 30,
     "prices": {"HOME": 22.0, "RENTERS": 9.0}},
    {"code": "FIRE_EXTENDED", "name": "Extended Fire & Smoke Cover",
     "description": "Adds smoke and soot damage, wildfire damage and up to 12 months of temporary housing after a fire. "
                    "Basic fire damage is already included in the base policy.",
     "good_for": "wildfire-prone areas, older homes, wooden construction",
     "coverage_limit": "$50,000 extra, plus temporary housing up to 12 months",
     "prices": {"HOME": 8.0, "RENTERS": 4.0}},
    {"code": "WATER_BACKUP", "name": "Sewer & Water Backup Cover",
     "description": "Covers water damage from backed-up drains, sewers or failed sump pumps.",
     "good_for": "homes with basements, older plumbing or a sump pump",
     "coverage_limit": "$15,000", "prices": {"HOME": 5.0, "RENTERS": 3.0}},
    {"code": "EARTHQUAKE", "name": "Earthquake Cover",
     "description": "Covers structural and contents damage caused by earthquakes, which the base policy excludes.",
     "good_for": "homes in seismic zones", "coverage_limit": "Up to the dwelling limit, 10% deductible",
     "prices": {"HOME": 15.0}},
    {"code": "VALUABLES", "name": "Valuables Cover (jewelry, art, collectibles)",
     "description": "Covers listed valuables for their appraised value, including accidental loss, anywhere in the world.",
     "good_for": "owners of jewelry, watches, art or collectibles worth more than $1,500 per item",
     "coverage_limit": "$10,000 total, $5,000 per item", "prices": {"HOME": 6.0, "RENTERS": 6.0}},
    {"code": "RENTAL_CAR", "name": "Rental Car Cover",
     "description": "Pays for a rental car while your vehicle is being repaired after a covered claim.",
     "good_for": "drivers who rely on their car daily and have no second vehicle",
     "coverage_limit": "$40 per day, up to 30 days", "prices": {"AUTO": 7.0},
     "requires": "standard or premium coverage (collision)"},
    {"code": "ROADSIDE", "name": "Roadside Assistance",
     "description": "Towing, jump-starts, flat tyres, lock-outs and fuel delivery, 24/7.",
     "good_for": "long commutes, older vehicles, frequent highway travel",
     "coverage_limit": "Towing up to 50 miles, 4 call-outs per year", "prices": {"AUTO": 4.0}},
    {"code": "GAP", "name": "Gap Cover",
     "description": "If the car is written off, pays the difference between its market value and what is still owed "
                    "on the car loan or lease.",
     "good_for": "new cars bought with a loan or lease", "coverage_limit": "Up to 25% of the vehicle value",
     "prices": {"AUTO": 6.0}, "requires": "vehicle 3 model years old or newer"},
    {"code": "TRIP_DELAY_PLUS", "name": "Trip Delay Plus",
     "description": "Pays for meals, hotels and rebooking when a trip is delayed by more than 6 hours.",
     "good_for": "trips with connecting flights or tight schedules", "coverage_limit": "$1,000 per trip",
     "prices": {"TRAVEL": 5.0}},
    {"code": "ADVENTURE_SPORTS", "name": "Adventure Sports Cover",
     "description": "Extends medical cover to skiing, scuba diving, trekking above 3,000 m and similar activities.",
     "good_for": "travellers planning skiing, diving, hiking or other sports", "coverage_limit": "$100,000 medical",
     "prices": {"TRAVEL": 8.0}},
    {"code": "ACCIDENTAL_DEATH", "name": "Accidental Death Benefit",
     "description": "Pays an extra lump sum equal to the coverage amount if death is caused by an accident.",
     "good_for": "people with dependents who want extra protection at low cost", "coverage_limit": "100% of coverage",
     "prices": {"LIFE": 6.0}},
    {"code": "CRITICAL_ILLNESS", "name": "Critical Illness Cover",
     "description": "Pays 25% of the coverage amount on diagnosis of cancer, heart attack or stroke.",
     "good_for": "family history of serious illness, sole earners", "coverage_limit": "25% of coverage",
     "prices": {"LIFE": 12.0}},
    {"code": "WAIVER_OF_PREMIUM", "name": "Waiver of Premium",
     "description": "Premiums are waived if you cannot work for 6 months or more because of disability.",
     "good_for": "self-employed people and sole earners", "coverage_limit": "All premiums while disabled",
     "prices": {"LIFE": 4.0}},
]
RIDERS_BY_CODE = {r["code"]: r for r in RIDERS}

# Changes to core customer details need a proof document uploaded in the chat.
MAX_DOCUMENT_BYTES = 5 * 1024 * 1024
DOCUMENT_SIGNATURES = {  # content type -> leading bytes of a real file of that type
    "application/pdf": [b"%PDF"],
    "image/png": [b"\x89PNG"],
    "image/jpeg": [b"\xff\xd8\xff"],
    "image/webp": [b"RIFF"],
}
CHANGE_TYPES: dict[str, dict[str, Any]] = {
    "mailing_address": {
        "field": "address",
        "label": "Mailing address",
        "questions": ["the full new address: street, unit, city, state and ZIP",
                      "the date they moved (effective date)"],
        "accepted_documents": {
            "utility_bill": "utility bill (electricity, gas, water, internet) from the last 3 months",
            "bank_statement": "bank or credit card statement from the last 3 months",
            "lease_agreement": "signed lease or rental agreement",
            "government_id": "government-issued ID showing the new address",
        },
        "notes": "The document must show the customer's name and the new address.",
    },
    "date_of_birth": {
        "field": "date_of_birth",
        "label": "Date of birth",
        "questions": ["the correct date of birth (exact date, not an age)",
                      "why it needs correcting (for example, a typo when the policy was set up)"],
        "accepted_documents": {
            "passport": "passport",
            "drivers_license": "driver's license",
            "birth_certificate": "birth certificate",
        },
        "notes": "Premiums that depend on age may be recalculated after review.",
    },
}


def driver_surcharge(age: int) -> float:
    """Premium surcharge per additional driver: young and senior drivers cost more."""
    if age < 25:
        return 0.35
    if age >= 70:
        return 0.20
    return 0.10


def age_on(dob: date, today: date) -> int:
    return today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))

PRODUCTS = [
    ("AUTO", "Auto Insurance", "Liability, collision and comprehensive cover for one vehicle.", 95.0,
     ["vehicle_year", "vehicle_make", "vehicle_model"]),
    ("HOME", "Homeowners Insurance", "Dwelling, personal property and liability cover for an owned home.", 110.0,
     ["property_address", "year_built", "square_feet"]),
    ("RENTERS", "Renters Insurance", "Personal property and liability cover for tenants.", 18.0,
     ["property_address", "personal_property_value"]),
    ("TRAVEL", "Travel Insurance", "Trip cancellation, medical and baggage cover for one trip.", 25.0,
     ["destination", "trip_start", "trip_end"]),
    ("LIFE", "Term Life Insurance", "Fixed-term life cover paying a lump sum to beneficiaries.", 40.0,
     ["term_years", "coverage_amount"]),
]


# ---------------------------------------------------------------------------- errors
class BackendError(Exception):
    code = "ERROR"

    def __str__(self) -> str:  # surfaces as "CODE: message" in tool results
        return f"{self.code}: {self.args[0] if self.args else ''}"


class AccessDenied(BackendError):
    code = "ACCESS_DENIED"


class NotFound(BackendError):
    code = "NOT_FOUND"


class InvalidRequest(BackendError):
    code = "INVALID_REQUEST"


class NotPermitted(BackendError):
    code = "NOT_PERMITTED"


class DuplicateRequest(BackendError):
    code = "DUPLICATE_REQUEST"


class ConsentRequired(BackendError):
    code = "CONSENT_REQUIRED"


class VerificationRequired(BackendError):
    code = "VERIFICATION_REQUIRED"


class CustomerNotVerified(BackendError):
    code = "CUSTOMER_NOT_VERIFIED"


GUEST_PREFIX = "GUEST-"
GUEST_OPERATIONS = {"get_quote", "start_application", "request_representative"}
POLICY_DOCUMENT_TYPES = {
    "declarations": "Declarations page",
    "proof_of_insurance": "Proof of insurance",
    "id_card": "Auto insurance ID card",
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


TRANSACTION_STATUSES = {
    "committed": "Done: the change is saved in the system of record.",
    "submitted": "Submitted into a workflow (for example a claim with an adjuster); the outcome comes later.",
    "pending_review": "Waiting for review by Harbor Mutual staff. Nothing has changed yet.",
    "rejected": "Rejected in review. Nothing was changed.",
    "failed": "Approved in review, but the change could not be applied. Nothing was changed.",
}
# Result keys that name the business record a request created or changed, most specific first.
REFERENCE_KEYS = ("payment_id", "claim_id", "change_id", "driver_id", "rider_id", "policy_id", "application_id",
                  "handoff_id")


# ---------------------------------------------------------------------------- pricing
def _item_factor(product_code: str, item: dict[str, Any], today: date) -> float:
    try:
        if product_code == "AUTO":
            age = today.year - int(item["vehicle_year"])
            return 1.2 if age <= 3 else 1.0 if age <= 10 else 0.85
        if product_code == "HOME":
            return min(max(float(item["square_feet"]) / 2000, 0.7), 2.0)
        if product_code == "RENTERS":
            return min(max(float(item["personal_property_value"]) / 30000, 0.6), 2.5)
        if product_code == "LIFE":
            return min(max(float(item["coverage_amount"]) / 250000, 0.5), 4.0)
    except (KeyError, ValueError) as exc:
        raise InvalidRequest(f"Invalid insured item details: {exc}") from exc
    return 1.0


def price(base: float, product_code: str, coverage_level: str, deductible: int, item: dict, today: date) -> float:
    return round(
        base * COVERAGE_FACTORS[coverage_level] * DEDUCTIBLE_FACTORS[deductible] * _item_factor(product_code, item, today),
        2,
    )


# ---------------------------------------------------------------------------- seed
def _seed_queue(con: sqlite3.Connection, today: date) -> None:
    """A small representative backlog: a request held for approval, two applications (one possibly an existing
    customer) and two customer cases. Ids sit just below each sequence's start, so the ids the app generates
    (TXN-10001, APP-9501, HND-3501, QTE-7001, PRS-9001, ...) are the same with or without the backlog."""
    yesterday = (today - timedelta(days=1)).isoformat()
    con.execute("INSERT INTO transactions (id, customer_id, operation, arguments, level, status, reference, result, "
                "actor, created_on, created_at, consent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                ("TXN-10000", "CUST-001", "cancel_policy",
                 json.dumps({"effective_date": None, "policy_id": "POL-1001", "reason": "Vehicle sold"}, sort_keys=True),
                 "submit", "pending_review", None, None, "rep:REP-201", yesterday, f"{yesterday}T15:00:00+00:00",
                 DISCLOSURES["cancel_policy"]["id"]))
    renters = next(p[3] for p in PRODUCTS if p[0] == "RENTERS")
    expires = (today + timedelta(days=29)).isoformat()
    for qid, guest, item in (
        ("QTE-7000", "GUEST-SEED0001", {"property_address": "5 Birch Ln, Springfield, IL 62704",
                                        "personal_property_value": "10000"}),
        ("QTE-6999", "GUEST-SEED0002", {"property_address": "201 River St, Apt 4B, Springfield, IL 62702",
                                        "personal_property_value": "25000"}),
    ):
        con.execute("INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (qid, guest, "RENTERS", "standard", 500, json.dumps(item),
                     price(renters, "RENTERS", "standard", 500, item, today), yesterday, expires, "applied"))
    con.executemany("INSERT INTO prospects VALUES (?,?,?,?,?,?,?,?,?)", [
        ("PRS-9000", "GUEST-SEED0001", "Omar Haddad", "omar.haddad@example.com", "217-555-0190", "1988-03-02",
         "5 Birch Ln, Springfield, IL 62704", yesterday, "new"),
        # Same name and date of birth as CUST-003: flagged internally as a possible existing customer.
        ("PRS-8999", "GUEST-SEED0002", "Sri", "sri.r@example.com", None, "1998-01-22",
         "201 River St, Apt 4B, Springfield, IL 62702", yesterday, "possible_existing_customer"),
    ])
    con.executemany("INSERT INTO applications (id, prospect_id, guest_id, quote_id, product_code, status, submitted_on) "
                    "VALUES (?,?,?,?,?,?,?)", [
                        ("APP-9500", "PRS-9000", "GUEST-SEED0001", "QTE-7000", "RENTERS", "submitted", yesterday),
                        ("APP-9499", "PRS-8999", "GUEST-SEED0002", "QTE-6999", "RENTERS", "submitted", yesterday)])
    con.executemany("INSERT INTO handoffs (id, customer_id, topic, summary, channel, contact, callback_time, status, "
                    "created_on) VALUES (?,?,?,?,?,?,?,?,?)", [
                        ("HND-3500", "CUST-003", "billing question",
                         "Asked how monthly billing works before buying renters cover.", "phone", "217-555-0177",
                         "tomorrow morning", "open", yesterday),
                        ("HND-3499", "CUST-002", "claim dispute",
                         "Disagrees with the estimate on claim CLM-5002 and wants a person to review it.", "email",
                         "indu@example.com", None, "open", yesterday)])


def init_db(db_path: Path, today: date | None = None, *, queue: bool = False) -> None:
    """(Re)create the database with deterministic demo data (queue=True adds a representative backlog)."""
    today = today or date.today()
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    # Reset in place (drop every table) rather than deleting the file, so a running server, or another
    # process with the file open, never blocks a reset (Windows can't delete open files).
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for t in tables:
        con.execute(f'DROP TABLE IF EXISTS "{t}"')
    con.commit()
    con.executescript(SCHEMA)
    for ddl in APP_TABLES_DDL:
        con.execute(ddl)
    _seed_representatives(con)
    con.executemany(
        "INSERT INTO customers VALUES (?,?,?,?,?)",
        [
            ("CUST-001", "Shan", "shan@example.com", "48 Oak Ave, Springfield, IL 62701", "1985-04-12"),
            ("CUST-002", "Indu", "indu@example.com", "9 Pine Rd, Shelbyville, IL 62565", "1990-09-30"),
            ("CUST-003", "Sri", "sri@example.com", "201 River St, Apt 4B, Springfield, IL 62702", "1998-01-22"),
        ],
    )
    con.executemany(
        "INSERT INTO products VALUES (?,?,?,?,?)",
        [(c, n, d, b, json.dumps(f)) for c, n, d, b, f in PRODUCTS],
    )
    start = today - timedelta(days=180)
    end = start + timedelta(days=365)
    due = today + timedelta(days=12)

    def pol(pid, cust, code, level, ded, item, balance):
        base = next(p[3] for p in PRODUCTS if p[0] == code)
        return (pid, cust, code, "active", level, ded, json.dumps(item), price(base, code, level, ded, item, today),
                start.isoformat(), end.isoformat(), balance, due.isoformat(), None, None, None)

    con.executemany(
        "INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            pol("POL-1001", "CUST-001", "AUTO", "standard", 500,
                {"vehicle_year": "2019", "vehicle_make": "Toyota", "vehicle_model": "Camry"}, 0.0),
            pol("POL-1002", "CUST-001", "HOME", "standard", 1000,
                {"property_address": "48 Oak Ave, Springfield, IL 62701", "year_built": "1998", "square_feet": "2200"},
                193.6),
            pol("POL-2001", "CUST-002", "AUTO", "premium", 250,
                {"vehicle_year": "2024", "vehicle_make": "Honda", "vehicle_model": "Civic"}, 0.0),
            pol("POL-2002", "CUST-002", "RENTERS", "basic", 500,
                {"property_address": "9 Pine Rd, Shelbyville, IL 62565", "personal_property_value": "20000"}, 0.0),
        ],
    )
    con.executemany(
        "INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
        [
            ("CLM-5001", "POL-1001", "CUST-001", (today - timedelta(days=140)).isoformat(), "collision",
             "Rear-ended at a stop light.", 2400.0, "approved", (today - timedelta(days=139)).isoformat()),
            ("CLM-5002", "POL-2001", "CUST-002", (today - timedelta(days=30)).isoformat(), "theft",
             "Catalytic converter stolen.", 1300.0, "in_review", (today - timedelta(days=29)).isoformat()),
        ],
    )
    added = (start + timedelta(days=10)).isoformat()
    con.execute("INSERT INTO drivers (id, policy_id, customer_id, full_name, date_of_birth, relationship, added_on) "
                "VALUES (?,?,?,?,?,?,?)", ("DRV-3001", "POL-2001", "CUST-002", "Kiran", "1988-02-11", "spouse", added))
    con.execute("INSERT INTO policy_riders VALUES (?,?,?,?,?,?,?,?,?)",
                ("RDR-4001", "POL-2001", "CUST-002", "ROADSIDE", RIDERS_BY_CODE["ROADSIDE"]["prices"]["AUTO"],
                 "active", added, added, None))
    # Premium = base x driver surcharges + riders (same rule as InsuranceBackend._premium).
    base = price(95.0, "AUTO", "premium", 250, {"vehicle_year": "2024"}, today)
    kiran_age = age_on(date(1988, 2, 11), today)
    con.execute("UPDATE policies SET monthly_premium=? WHERE id='POL-2001'",
                (round(base * (1 + driver_surcharge(kiran_age)) + RIDERS_BY_CODE["ROADSIDE"]["prices"]["AUTO"], 2),))
    if queue:
        _seed_queue(con, today)
    con.commit()
    con.close()


# ---------------------------------------------------------------------------- service
def _reference(result: Any) -> str | None:
    if isinstance(result, dict):
        for key in REFERENCE_KEYS:
            if result.get(key):
                return str(result[key])
    return None


def _strip_new_ids(preview: Any, arguments: dict[str, Any]) -> Any:
    """A validated-but-unsaved result: drop ids that would only exist once applied (they may change)."""
    if not isinstance(preview, dict):
        return preview
    known = {str(v) for v in arguments.values()}
    return {k: v for k, v in preview.items()
            if not (k.endswith("_id") and str(v) not in known) and k not in ("id", "status")}


def pathlib_name(filename: str) -> str:
    """Keep only a safe base name for display."""
    name = Path(str(filename).replace("\\", "/")).name.replace('"', "").strip() or "document"
    return name[:120]


class InsuranceBackend:
    """All operations available to ONE authenticated principal, fixed by the host app:

    * a customer (CUST-...): their own account;
    * a guest (GUEST-...): a prospective customer, products, quotes and applying only;
    * a representative (REP-...): works for one verified customer at a time. Which customer is never a tool
      argument: verify_customer opens a service context and every customer operation acts on it."""

    def __init__(self, db_path: Path | str, customer_id: str, today: date | None = None,
                 txn_policy: str | None = None):
        self.db_path = Path(db_path)
        self.principal = customer_id
        self.today = today or date.today()
        self.is_guest = customer_id.startswith(GUEST_PREFIX)
        self.is_rep = customer_id.startswith(REP_PREFIX)
        self._customer_id: str | None = None if self.is_rep else customer_id
        self.actor = f"{'guest' if self.is_guest else 'rep' if self.is_rep else 'customer'}:{customer_id}"
        self.rep: dict[str, Any] | None = None
        self._local = threading.local()  # dry_run flag: validate a request without saving it
        with self._conn() as con:
            for ddl in APP_TABLES_DDL:  # databases created before these tables existed
                con.execute(ddl)
            _seed_representatives(con)
            for table, column, ddl in (("transactions", "consent", "consent TEXT"),
                                       ("documents", "source", "source TEXT NOT NULL DEFAULT 'upload'"),
                                       ("audit_log", "actor", "actor TEXT"),
                                       ("handoffs", "resolution", "resolution TEXT")):
                if column not in {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}:
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
            driver_cols = {r["name"] for r in con.execute("PRAGMA table_info(drivers)")}
            if "status" not in driver_cols:  # drivers table from before driver removal existed
                con.execute("ALTER TABLE drivers ADD COLUMN status TEXT NOT NULL DEFAULT 'active'")
                con.execute("ALTER TABLE drivers ADD COLUMN removed_on TEXT")
            if self.is_rep:
                row = con.execute("SELECT * FROM representatives WHERE id=?", (customer_id,)).fetchone()
                if row is None:
                    raise NotFound(f"Representative {customer_id} does not exist")
                book = row["book"]
                self.rep = {**ROLES[row["role"]], "id": row["id"], "name": row["name"], "role": row["role"],
                            "book": "*" if book == "*" else json.loads(book)}
            elif not self.is_guest and not con.execute("SELECT 1 FROM customers WHERE id=?",
                                                       (customer_id,)).fetchone():
                raise NotFound(f"Customer {customer_id} does not exist")
        self.policy = (TransactionPolicy(role=self.rep["role"]) if self.rep else TransactionPolicy(txn_policy))
        self.load_context()

    # -- who the calls act for
    @property
    def customer_id(self) -> str:
        if self._customer_id is None:
            raise CustomerNotVerified("No customer is open. Find the customer with search_customers and verify them "
                                      "with verify_customer first.")
        return self._customer_id

    def _owner_id(self) -> str:
        """Who owns records made outside a customer context (a representative's prospect quotes)."""
        return self._customer_id or self.principal

    @property
    def rep_label(self) -> str:
        return f"{self.rep['name']} ({self.rep['id']})" if self.rep else self.actor

    def load_context(self) -> None:
        """Representatives: re-read which verified customer is open (called before every tool call)."""
        if not self.is_rep:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=SERVICE_CONTEXT_MINUTES)).isoformat(timespec="seconds")
        with self._conn() as con:
            row = con.execute("SELECT customer_id FROM service_contexts WHERE rep_id=? AND closed_at IS NULL "
                              "AND opened_at >= ? ORDER BY opened_at DESC LIMIT 1", (self.principal, cutoff)).fetchone()
        self._customer_id = row["customer_id"] if row else None

    def _in_book(self, customer_id: str) -> bool:
        return bool(self.rep) and (self.rep["book"] == "*" or customer_id in self.rep["book"])

    def _require_rep(self, *, approver: bool = False, licensed: bool = False) -> None:
        if not self.rep:
            raise NotPermitted("Only Harbor Mutual representatives can do this")
        if approver and not self.rep["approver"]:
            raise NotPermitted(f"A {self.rep['label'].lower()} can't approve requests; a supervisor reviews them")
        if licensed and not self.rep["licensed"]:
            raise NotPermitted("This needs a licensed agent; route it for review or hand it to a licensed agent")

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            if getattr(self._local, "dry_run", False):
                con.rollback()
            else:
                con.commit()
        finally:
            con.close()

    # -- helpers
    @staticmethod
    def _next_id(con: sqlite3.Connection, table: str, prefix: str, start: int) -> str:
        rows = con.execute(f"SELECT id FROM {table} WHERE id LIKE ?", (f"{prefix}-%",)).fetchall()
        nums = [int(r["id"].split("-")[1]) for r in rows]
        return f"{prefix}-{max(nums + [start - 1]) + 1}"

    @staticmethod
    def _policy_dict(row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["insured_item"] = json.loads(d["insured_item"])
        return d

    def _owned_policy(self, con: sqlite3.Connection, policy_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
        if row is None:
            raise NotFound(f"Policy {policy_id} not found")
        if row["customer_id"] != self.customer_id:
            raise AccessDenied(f"Policy {policy_id} does not belong to the signed-in customer")
        return row

    def _product(self, con: sqlite3.Connection, code: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM products WHERE code=?", (code.upper(),)).fetchone()
        if row is None:
            raise NotFound(f"Product {code} not found. Use list_products to see available products.")
        return row

    def audit(self, tool: str, arguments: dict[str, Any], outcome: str, detail: str | None = None) -> None:
        with self._conn() as con:
            con.execute(
                "INSERT INTO audit_log (customer_id, tool, arguments, outcome, detail, actor) VALUES (?,?,?,?,?,?)",
                (self._owner_id(), tool, json.dumps(arguments, sort_keys=True), outcome, detail, self.actor),
            )

    # -- transactions: every submit/commit request goes through execute()
    def execute(self, operation: str, arguments: dict[str, Any], repeat_of: str | None = None) -> dict[str, Any]:
        """Run a write operation at the level the transaction policy allows, and record it.

        Allowed to commit: applied now, recorded as committed (or submitted for submit-level operations).
        Capped at submit: validated without saving, recorded as pending_review for a reviewer.
        Capped below submit: refused with NOT_PERMITTED."""
        arguments = dict(arguments)
        consent = arguments.pop("disclosure_accepted", None)
        needed = OPERATION_LEVELS[operation]
        method = getattr(self, OPERATION_METHODS[operation])
        label = OPERATION_LABELS.get(operation, operation)
        if self.is_guest and operation not in GUEST_OPERATIONS:
            raise NotPermitted(f"{label} needs a signed-in customer. Ask them to sign in, or, if they are new, "
                               "help them get a quote and apply.")
        if needed == "prepare":  # non-binding (quotes): no transaction record
            return method(**arguments)
        level = self.policy.effective(operation, arguments)
        if rank(level) < rank("submit"):
            if self.rep:
                raise NotPermitted(f"{label} is outside a {self.rep['label'].lower()}'s authority. Route it to "
                                   "someone who holds it (a licensed agent or a supervisor).")
            raise NotPermitted(f"{label} can't be requested through self-service chat. Offer to connect the "
                               "customer with a Harbor Mutual representative.")
        if operation in DISCLOSURES and consent != DISCLOSURES[operation]["id"]:
            raise ConsentRequired(f"{label} needs the customer to accept a disclosure in the app's approval "
                                  "prompt; nothing was done.")
        if operation in self.policy.step_up:
            self._require_verified(label)
        subject = self._subject(operation, arguments)  # whose transaction this is (raises if no customer is open)
        self._local.consent = consent if operation in DISCLOSURES else None
        if operation in DUPLICATE_GUARDED:
            self._check_duplicate(operation, arguments, repeat_of)
        if rank(level) < rank(needed):
            preview = self._dry_run(method, arguments)
            txn = self._record(operation, arguments, "submit", "pending_review", None, preview, subject)
            return {"transaction_id": txn, "status": "pending_review", "applied": False,
                    "operation": label, "requested": arguments, "preview": _strip_new_ids(preview, arguments),
                    "next_step": ("Held for a supervisor's approval: it is beyond your role's authority. Nothing has "
                                  "changed yet." if self.rep else
                                  "Submitted for review by Harbor Mutual staff. Nothing has changed yet; the customer "
                                  "can check progress with this transaction id.")}
        result = method(**arguments)
        status = "submitted" if needed == "submit" else "committed"
        txn = self._record(operation, arguments, needed, status, _reference(result), result, subject)
        # Tracking details sit apart from the business result, so the reference the customer is given stays the
        # business id (CHG-, PAY-, CLM-, ...), not the internal transaction id.
        return {**result, "request_tracking": {"transaction_id": txn, "status": status}}

    def _dry_run(self, method: Any, arguments: dict[str, Any]) -> dict[str, Any]:
        self._local.dry_run = True
        try:
            return method(**arguments)
        finally:
            self._local.dry_run = False

    def _subject(self, operation: str, arguments: dict[str, Any]) -> str:
        """The customer (or prospect) a transaction is about."""
        if operation in ("issue_application", "decline_application"):
            with self._conn() as con:
                row = con.execute("SELECT prospect_id FROM applications WHERE id=?",
                                  (str(arguments.get("application_id", "")).upper(),)).fetchone()
            return row["prospect_id"] if row else str(arguments.get("application_id"))
        if operation in ("start_application", "get_quote"):
            return self._owner_id()
        return self.customer_id

    def _record(self, operation: str, arguments: dict[str, Any], level: str, status: str,
                reference: str | None, result: Any, subject: str | None = None) -> str:
        with self._conn() as con:
            txn = self._next_id(con, "transactions", "TXN", 10001)
            con.execute("INSERT INTO transactions (id, customer_id, operation, arguments, level, status, reference, "
                        "result, actor, created_on, created_at, consent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (txn, subject or self.customer_id, operation, json.dumps(arguments, sort_keys=True), level,
                         status,
                         reference, json.dumps(result, default=str), self.actor, self.today.isoformat(),
                         datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         getattr(self._local, "consent", None)))
        return txn

    def _check_duplicate(self, operation: str, arguments: dict[str, Any], repeat_of: str | None) -> None:
        since = (datetime.now(timezone.utc) - timedelta(minutes=DUPLICATE_WINDOW_MINUTES)).isoformat(timespec="seconds")
        with self._conn() as con:
            row = con.execute(
                "SELECT id, status, reference FROM transactions WHERE customer_id=? AND operation=? AND arguments=? "
                "AND status IN ('committed','submitted','pending_review') AND created_at >= ? ORDER BY id DESC",
                (self.customer_id, operation, json.dumps(arguments, sort_keys=True), since)).fetchone()
        if row is None or (repeat_of and repeat_of.upper() == row["id"]):
            return
        label = OPERATION_LABELS[operation].lower()
        raise DuplicateRequest(
            f"An identical {label} was already made a few minutes ago ({row['id']}"
            f"{', ' + row['reference'] if row['reference'] else ''}, status {row['status']}). Nothing was done again. "
            f"If the customer confirms they want a second identical {label}, call again with repeat_of={row['id']}.")

    def _transaction_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["arguments"] = json.loads(d["arguments"])
        result = json.loads(d.pop("result") or "null")
        d["operation_label"] = OPERATION_LABELS.get(d["operation"], d["operation"])
        d["status_meaning"] = TRANSACTION_STATUSES.get(d["status"], "")
        if d["status"] == "pending_review":
            d["preview"] = _strip_new_ids(result, d["arguments"])
        elif d["status"] in ("committed", "submitted"):
            d["result"] = result
        d.pop("created_at", None)
        return d

    def get_transaction(self, transaction_id: str) -> dict[str, Any]:
        with self._conn() as con:
            row = con.execute("SELECT * FROM transactions WHERE id=?", (transaction_id.upper(),)).fetchone()
        if row is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        if row["customer_id"] != self.customer_id:
            raise AccessDenied(f"Transaction {transaction_id} does not belong to the signed-in customer")
        return self._transaction_dict(row)

    def list_transactions(self, status: str | None = None) -> list[dict[str, Any]]:
        sql, params = "SELECT * FROM transactions WHERE customer_id=?", [self.customer_id]
        if status:
            sql += " AND status=?"
            params.append(status)
        with self._conn() as con:
            return [self._transaction_dict(r) for r in con.execute(sql + " ORDER BY id DESC", params)]

    def review_transaction(self, transaction_id: str, decision: str, reviewer: str,
                           note: str | None = None) -> dict[str, Any]:
        """Approve or reject a pending request. Staff/back-office use: never exposed as a customer tool.
        Approving applies the operation now; if a rule no longer allows it, the transaction fails."""
        if not reviewer.strip():
            raise InvalidRequest("reviewer is required")
        row = self._pending(transaction_id, decision, owner=None if self.is_rep else self._owner_id())
        return self._decide(row, decision, reviewer.strip(), note)

    def _pending(self, transaction_id: str, decision: str, owner: str | None = None) -> sqlite3.Row:
        if decision not in ("approve", "reject"):
            raise InvalidRequest("decision must be approve or reject")
        with self._conn() as con:
            row = con.execute("SELECT * FROM transactions WHERE id=?", (transaction_id.upper(),)).fetchone()
        if row is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        if owner is not None and row["customer_id"] != owner:
            raise AccessDenied(f"Transaction {transaction_id} does not belong to this customer")
        if row["status"] != "pending_review":
            raise InvalidRequest(f"Transaction {row['id']} is {row['status']}, not pending review")
        return row

    @classmethod
    def for_transaction(cls, db_path: Path | str, transaction_id: str, today: date | None = None) -> "InsuranceBackend":
        """A backend acting as whoever requested the transaction (the customer, or the representative working for
        that customer): approving applies the request exactly as they would have."""
        con = sqlite3.connect(db_path)
        try:
            row = con.execute("SELECT actor, customer_id FROM transactions WHERE id=?",
                              (transaction_id.upper(),)).fetchone()
        finally:
            con.close()
        if row is None:
            raise NotFound(f"Transaction {transaction_id} not found")
        kind, _, principal = row[0].partition(":")
        backend = cls(db_path, principal if kind in ("rep", "guest", "customer") else row[1], today)
        if backend.is_rep:
            backend._customer_id = row[1] if row[1].startswith("CUST-") else None
        return backend

    def _decide(self, row: sqlite3.Row, decision: str, reviewer: str, note: str | None) -> dict[str, Any]:
        reference, result = None, None
        if decision == "reject":
            status = "rejected"
        else:
            applier = InsuranceBackend.for_transaction(self.db_path, row["id"], self.today)
            try:
                result = getattr(applier, OPERATION_METHODS[row["operation"]])(**json.loads(row["arguments"]))
                status, reference = "committed", _reference(result)
            except (BackendError, ValueError) as exc:
                status, note = "failed", f"{note + ' | ' if note else ''}{exc}"
        with self._conn() as con:
            con.execute("UPDATE transactions SET status=?, reference=?, result=COALESCE(?, result), decided_on=?, "
                        "decided_by=?, decision_note=? WHERE id=?",
                        (status, reference, json.dumps(result, default=str) if result is not None else None,
                         self.today.isoformat(), reviewer, note, row["id"]))
            return self._transaction_dict(con.execute("SELECT * FROM transactions WHERE id=?",
                                                      (row["id"],)).fetchone())

    # -- identity re-check (step-up) before high-impact changes
    def _recent_checks(self, con: sqlite3.Connection, customer_id: str | None = None) -> list[sqlite3.Row]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=STEP_UP_MINUTES)).isoformat(timespec="seconds")
        return con.execute("SELECT outcome, checked_at FROM identity_checks WHERE customer_id=? AND checked_at >= ? "
                           "ORDER BY id DESC", (customer_id or self.customer_id, since)).fetchall()

    def _check_answers(self, con: sqlite3.Connection, customer_id: str, date_of_birth: str, postal_code: str,
                       method: str) -> tuple[bool, int, datetime]:
        """Compare answers with the record and store only the outcome. Returns (passed, earlier failures, when)."""
        failures = 0
        for r in self._recent_checks(con, customer_id):
            if r["outcome"] == "passed":
                break
            failures += 1
        if failures >= STEP_UP_MAX_FAILURES:
            raise NotPermitted("Too many failed identity checks. For the customer's protection the chat can't "
                               "try again now; offer to connect them with a representative.")
        cust = con.execute("SELECT date_of_birth, address FROM customers WHERE id=?", (customer_id,)).fetchone()
        zips = re.findall(r"\b(\d{5})(?:-\d{4})?\b", cust["address"])
        try:
            dob_ok = date.fromisoformat(date_of_birth.strip()).isoformat() == cust["date_of_birth"]
        except ValueError:
            dob_ok = False
        ok = dob_ok and bool(zips) and postal_code.strip()[:5] == zips[-1]
        now = datetime.now(timezone.utc)
        con.execute("INSERT INTO identity_checks (customer_id, method, outcome, checked_at) VALUES (?,?,?,?)",
                    (customer_id, method, "passed" if ok else "failed", now.isoformat(timespec="seconds")))
        return ok, failures, now

    def verify_identity(self, date_of_birth: str, postal_code: str) -> dict[str, Any]:
        """Check the person in the chat against the customer record: date of birth and ZIP code. Only the
        outcome is stored; the answers are never stored or echoed back."""
        if self.is_guest or self.is_rep:
            raise NotPermitted("Identity re-checks are for signed-in customers")
        with self._conn() as con:
            ok, failures, now = self._check_answers(con, self.customer_id, date_of_birth, postal_code,
                                                    "date_of_birth+zip")
        if ok:
            return {"verified": True,
                    "valid_until": (now + timedelta(minutes=STEP_UP_MINUTES)).isoformat(timespec="minutes")}
        left = STEP_UP_MAX_FAILURES - failures - 1
        return {"verified": False, "attempts_left": left,
                "message": "The details don't match our records. Don't say which detail was wrong."
                           + (" Ask the customer to try again." if left else
                              " No attempts left: offer to connect them with a representative.")}

    def _require_verified(self, label: str) -> None:
        with self._conn() as con:
            recent = self._recent_checks(con)
        if not recent or recent[0]["outcome"] != "passed":
            raise VerificationRequired(
                f"{label} needs a quick identity check first. Ask the customer for their date of birth and the ZIP "
                "code of their mailing address, call verify_identity, then try again. Never reveal the values on file.")

    # -- prospects (not signed in): applications
    def start_application(self, quote_id: str, full_name: str, email: str, date_of_birth: str, address: str,
                          phone: str | None = None) -> dict[str, Any]:
        if not (self.is_guest or self.is_rep):
            raise NotPermitted("Signed-in customers buy from a quote with purchase_policy")
        if self.is_rep and self._customer_id:
            raise NotPermitted(f"{self._customer_id} is open: they are already a customer, so quote and use "
                               "purchase_policy. To onboard someone new, end_customer_session first.")
        full_name, email, address = " ".join(full_name.split()), email.strip().lower(), " ".join(address.split())
        if len(full_name) < 2:
            raise InvalidRequest("full_name is required; ask the customer")
        if not EMAIL_RE.match(email):
            raise InvalidRequest("That email address doesn't look valid; ask the customer to check it")
        dob = date.fromisoformat(date_of_birth)
        if not (18 <= age_on(dob, self.today) <= 120):
            raise InvalidRequest("Applicants must be at least 18; a parent or guardian can apply instead")
        if len(address) < 10 or not any(ch.isdigit() for ch in address):
            raise InvalidRequest("Address looks incomplete; include street, city, state and ZIP")
        if phone is not None and len(re.sub(r"\D", "", phone)) < 10:
            raise InvalidRequest("Phone number looks incomplete; ask the customer")
        with self._conn() as con:
            q = con.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
            if q is None:
                raise NotFound(f"Quote {quote_id} not found")
            if q["customer_id"] != self._owner_id():
                raise AccessDenied(f"Quote {quote_id} was not created in this chat")
            if q["status"] != "open":
                raise InvalidRequest(f"Quote {quote_id} was already used (status: {q['status']})")
            if date.fromisoformat(q["expires_on"]) < self.today:
                raise InvalidRequest(f"Quote {quote_id} expired on {q['expires_on']}; get a new quote")
            # Matching rules: never a second prospect for the same email, and a possible existing customer is
            # flagged for the representative. The applicant is told the same thing either way.
            prospect = con.execute("SELECT id FROM prospects WHERE email=? ORDER BY id LIMIT 1", (email,)).fetchone()
            customer = con.execute("SELECT 1 FROM customers WHERE lower(email)=? OR (lower(name)=? AND date_of_birth=?)",
                                   (email, full_name.lower(), dob.isoformat())).fetchone()
            if prospect:
                prospect_id = prospect["id"]
                if customer:
                    con.execute("UPDATE prospects SET match_status='possible_existing_customer' WHERE id=?",
                                (prospect_id,))
            else:
                prospect_id = self._next_id(con, "prospects", "PRS", 9001)
                con.execute("INSERT INTO prospects VALUES (?,?,?,?,?,?,?,?,?)",
                            (prospect_id, self._owner_id(), full_name, email, phone, dob.isoformat(), address,
                             self.today.isoformat(), "possible_existing_customer" if customer else "new"))
            app_id = self._next_id(con, "applications", "APP", 9501)
            con.execute("INSERT INTO applications (id, prospect_id, guest_id, quote_id, product_code, status, "
                        "submitted_on) VALUES (?,?,?,?,?,?,?)",
                        (app_id, prospect_id, self._owner_id(), quote_id, q["product_code"], "submitted",
                         self.today.isoformat()))
            con.execute("UPDATE quotes SET status='applied' WHERE id=?", (quote_id,))
        return {"application_id": app_id, "quote_id": quote_id, "product_code": q["product_code"],
                "monthly_premium": q["monthly_premium"], "status": "submitted", "is_policy": False,
                "next_step": f"A licensed Harbor Mutual representative reviews the application and contacts the "
                             f"applicant at {email}. It is not a policy yet: nothing is covered until they confirm."}

    def get_application(self, application_id: str) -> dict[str, Any]:
        with self._conn() as con:
            row = con.execute("SELECT a.*, q.monthly_premium FROM applications a JOIN quotes q ON q.id = a.quote_id "
                              "WHERE a.id=?", (application_id.upper(),)).fetchone()
        if row is None:
            raise NotFound(f"Application {application_id} not found")
        if not self.is_rep and row["guest_id"] != self._owner_id():
            raise AccessDenied(f"Application {application_id} was not made in this chat session")
        d = {k: row[k] for k in ("id", "quote_id", "product_code", "monthly_premium", "status", "submitted_on",
                                 "decided_on")}
        d["application_id"] = d.pop("id")
        d["meaning"] = {"submitted": "Being reviewed by a representative. Not a policy yet.",
                        "approved": "Approved; the representative will confirm the start date. Not covered until "
                                    "the policy is issued.",
                        "declined": "Not approved. A representative explains why.",
                        "issued": "Policy issued."}.get(d["status"], "")
        return d

    # -- talking to a person
    def request_representative(self, topic: str, summary: str, preferred_channel: str,
                               contact: str | None = None, callback_time: str | None = None) -> dict[str, Any]:
        if preferred_channel not in ("phone", "email", "chat"):
            raise InvalidRequest("preferred_channel must be phone, email or chat")
        if not summary.strip() or not topic.strip():
            raise InvalidRequest("topic and summary are required")
        contact = (contact or "").strip()
        if not contact and preferred_channel == "email" and not self.is_guest:
            contact = self.get_profile()["email"]
        if not contact and (preferred_channel != "chat" or self.is_guest):
            raise InvalidRequest("Ask the customer for the "
                                 f"{'phone number' if preferred_channel == 'phone' else 'email address'} to use")
        if preferred_channel == "email" and not EMAIL_RE.match(contact):
            raise InvalidRequest("That email address doesn't look valid; ask the customer to check it")
        if preferred_channel == "phone" and len(re.sub(r"\D", "", contact)) < 10:
            raise InvalidRequest("Phone number looks incomplete; ask the customer")
        with self._conn() as con:
            hid = self._next_id(con, "handoffs", "HND", 3501)
            con.execute("INSERT INTO handoffs (id, customer_id, topic, summary, channel, contact, callback_time, status, "
                        "created_on) VALUES (?,?,?,?,?,?,?,?,?)",
                        (hid, self.customer_id, topic.strip()[:120], summary.strip()[:2000], preferred_channel,
                         contact or "chat", callback_time, "open", self.today.isoformat()))
        reach = {"phone": f"call {contact}", "email": f"email {contact}",
                 "chat": "reply in this chat" if not self.is_guest else f"contact you at {contact}"}[preferred_channel]
        return {"handoff_id": hid, "status": "open", "channel": preferred_channel,
                "next_step": f"A Harbor Mutual representative will {reach}"
                             + (f" around {callback_time}" if callback_time else "")
                             + ", usually within one business day."}

    # -- representatives (persona 2)
    def my_role(self) -> dict[str, Any]:
        self._require_rep()
        assert self.rep is not None
        active = None
        if self._customer_id:
            with self._conn() as con:
                name = con.execute("SELECT name FROM customers WHERE id=?", (self._customer_id,)).fetchone()
            active = {"customer_id": self._customer_id, "name": name["name"] if name else None}
        ops = {op: self.policy.effective(op) for op in OPERATION_LEVELS if op not in ("request_representative",)}
        return {"rep_id": self.rep["id"], "name": self.rep["name"], "role": self.rep["role"],
                "role_label": self.rep["label"], "licensed": self.rep["licensed"],
                "can_approve_others_requests": self.rep["approver"],
                "book": "all customers" if self.rep["book"] == "*" else self.rep["book"],
                "active_customer": active,
                "operation_levels": ops, "level_overrides": {op: v for op in ops if (v := self.policy.variants(op))},
                "levels_meaning": "commit = done now; submit = held for a supervisor's approval (pending_review) "
                                  "unless the operation itself is a submission; read = not allowed"}

    @staticmethod
    def _mask_email(email: str) -> str:
        user, _, domain = email.partition("@")
        return f"{user[:1]}***@{domain}" if domain else "***"

    @staticmethod
    def _city(address: str) -> str:
        parts = [p.strip() for p in address.split(",")]
        if len(parts) >= 2:
            return f"{parts[-2]}, {parts[-1].split()[0] if parts[-1].split() else ''}".rstrip(", ")
        return ""

    def search_customers(self, query: str) -> dict[str, Any]:
        """Approved identifiers only: customer id, policy id, email, or name. Results are limited to the
        representative's book and to what's needed to pick the right person (no DOB, no full address)."""
        self._require_rep()
        q = " ".join(query.split())
        if len(q) < 3:
            raise InvalidRequest("Search with a customer id, policy id, email address, or at least 3 letters of a name")
        with self._conn() as con:
            up = q.upper()
            if up.startswith("CUST-"):
                rows = con.execute("SELECT * FROM customers WHERE id=?", (up,)).fetchall()
            elif up.startswith("POL-"):
                rows = con.execute("SELECT c.* FROM customers c JOIN policies p ON p.customer_id = c.id WHERE p.id=?",
                                   (up,)).fetchall()
            elif "@" in q:
                rows = con.execute("SELECT * FROM customers WHERE lower(email)=?", (q.lower(),)).fetchall()
            else:
                rows = con.execute("SELECT * FROM customers WHERE lower(name) LIKE ? ORDER BY id",
                                   (f"%{q.lower()}%",)).fetchall()
            customers, outside = [], 0
            for r in rows:
                if not self._in_book(r["id"]):
                    outside += 1  # counted, never shown: the caller belongs to another service team
                    continue
                n = con.execute("SELECT COUNT(*) FROM policies WHERE customer_id=? AND status='active'",
                                (r["id"],)).fetchone()[0]
                customers.append({"customer_id": r["id"], "name": r["name"], "email": self._mask_email(r["email"]),
                                  "city": self._city(r["address"]), "active_policies": n})
            prospects = []
            if not up.startswith(("CUST-", "POL-")):
                prows = (con.execute("SELECT * FROM prospects WHERE email=?", (q.lower(),)).fetchall() if "@" in q else
                         con.execute("SELECT * FROM prospects WHERE lower(full_name) LIKE ? ORDER BY id",
                                     (f"%{q.lower()}%",)).fetchall())
                for p in prows[:5]:
                    apps = [dict(a) for a in con.execute(
                        "SELECT id AS application_id, product_code, status FROM applications WHERE prospect_id=?",
                        (p["id"],))]
                    prospects.append({"prospect_id": p["id"], "name": p["full_name"],
                                      "email": self._mask_email(p["email"]), "applications": apps})
        out = {"customers": customers[:5], "prospects": prospects,
               "next_step": "Verify a customer with verify_customer (date of birth and ZIP they give you) before "
                            "discussing or changing anything. Only customers in your book are listed."}
        if outside:
            out["matches_outside_your_book"] = outside
            out["next_step"] += (" Some matches are outside your book: you can't open them; transfer the caller to "
                                 "their service team.")
        return out

    def verify_customer(self, customer_id: str, date_of_birth: str, postal_code: str) -> dict[str, Any]:
        """Check the caller's answers against the record. A pass opens this customer for the representative
        (closing any other); only the outcome is stored, never the answers."""
        self._require_rep()
        cid = customer_id.strip().upper()
        with self._conn() as con:
            cust = con.execute("SELECT id, name FROM customers WHERE id=?", (cid,)).fetchone()
            if cust is None:
                raise NotFound(f"Customer {cid} not found; use search_customers")
            if not self._in_book(cid):
                raise AccessDenied(f"Customer {cid} is not in your assigned book; transfer the caller to their "
                                   "service team")
            ok, failures, now = self._check_answers(con, cid, date_of_birth, postal_code,
                                                    f"rep:{self.principal} date_of_birth+zip")
            if ok:
                stamp = now.isoformat(timespec="seconds")
                con.execute("UPDATE service_contexts SET closed_at=? WHERE rep_id=? AND closed_at IS NULL",
                            (stamp, self.principal))
                ctx = self._next_id(con, "service_contexts", "SVC", 1)
                con.execute("INSERT INTO service_contexts VALUES (?,?,?,?,?,?)",
                            (ctx, self.principal, cid, stamp, stamp, None))
        if not ok:
            left = STEP_UP_MAX_FAILURES - failures - 1
            return {"verified": False, "customer_id": cid, "attempts_left": left,
                    "message": "The answers don't match the record. Don't tell the caller which one was wrong."
                               + ("" if left else " No attempts left: don't discuss the account; arrange a "
                                                  "follow-up through the customer's contact details on file.")}
        self._customer_id = cid
        return {"verified": True, "customer_id": cid, "name": cust["name"], "service_context": ctx,
                "next_step": f"Customer tools now act for {cust['name']} ({cid}) until end_customer_session or "
                             f"{SERVICE_CONTEXT_MINUTES} minutes without activity."}

    def end_customer_session(self) -> dict[str, Any]:
        self._require_rep()
        if self._customer_id is None:
            raise InvalidRequest("No customer is open, so there is nothing to close")
        with self._conn() as con:
            con.execute("UPDATE service_contexts SET closed_at=? WHERE rep_id=? AND closed_at IS NULL",
                        (datetime.now(timezone.utc).isoformat(timespec="seconds"), self.principal))
        closed, self._customer_id = self._customer_id, None
        return {"closed": closed, "next_step": "No customer is open now."}

    def service_history(self) -> dict[str, Any]:
        """Prior requests, cases, notes and verification outcomes for the open customer (internal view)."""
        self._require_rep()
        cid = self.customer_id
        with self._conn() as con:
            txns = [{"transaction_id": r["id"], "operation": OPERATION_LABELS.get(r["operation"], r["operation"]),
                     "status": r["status"], "reference": r["reference"], "created_on": r["created_on"],
                     "requested_by": r["actor"], "decided_by": r["decided_by"]}
                    for r in con.execute("SELECT * FROM transactions WHERE customer_id=? ORDER BY id DESC LIMIT 20",
                                         (cid,))]
            cases = [dict(r) for r in con.execute(
                "SELECT id AS handoff_id, topic, summary, channel, status, created_on, assigned_to, resolution "
                "FROM handoffs WHERE customer_id=? ORDER BY id DESC", (cid,))]
            notes = [dict(r) for r in con.execute(
                "SELECT id AS note_id, kind, text, due_on, status, rep_id, created_on FROM notes WHERE customer_id=? "
                "ORDER BY id DESC", (cid,))]
            checks = [dict(r) for r in con.execute(
                "SELECT method, outcome, checked_at FROM identity_checks WHERE customer_id=? ORDER BY id DESC LIMIT 5",
                (cid,))]
        return {"customer_id": cid, "transactions": txns, "cases": cases, "notes": notes,
                "identity_checks": checks, "internal_only": True}

    def add_note(self, text: str, follow_up_on: str | None = None) -> dict[str, Any]:
        """An internal interaction note; with follow_up_on it is a follow-up task in the representative's queue."""
        self._require_rep()
        cid = self.customer_id
        text = " ".join(text.split())
        if len(text) < 3:
            raise InvalidRequest("The note is empty")
        due = date.fromisoformat(follow_up_on).isoformat() if follow_up_on else None
        if due and due < self.today.isoformat():
            raise InvalidRequest("follow_up_on can't be in the past")
        with self._conn() as con:
            nid = self._next_id(con, "notes", "NTE", 4501)
            con.execute("INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?)",
                        (nid, cid, self.principal, "task" if due else "note", text[:2000], due,
                         "open" if due else "recorded", self.today.isoformat(), None))
        return {"note_id": nid, "customer_id": cid, "kind": "task" if due else "note", "due_on": due,
                "visibility": "internal: never shown to the customer"}

    def complete_task(self, task_id: str) -> dict[str, Any]:
        self._require_rep()
        with self._conn() as con:
            row = con.execute("SELECT * FROM notes WHERE id=? AND kind='task'", (task_id.upper(),)).fetchone()
            if row is None:
                raise NotFound(f"Task {task_id} not found")
            if row["rep_id"] != self.principal and not self.rep["approver"]:
                raise AccessDenied(f"Task {task_id} belongs to another representative")
            if row["status"] == "done":
                raise InvalidRequest(f"Task {row['id']} is already done")
            con.execute("UPDATE notes SET status='done', completed_on=? WHERE id=?", (self.today.isoformat(), row["id"]))
        return {"task_id": row["id"], "status": "done"}

    def work_queue(self, kind: str = "all") -> dict[str, Any]:
        """What's waiting: requests held for approval, applications, customer cases, and my follow-up tasks."""
        self._require_rep()
        assert self.rep is not None
        if kind not in ("all", "requests", "applications", "cases", "tasks"):
            raise InvalidRequest("kind must be all, requests, applications, cases or tasks")
        out: dict[str, Any] = {}
        with self._conn() as con:
            if kind in ("all", "requests"):
                out["requests_pending_review"] = [
                    {"transaction_id": r["id"], "customer_id": r["customer_id"],
                     "operation": OPERATION_LABELS.get(r["operation"], r["operation"]),
                     "arguments": json.loads(r["arguments"]), "requested_by": r["actor"], "created_on": r["created_on"],
                     "you_can_approve": self.rep["approver"] and r["actor"] != self.actor}
                    for r in con.execute("SELECT * FROM transactions WHERE status='pending_review' ORDER BY id")
                    if self._in_book(r["customer_id"]) or self.rep["approver"]]
            if kind in ("all", "applications"):
                out["applications"] = [
                    {"application_id": r["id"], "prospect_id": r["prospect_id"], "applicant": r["full_name"],
                     "product_code": r["product_code"], "monthly_premium": r["monthly_premium"],
                     "submitted_on": r["submitted_on"], "match_check": r["match_status"],
                     "you_can_decide": self.rep["licensed"]}
                    for r in con.execute(
                        "SELECT a.*, p.full_name, p.match_status, q.monthly_premium FROM applications a "
                        "JOIN prospects p ON p.id = a.prospect_id JOIN quotes q ON q.id = a.quote_id "
                        "WHERE a.status='submitted' ORDER BY a.id")]
            if kind in ("all", "cases"):
                out["cases"] = [
                    {"handoff_id": r["id"], "customer_id": r["customer_id"], "topic": r["topic"], "summary": r["summary"],
                     "channel": r["channel"], "contact": r["contact"], "callback_time": r["callback_time"],
                     "status": r["status"], "assigned_to": r["assigned_to"], "created_on": r["created_on"]}
                    for r in con.execute("SELECT * FROM handoffs WHERE status IN ('open','assigned') ORDER BY id")
                    if not r["customer_id"].startswith("CUST-") or self._in_book(r["customer_id"])]
            if kind in ("all", "tasks"):
                out["my_tasks"] = [dict(r) for r in con.execute(
                    "SELECT id AS task_id, customer_id, text, due_on FROM notes WHERE kind='task' AND status='open' "
                    "AND rep_id=? ORDER BY due_on, id", (self.principal,))]
        out["internal_only"] = True
        return out

    def review_request(self, transaction_id: str, decision: str, note: str | None = None) -> dict[str, Any]:
        """Approve or reject a request held for review. Dual control: never your own request."""
        self._require_rep(approver=True)
        row = self._pending(transaction_id, decision)
        if row["actor"] == self.actor:
            raise NotPermitted(f"You requested {row['id']} yourself; another approver must review it (dual control)")
        if decision == "reject" and not (note or "").strip():
            raise InvalidRequest("Give a reason (note) when rejecting")
        return self._decide(row, decision, self.rep_label, note)

    def update_handoff(self, handoff_id: str, action: str, resolution: str | None = None) -> dict[str, Any]:
        self._require_rep()
        if action not in ("assign", "close"):
            raise InvalidRequest("action must be assign or close")
        with self._conn() as con:
            row = con.execute("SELECT * FROM handoffs WHERE id=?", (handoff_id.upper(),)).fetchone()
            if row is None:
                raise NotFound(f"Case {handoff_id} not found")
            if row["customer_id"].startswith("CUST-") and not self._in_book(row["customer_id"]):
                raise AccessDenied(f"Case {row['id']} is for a customer outside your book")
            if row["status"] == "closed":
                raise InvalidRequest(f"Case {row['id']} is already closed")
            if action == "assign":
                con.execute("UPDATE handoffs SET status='assigned', assigned_to=? WHERE id=?", (self.rep_label, row["id"]))
                status = "assigned"
            else:
                if not (resolution or "").strip():
                    raise InvalidRequest("Say how the case was resolved (resolution)")
                con.execute("UPDATE handoffs SET status='closed', closed_on=?, resolution=?, "
                            "assigned_to=COALESCE(assigned_to, ?) WHERE id=?",
                            (self.today.isoformat(), resolution.strip()[:1000], self.rep_label, row["id"]))
                status = "closed"
        return {"handoff_id": row["id"], "status": status, "assigned_to": self.rep_label}

    def issue_application(self, application_id: str, existing_customer_id: str | None = None,
                          start_date: str | None = None) -> dict[str, Any]:
        """Underwriting decision: issue the policy an applicant applied for. Creates the customer record unless the
        applicant is an existing customer the representative has matched (existing_customer_id)."""
        self._require_rep()
        start = date.fromisoformat(start_date) if start_date else self.today
        if start < self.today:
            raise InvalidRequest("start_date cannot be in the past")
        with self._conn() as con:
            a = con.execute("SELECT * FROM applications WHERE id=?", (application_id.upper(),)).fetchone()
            if a is None:
                raise NotFound(f"Application {application_id} not found")
            if a["status"] != "submitted":
                raise InvalidRequest(f"Application {a['id']} is {a['status']}, not waiting for a decision")
            p = con.execute("SELECT * FROM prospects WHERE id=?", (a["prospect_id"],)).fetchone()
            q = con.execute("SELECT * FROM quotes WHERE id=?", (a["quote_id"],)).fetchone()
            if date.fromisoformat(q["expires_on"]) < self.today:
                raise InvalidRequest(f"Quote {q['id']} expired on {q['expires_on']}; the applicant needs a new quote")
            if existing_customer_id:
                c = con.execute("SELECT * FROM customers WHERE id=?", (existing_customer_id.upper(),)).fetchone()
                if c is None:
                    raise NotFound(f"Customer {existing_customer_id} not found")
                if not self._in_book(c["id"]):
                    raise AccessDenied(f"Customer {c['id']} is not in your assigned book")
                if self._customer_id != c["id"]:  # adding a policy to someone's account: they must be verified
                    raise CustomerNotVerified(f"Verify {c['id']} first (verify_customer with the date of birth and ZIP "
                                              "the applicant gives), then issue to that customer.")
                same_person = (c["email"].lower() == p["email"] or
                               (c["name"].lower() == p["full_name"].lower() and c["date_of_birth"] == p["date_of_birth"]))
                if not same_person:
                    raise InvalidRequest(f"{c['id']} doesn't match the applicant's email or name and date of birth")
                cid, new = c["id"], False
            else:
                if p["match_status"] == "possible_existing_customer":
                    raise InvalidRequest("The applicant may already be a customer (internal match check). Search for "
                                         "them, confirm with the applicant, then issue with existing_customer_id, or "
                                         "decline the application.")
                nums = [int(r[0].split("-")[1]) for r in con.execute("SELECT id FROM customers WHERE id LIKE 'CUST-%'")]
                cid, new = f"CUST-{max(nums + [0]) + 1:03d}", True
                con.execute("INSERT INTO customers VALUES (?,?,?,?,?)",
                            (cid, p["full_name"], p["email"], p["address"], p["date_of_birth"]))
            pid = self._bind(con, q, cid, start)
            con.execute("UPDATE applications SET status='issued', decided_on=?, decided_by=? WHERE id=?",
                        (self.today.isoformat(), self.rep_label, a["id"]))
        return {"policy_id": pid, "application_id": a["id"], "customer_id": cid, "new_customer": new,
                "product_code": q["product_code"], "monthly_premium": q["monthly_premium"],
                "start_date": start.isoformat(), "status": "issued",
                "next_step": f"Tell the applicant their policy {pid} is issued and starts {start.isoformat()}; the "
                             f"first premium is billed on the start date."}

    def decline_application(self, application_id: str, reason: str) -> dict[str, Any]:
        self._require_rep()
        if not reason.strip():
            raise InvalidRequest("A reason is required")
        with self._conn() as con:
            a = con.execute("SELECT * FROM applications WHERE id=?", (application_id.upper(),)).fetchone()
            if a is None:
                raise NotFound(f"Application {application_id} not found")
            if a["status"] != "submitted":
                raise InvalidRequest(f"Application {a['id']} is {a['status']}, not waiting for a decision")
            con.execute("UPDATE applications SET status='declined', decided_on=?, decided_by=?, decision_note=? "
                        "WHERE id=?", (self.today.isoformat(), self.rep_label, reason.strip()[:1000], a["id"]))
        return {"application_id": a["id"], "status": "declined",
                "next_step": "Nothing was issued. Explain the decision to the applicant through their contact details."}

    # -- documents Harbor Mutual issues (declarations page, proof of insurance, ID card)
    def policy_document(self, policy_id: str, document_type: str) -> dict[str, Any]:
        label = POLICY_DOCUMENT_TYPES.get(document_type)
        if label is None:
            raise InvalidRequest(f"document_type must be one of {list(POLICY_DOCUMENT_TYPES)}")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if document_type != "declarations" and p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is {p['status']}; only a declarations page is available")
            if document_type == "id_card" and p["product_code"] != "AUTO":
                raise InvalidRequest("ID cards are for auto policies; offer proof of insurance instead")
            filename = f"{policy_id} {label}.pdf"
            existing = con.execute("SELECT id, size_bytes FROM documents WHERE customer_id=? AND source='generated' "
                                   "AND filename=? AND uploaded_at=?",
                                   (self.customer_id, filename, self.today.isoformat())).fetchone()
            if existing:
                doc_id, size = existing["id"], existing["size_bytes"]
            else:
                cust = con.execute("SELECT name, address FROM customers WHERE id=?", (self.customer_id,)).fetchone()
                policy = self._with_riders(con, p)
                product = self._product(con, p["product_code"])
                lines = [f"Policyholder: {cust['name']}", f"Mailing address: {cust['address']}", "",
                         f"Policy: {policy_id}  ({product['name']})", f"Status: {p['status']}",
                         f"Policy period: {p['start_date']} to {p['end_date']}"]
                if document_type == "id_card":
                    item = policy["insured_item"]
                    lines.append(f"Vehicle: {item.get('vehicle_year', '')} {item.get('vehicle_make', '')} "
                                 f"{item.get('vehicle_model', '')}".rstrip())
                    lines += [f"Driver: {d['full_name']}" for d in con.execute(
                        "SELECT full_name FROM drivers WHERE policy_id=? AND status='active'", (policy_id,))]
                else:
                    lines += [f"Coverage level: {p['coverage_level']}", f"Deductible: ${p['deductible']:,}",
                              f"Monthly premium: ${p['monthly_premium']:,.2f}"]
                    lines += [f"Insured {k.replace('_', ' ')}: {v}" for k, v in policy["insured_item"].items()]
                    lines += [f"Rider: {r['name']} (${r['monthly_cost']:,.2f}/month)" for r in policy["riders"]]
                lines += ["", f"Issued {self.today.isoformat()} by Harbor Mutual Insurance (demo document)."]
                content = text_pdf(f"Harbor Mutual - {label}", lines)
                doc_id, size = self._next_id(con, "documents", "DOC", 6001), len(content)
                con.execute("INSERT INTO documents (id, customer_id, conversation_id, filename, content_type, "
                            "size_bytes, sha256, content, uploaded_at, used_for_change, source) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (doc_id, self.customer_id, None, filename, "application/pdf", size,
                             hashlib.sha256(content).hexdigest(), content, self.today.isoformat(), None, "generated"))
        return {"document_id": doc_id, "document_type": document_type, "filename": filename, "size_bytes": size,
                "download_url": f"/api/documents/{doc_id}", "issued_on": self.today.isoformat(),
                "delivery": "Secure link: it opens only for the signed-in customer."}

    # -- profile
    def get_profile(self) -> dict[str, Any]:
        with self._conn() as con:
            return dict(con.execute("SELECT * FROM customers WHERE id=?", (self.customer_id,)).fetchone())

    # -- proof documents & profile changes
    def store_document(self, filename: str, content_type: str, content: bytes,
                       conversation_id: str | None = None) -> dict[str, Any]:
        """Save a file the customer uploaded in the chat. Called by the web app / CLI, not the model."""
        content_type = (content_type or "").split(";")[0].strip().lower()
        if content_type == "image/jpg":
            content_type = "image/jpeg"
        if content_type not in DOCUMENT_SIGNATURES:
            raise InvalidRequest("Only PDF, JPG, PNG or WebP files can be uploaded")
        if not content:
            raise InvalidRequest("The file is empty")
        if len(content) > MAX_DOCUMENT_BYTES:
            raise InvalidRequest(f"Files can be at most {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB")
        if not any(content.startswith(sig) for sig in DOCUMENT_SIGNATURES[content_type]):
            raise InvalidRequest(f"The file does not look like a valid {content_type} file")
        name = pathlib_name(filename)
        with self._conn() as con:
            doc_id = self._next_id(con, "documents", "DOC", 6001)
            con.execute("INSERT INTO documents (id, customer_id, conversation_id, filename, content_type, size_bytes, "
                        "sha256, content, uploaded_at, used_for_change, source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (doc_id, self.customer_id, conversation_id, name, content_type, len(content),
                         hashlib.sha256(content).hexdigest(), content, self.today.isoformat(), None, "upload"))
        return {"document_id": doc_id, "filename": name, "content_type": content_type, "size_bytes": len(content)}

    def get_document(self, document_id: str) -> dict[str, Any]:
        """Document including its bytes, for the owner only (used by the download endpoint)."""
        with self._conn() as con:
            row = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
        if row is None:
            raise NotFound(f"Document {document_id} not found")
        if row["customer_id"] != self.customer_id:
            raise AccessDenied(f"Document {document_id} does not belong to the signed-in customer")
        return dict(row)

    def list_documents(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            return [dict(r) for r in con.execute(
                "SELECT id AS document_id, filename, content_type, size_bytes, uploaded_at, used_for_change, source "
                "FROM documents WHERE customer_id=? ORDER BY id", (self.customer_id,))]

    def change_requirements(self, change_type: str) -> dict[str, Any]:
        spec = CHANGE_TYPES.get(change_type)
        if spec is None:
            raise InvalidRequest(f"change_type must be one of {list(CHANGE_TYPES)}")
        current = self.get_profile()[spec["field"]]
        return {"change_type": change_type, "label": spec["label"], "current_value": current,
                "ask_customer_for": spec["questions"], "accepted_documents": spec["accepted_documents"],
                "notes": spec["notes"],
                "how_to_upload": "The customer attaches the file with the paperclip button in the chat; "
                                 "it then appears in their message as [Attached document DOC-...]."}

    def submit_profile_change(self, change_type: str, new_value: str, document_id: str, document_type: str,
                              reason: str | None = None, effective_date: str | None = None) -> dict[str, Any]:
        spec = CHANGE_TYPES.get(change_type)
        if spec is None:
            raise InvalidRequest(f"change_type must be one of {list(CHANGE_TYPES)}")
        if document_type not in spec["accepted_documents"]:
            raise InvalidRequest(f"A {document_type} is not accepted as proof for a {spec['label'].lower()} change. "
                                 f"Accepted: {', '.join(spec['accepted_documents'].values())}")
        new_value = " ".join(new_value.split())
        # The follow-up answers are required, not optional: the backend refuses the change without them.
        if change_type == "mailing_address" and not effective_date:
            raise InvalidRequest("effective_date (the date the customer moved) is required; ask the customer")
        if change_type == "date_of_birth" and not (reason or "").strip():
            raise InvalidRequest("reason for the correction is required; ask the customer")
        if change_type == "mailing_address":
            if len(new_value) < 10 or not any(ch.isdigit() for ch in new_value):
                raise InvalidRequest("Address looks incomplete; include street, city, state and ZIP")
        else:
            dob = date.fromisoformat(new_value)
            if not (16 <= age_on(dob, self.today) <= 120):
                raise InvalidRequest("Date of birth gives an age outside 16-120")
            new_value = dob.isoformat()
        eff = date.fromisoformat(effective_date).isoformat() if effective_date else None
        with self._conn() as con:
            doc = con.execute("SELECT * FROM documents WHERE id=?", (document_id,)).fetchone()
            if doc is None:
                raise NotFound(f"Document {document_id} not found; ask the customer to attach the proof in the chat")
            if doc["customer_id"] != self.customer_id:
                raise AccessDenied(f"Document {document_id} does not belong to the signed-in customer")
            if doc["used_for_change"]:
                raise InvalidRequest(f"Document {document_id} was already used for change {doc['used_for_change']}; "
                                     "ask for a new proof document")
            old_value = con.execute(f"SELECT {spec['field']} FROM customers WHERE id=?",
                                    (self.customer_id,)).fetchone()[0]
            if old_value.strip().casefold() == new_value.casefold():
                raise InvalidRequest(f"The {spec['label'].lower()} on file is already {old_value}")
            change_id = self._next_id(con, "profile_changes", "CHG", 8001)
            con.execute("INSERT INTO profile_changes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (change_id, self.customer_id, change_type, old_value, new_value, document_id, document_type,
                         reason, eff, "applied", self.today.isoformat()))
            con.execute(f"UPDATE customers SET {spec['field']}=? WHERE id=?", (new_value, self.customer_id))
            con.execute("UPDATE documents SET used_for_change=? WHERE id=?", (change_id, document_id))
        return {"change_id": change_id, "change_type": change_type, "old_value": old_value, "new_value": new_value,
                "proof_document_id": document_id, "document_type": document_type, "effective_date": eff,
                "status": "applied"}

    def update_address(self, new_address: str) -> dict[str, Any]:
        """Internal/admin use only. Customers change their address via submit_profile_change with a proof."""
        if len(new_address.strip()) < 8:
            raise InvalidRequest("Address looks incomplete; include street, city, state and ZIP.")
        with self._conn() as con:
            old = con.execute("SELECT address FROM customers WHERE id=?", (self.customer_id,)).fetchone()["address"]
            con.execute("UPDATE customers SET address=? WHERE id=?", (new_address.strip(), self.customer_id))
        return {"customer_id": self.customer_id, "old_address": old, "new_address": new_address.strip()}

    # -- products & quotes
    def list_products(self) -> list[dict[str, Any]]:
        with self._conn() as con:
            rows = con.execute("SELECT * FROM products ORDER BY code").fetchall()
        return [
            {**dict(r), "required_fields": json.loads(r["required_fields"]),
             "coverage_levels": list(COVERAGE_FACTORS), "deductible_options": list(DEDUCTIBLE_FACTORS)}
            for r in rows
        ]

    def create_quote(self, product_code: str, coverage_level: str, deductible: int, insured_item: dict[str, Any]) -> dict:
        if coverage_level not in COVERAGE_FACTORS:
            raise InvalidRequest(f"coverage_level must be one of {list(COVERAGE_FACTORS)}")
        if deductible not in DEDUCTIBLE_FACTORS:
            raise InvalidRequest(f"deductible must be one of {list(DEDUCTIBLE_FACTORS)}")
        with self._conn() as con:
            product = self._product(con, product_code)
            required = json.loads(product["required_fields"])
            missing = [f for f in required if not str(insured_item.get(f, "")).strip()]
            if missing:
                raise InvalidRequest(f"Missing insured item details for {product['code']}: {missing}. Ask the customer.")
            premium = price(product["base_monthly_premium"], product["code"], coverage_level, deductible,
                            insured_item, self.today)
            qid = self._next_id(con, "quotes", "QTE", 7001)
            expires = self.today + timedelta(days=30)
            con.execute(
                "INSERT INTO quotes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (qid, self._owner_id(), product["code"], coverage_level, deductible,
                 json.dumps({k: str(v) for k, v in insured_item.items()}), premium,
                 self.today.isoformat(), expires.isoformat(), "open"),
            )
        return {"quote_id": qid, "product_code": product["code"], "product_name": product["name"],
                "coverage_level": coverage_level, "deductible": deductible, "insured_item": insured_item,
                "monthly_premium": premium, "expires_on": expires.isoformat()}

    def purchase_policy(self, quote_id: str, start_date: str | None = None) -> dict[str, Any]:
        start = date.fromisoformat(start_date) if start_date else self.today
        if start < self.today:
            raise InvalidRequest("start_date cannot be in the past")
        with self._conn() as con:
            q = con.execute("SELECT * FROM quotes WHERE id=?", (quote_id,)).fetchone()
            if q is None:
                raise NotFound(f"Quote {quote_id} not found")
            if q["customer_id"] != self.customer_id:
                raise AccessDenied(f"Quote {quote_id} does not belong to the signed-in customer")
            if q["status"] != "open":
                raise InvalidRequest(f"Quote {quote_id} was already used (status: {q['status']})")
            if date.fromisoformat(q["expires_on"]) < self.today:
                raise InvalidRequest(f"Quote {quote_id} expired on {q['expires_on']}; request a new quote")
            pid = self._bind(con, q, self.customer_id, start)
            policy = self._policy_dict(con.execute("SELECT * FROM policies WHERE id=?", (pid,)).fetchone())
        return {"policy_id": pid, **policy}

    def _bind(self, con: sqlite3.Connection, q: sqlite3.Row, customer_id: str, start: date) -> str:
        """Issue a policy from a quote: the policy and its first premium bill."""
        pid = self._next_id(con, "policies", "POL", 1001)
        end = start + timedelta(days=365)
        con.execute(
            "INSERT INTO policies VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, customer_id, q["product_code"], "active", q["coverage_level"], q["deductible"],
             q["insured_item"], q["monthly_premium"], start.isoformat(), end.isoformat(),
             q["monthly_premium"], start.isoformat(), None, None, q["id"]),
        )
        con.execute("UPDATE quotes SET status='purchased' WHERE id=?", (q["id"],))
        return pid

    # -- policies
    def _own_policies_by_product(self, con: sqlite3.Connection) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for r in con.execute("SELECT id, product_code FROM policies WHERE customer_id=? AND status='active' "
                             "ORDER BY id", (self.customer_id,)):
            out.setdefault(r["product_code"], []).append(f"{r['product_code']} policy {r['id']}")
        return out

    def _with_riders(self, con: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        d = self._policy_dict(row)
        d["riders"] = [
            {"rider_code": r["rider_code"], "name": RIDERS_BY_CODE[r["rider_code"]]["name"],
             "monthly_cost": r["monthly_cost"], "coverage_starts": r["coverage_starts"]}
            for r in con.execute("SELECT * FROM policy_riders WHERE policy_id=? AND status='active' ORDER BY id",
                                 (row["id"],))
        ]
        return d

    def _riders_total(self, con: sqlite3.Connection, policy_id: str) -> float:
        row = con.execute("SELECT COALESCE(SUM(monthly_cost), 0) FROM policy_riders "
                          "WHERE policy_id=? AND status='active'", (policy_id,)).fetchone()
        return float(row[0])

    def _premium(self, con: sqlite3.Connection, p: sqlite3.Row, coverage_level: str, deductible: int) -> float:
        """Monthly premium = base price x driver surcharges + active riders."""
        product = self._product(con, p["product_code"])
        base = price(product["base_monthly_premium"], p["product_code"], coverage_level, deductible,
                     json.loads(p["insured_item"]), self.today)
        return round(base * self._driver_factor(con, p["id"]) + self._riders_total(con, p["id"]), 2)

    def list_policies(self, include_cancelled: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM policies WHERE customer_id=?" + ("" if include_cancelled else " AND status='active'")
        with self._conn() as con:
            return [self._with_riders(con, r) for r in con.execute(sql + " ORDER BY id", (self.customer_id,))]

    def get_policy(self, policy_id: str) -> dict[str, Any]:
        with self._conn() as con:
            return self._with_riders(con, self._owned_policy(con, policy_id))

    def update_coverage(self, policy_id: str, deductible: int | None = None, coverage_level: str | None = None) -> dict:
        if deductible is None and coverage_level is None:
            raise InvalidRequest("Provide a new deductible and/or coverage_level")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is {p['status']}")
            new_ded = deductible if deductible is not None else p["deductible"]
            new_level = coverage_level or p["coverage_level"]
            if new_ded not in DEDUCTIBLE_FACTORS:
                raise InvalidRequest(f"deductible must be one of {list(DEDUCTIBLE_FACTORS)}")
            if new_level not in COVERAGE_FACTORS:
                raise InvalidRequest(f"coverage_level must be one of {list(COVERAGE_FACTORS)}")
            if new_level == "basic" and con.execute(
                    "SELECT 1 FROM policy_riders WHERE policy_id=? AND rider_code='RENTAL_CAR' AND status='active'",
                    (policy_id,)).fetchone():
                raise InvalidRequest("Rental Car Cover requires standard or premium coverage; "
                                     "remove that rider before switching to basic")
            new_premium = self._premium(con, p, new_level, new_ded)
            con.execute("UPDATE policies SET deductible=?, coverage_level=?, monthly_premium=? WHERE id=?",
                        (new_ded, new_level, new_premium, policy_id))
        return {"policy_id": policy_id,
                "old": {"deductible": p["deductible"], "coverage_level": p["coverage_level"],
                        "monthly_premium": p["monthly_premium"]},
                "new": {"deductible": new_ded, "coverage_level": new_level, "monthly_premium": new_premium}}

    def cancel_policy(self, policy_id: str, reason: str, effective_date: str | None = None) -> dict[str, Any]:
        eff = date.fromisoformat(effective_date) if effective_date else self.today
        if eff < self.today:
            raise InvalidRequest("effective_date cannot be in the past")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is already {p['status']}")
            con.execute("UPDATE policies SET status='cancelled', cancelled_on=?, cancel_reason=? WHERE id=?",
                        (eff.isoformat(), reason, policy_id))
        return {"policy_id": policy_id, "status": "cancelled", "effective_date": eff.isoformat(), "reason": reason}

    # -- claims
    def file_claim(self, policy_id: str, incident_date: str, claim_type: str, description: str,
                   estimated_amount: float) -> dict[str, Any]:
        if claim_type not in CLAIM_TYPES:
            raise InvalidRequest(f"claim_type must be one of {CLAIM_TYPES}")
        if estimated_amount <= 0:
            raise InvalidRequest("estimated_amount must be positive")
        incident = date.fromisoformat(incident_date)
        if incident > self.today:
            raise InvalidRequest("incident_date cannot be in the future")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is {p['status']}; claims require an active policy")
            if not (date.fromisoformat(p["start_date"]) <= incident <= date.fromisoformat(p["end_date"])):
                raise InvalidRequest("incident_date is outside the policy period")
            cid = self._next_id(con, "claims", "CLM", 5001)
            con.execute("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?,?)",
                        (cid, policy_id, self.customer_id, incident.isoformat(), claim_type, description,
                         float(estimated_amount), "submitted", self.today.isoformat()))
        return {"claim_id": cid, "policy_id": policy_id, "status": "submitted", "claim_type": claim_type,
                "incident_date": incident.isoformat(), "estimated_amount": float(estimated_amount)}

    # -- drivers (auto policies)
    def _driver_factor(self, con: sqlite3.Connection, policy_id: str) -> float:
        dobs = [r["date_of_birth"] for r in con.execute(
            "SELECT date_of_birth FROM drivers WHERE policy_id=? AND status='active'", (policy_id,))]
        return 1 + sum(driver_surcharge(age_on(date.fromisoformat(d), self.today)) for d in dobs)

    def list_drivers(self, policy_id: str) -> dict[str, Any]:
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["product_code"] != "AUTO":
                raise InvalidRequest(f"Policy {policy_id} is not an auto policy; drivers only apply to auto policies")
            holder = con.execute("SELECT name, date_of_birth FROM customers WHERE id=?",
                                 (self.customer_id,)).fetchone()
            extra = [dict(r) for r in con.execute(
                "SELECT id AS driver_id, full_name, date_of_birth, relationship, added_on FROM drivers "
                "WHERE policy_id=? AND status='active' ORDER BY id", (policy_id,))]
        for d in extra:
            d["age"] = age_on(date.fromisoformat(d["date_of_birth"]), self.today)
        return {"policy_id": policy_id,
                "primary_driver": {"full_name": holder["name"], "relationship": "policyholder"},
                "additional_drivers": extra}

    def add_driver(self, policy_id: str, full_name: str, date_of_birth: str, relationship: str) -> dict[str, Any]:
        full_name = " ".join(full_name.split())
        if len(full_name) < 2:
            raise InvalidRequest("full_name is required")
        if relationship not in DRIVER_RELATIONSHIPS:
            raise InvalidRequest(f"relationship must be one of {DRIVER_RELATIONSHIPS}")
        dob = date.fromisoformat(date_of_birth)
        age = age_on(dob, self.today)
        if dob > self.today or age < MIN_DRIVER_AGE:
            raise InvalidRequest(f"Drivers must be at least {MIN_DRIVER_AGE} years old (got age {max(age, 0)})")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["product_code"] != "AUTO":
                raise InvalidRequest(f"Policy {policy_id} is not an auto policy; drivers can only be added to auto policies")
            if p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is {p['status']}")
            existing = con.execute("SELECT full_name, date_of_birth FROM drivers WHERE policy_id=? "
                                   "AND status='active'", (policy_id,)).fetchall()
            if len(existing) >= MAX_ADDITIONAL_DRIVERS:
                raise InvalidRequest(f"A policy can have at most {MAX_ADDITIONAL_DRIVERS} additional drivers")
            if any(r["full_name"].casefold() == full_name.casefold() and r["date_of_birth"] == dob.isoformat()
                   for r in existing):
                raise InvalidRequest(f"{full_name} is already a driver on {policy_id}")
            old_premium = p["monthly_premium"]
            driver_id = self._next_id(con, "drivers", "DRV", 3001)
            con.execute("INSERT INTO drivers (id, policy_id, customer_id, full_name, date_of_birth, "
                        "relationship, added_on) VALUES (?,?,?,?,?,?,?)",
                        (driver_id, policy_id, self.customer_id, full_name, dob.isoformat(), relationship,
                         self.today.isoformat()))
            new_premium = self._premium(con, p, p["coverage_level"], p["deductible"])
            con.execute("UPDATE policies SET monthly_premium=? WHERE id=?", (new_premium, policy_id))
        return {"driver_id": driver_id, "policy_id": policy_id, "full_name": full_name,
                "date_of_birth": dob.isoformat(), "age": age, "relationship": relationship,
                "old_monthly_premium": old_premium, "new_monthly_premium": new_premium}

    def remove_driver(self, policy_id: str, driver_id: str) -> dict[str, Any]:
        """Remove an additional driver (the policyholder stays the primary driver). Kept as history."""
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if p["product_code"] != "AUTO":
                raise InvalidRequest(f"Policy {policy_id} is not an auto policy; it has no drivers")
            if p["status"] != "active":
                raise InvalidRequest(f"Policy {policy_id} is {p['status']}")
            row = con.execute("SELECT * FROM drivers WHERE id=? AND policy_id=? AND status='active'",
                              (driver_id.upper(), policy_id)).fetchone()
            if row is None:
                raise NotFound(f"Driver {driver_id} is not an active driver on {policy_id}; "
                               "use list_drivers to see driver ids. The policyholder cannot be removed.")
            con.execute("UPDATE drivers SET status='removed', removed_on=? WHERE id=?",
                        (self.today.isoformat(), row["id"]))
            new_premium = self._premium(con, p, p["coverage_level"], p["deductible"])
            con.execute("UPDATE policies SET monthly_premium=? WHERE id=?", (new_premium, policy_id))
        return {"driver_id": row["id"], "policy_id": policy_id, "full_name": row["full_name"],
                "status": "removed", "old_monthly_premium": p["monthly_premium"],
                "new_monthly_premium": new_premium}

    # -- riders (optional add-on covers)
    def _rider_eligibility(self, p: sqlite3.Row, rider: dict[str, Any],
                           own_policies: dict[str, list[str]] | None = None) -> str | None:
        """None if the rider can be added to this policy, otherwise the reason it can not.

        own_policies (product -> the customer's active policy ids) lets the reason point to a policy
        of theirs where the rider does apply."""
        product = p["product_code"]
        if product not in rider["prices"]:
            names = ", ".join(sorted(rider["prices"]))
            reason = f"only available for {names} policies (this is a {product} policy)"
            fits = [pid for code in sorted(rider["prices"]) for pid in (own_policies or {}).get(code, [])]
            if fits:
                reason += f"; it can be added to your {', '.join(fits)} instead"
            return reason
        if p["status"] != "active":
            return f"policy is {p['status']}"
        if rider["code"] == "RENTAL_CAR" and p["coverage_level"] == "basic":
            return "requires standard or premium coverage (collision); this policy has basic coverage"
        if rider["code"] == "GAP":
            year = int(json.loads(p["insured_item"]).get("vehicle_year", 0) or 0)
            if self.today.year - year > 3:
                return f"only for vehicles 3 model years old or newer (this vehicle is a {year})"
        return None

    def list_riders(self, policy_id: str | None = None, product_code: str | None = None) -> dict[str, Any]:
        """The rider catalog. With a policy_id: eligibility, price and status for that policy."""
        if policy_id is None and product_code:
            # If the customer has exactly one active policy of this product, answer for that policy:
            # eligibility depends on the policy, and the reasons point to their other policies.
            with self._conn() as con:
                mine = [r["id"] for r in con.execute(
                    "SELECT id FROM policies WHERE customer_id=? AND product_code=? AND status='active'",
                    (self.customer_id, product_code.upper()))]
            if len(mine) == 1:
                policy_id = mine[0]
        if policy_id is None:
            riders = [r for r in RIDERS if product_code is None or product_code.upper() in r["prices"]]
            return {"riders": [dict(r) for r in riders]}
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            active = {r["rider_code"] for r in con.execute(
                "SELECT rider_code FROM policy_riders WHERE policy_id=? AND status='active'", (policy_id,))}
            own = self._own_policies_by_product(con)
        out = []
        for r in RIDERS:
            reason = self._rider_eligibility(p, r, own)
            if reason and p["product_code"] not in r["prices"]:
                # Not offered for this product at all: keep it short.
                out.append({"code": r["code"], "name": r["name"], "eligible": False,
                            "already_on_policy": False, "not_eligible_reason": reason})
                continue
            entry = {"code": r["code"], "name": r["name"], "description": r["description"],
                     "good_for": r["good_for"], "coverage_limit": r["coverage_limit"],
                     "already_on_policy": r["code"] in active, "eligible": reason is None}
            if p["product_code"] in r["prices"]:
                entry["monthly_cost"] = r["prices"][p["product_code"]]
            if r.get("waiting_period_days"):
                entry["waiting_period_days"] = r["waiting_period_days"]
            if reason:
                entry["not_eligible_reason"] = reason
            out.append(entry)
        # Riders the customer can actually add come first.
        out.sort(key=lambda e: (not e["eligible"], e["already_on_policy"], e["code"]))
        return {"policy_id": policy_id, "product_code": p["product_code"],
                "current_monthly_premium": p["monthly_premium"], "riders": out}

    def add_rider(self, policy_id: str, rider_code: str) -> dict[str, Any]:
        rider = RIDERS_BY_CODE.get(rider_code.upper())
        if rider is None:
            raise NotFound(f"Rider {rider_code} not found. Use list_riders to see available riders.")
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            reason = self._rider_eligibility(p, rider, self._own_policies_by_product(con))
            if reason:
                raise InvalidRequest(f"{rider['name']} cannot be added to {policy_id}: {reason}")
            if con.execute("SELECT 1 FROM policy_riders WHERE policy_id=? AND rider_code=? AND status='active'",
                           (policy_id, rider["code"])).fetchone():
                raise InvalidRequest(f"{rider['name']} is already on {policy_id}")
            starts = self.today + timedelta(days=rider.get("waiting_period_days", 0))
            rider_id = self._next_id(con, "policy_riders", "RDR", 4001)
            cost = rider["prices"][p["product_code"]]
            con.execute("INSERT INTO policy_riders VALUES (?,?,?,?,?,?,?,?,?)",
                        (rider_id, policy_id, self.customer_id, rider["code"], cost, "active",
                         self.today.isoformat(), starts.isoformat(), None))
            new_premium = self._premium(con, p, p["coverage_level"], p["deductible"])
            con.execute("UPDATE policies SET monthly_premium=? WHERE id=?", (new_premium, policy_id))
        return {"rider_id": rider_id, "policy_id": policy_id, "rider_code": rider["code"], "name": rider["name"],
                "monthly_cost": cost, "coverage_starts": starts.isoformat(),
                "old_monthly_premium": p["monthly_premium"], "new_monthly_premium": new_premium}

    def remove_rider(self, policy_id: str, rider_code: str) -> dict[str, Any]:
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            row = con.execute("SELECT id FROM policy_riders WHERE policy_id=? AND rider_code=? AND status='active'",
                              (policy_id, rider_code.upper())).fetchone()
            if row is None:
                raise NotFound(f"Rider {rider_code} is not on {policy_id}")
            con.execute("UPDATE policy_riders SET status='removed', removed_on=? WHERE id=?",
                        (self.today.isoformat(), row["id"]))
            new_premium = self._premium(con, p, p["coverage_level"], p["deductible"])
            con.execute("UPDATE policies SET monthly_premium=? WHERE id=?", (new_premium, policy_id))
        return {"policy_id": policy_id, "rider_code": rider_code.upper(), "status": "removed",
                "old_monthly_premium": p["monthly_premium"], "new_monthly_premium": new_premium}

    def list_claims(self, policy_id: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as con:
            if policy_id:
                self._owned_policy(con, policy_id)
                rows = con.execute("SELECT * FROM claims WHERE policy_id=? ORDER BY id", (policy_id,))
            else:
                rows = con.execute("SELECT * FROM claims WHERE customer_id=? ORDER BY id", (self.customer_id,))
            return [dict(r) for r in rows]

    # -- billing
    def get_billing(self, policy_id: str) -> dict[str, Any]:
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            pays = [dict(r) for r in con.execute(
                "SELECT id, amount, method, paid_on FROM payments WHERE policy_id=? ORDER BY id", (policy_id,))]
        return {"policy_id": policy_id, "monthly_premium": p["monthly_premium"], "balance_due": p["balance_due"],
                "next_due_date": p["next_due_date"], "payments": pays}

    def make_payment(self, policy_id: str, amount: float, payment_method: str) -> dict[str, Any]:
        if payment_method not in PAYMENT_METHODS:
            raise InvalidRequest(f"payment_method must be one of {PAYMENT_METHODS}")
        amount = round(float(amount), 2)
        with self._conn() as con:
            p = self._owned_policy(con, policy_id)
            if amount <= 0:
                raise InvalidRequest("amount must be positive")
            if amount > round(p["balance_due"], 2):
                raise InvalidRequest(f"amount exceeds balance due ({p['balance_due']:.2f})")
            pay_id = self._next_id(con, "payments", "PAY", 9001)
            con.execute("INSERT INTO payments VALUES (?,?,?,?,?,?)",
                        (pay_id, policy_id, self.customer_id, amount, payment_method, self.today.isoformat()))
            new_balance = round(p["balance_due"] - amount, 2)
            con.execute("UPDATE policies SET balance_due=? WHERE id=?", (new_balance, policy_id))
        return {"payment_id": pay_id, "policy_id": policy_id, "amount": amount, "method": payment_method,
                "remaining_balance": new_balance}


def main() -> None:
    from insurance_copilot.config import Settings

    parser = argparse.ArgumentParser(description="Create or reset the demo insurance database.")
    parser.add_argument("--db", type=Path, default=None)
    args = parser.parse_args()
    settings = Settings()
    path = args.db or settings.db_path
    init_db(path, settings.today, queue=True)
    print(f"Seeded demo database at {path}")


if __name__ == "__main__":
    main()
