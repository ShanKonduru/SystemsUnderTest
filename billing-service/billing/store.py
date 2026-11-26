"""The billing system of record: SQLite, with seed data that `reset` rebuilds."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from .config import Settings
from .errors import NotFound
from .installments import Installment, build_schedule
from .money import fmt
from .premiums import quote_premium
from .rates import RateTable

SCHEMA = """
CREATE TABLE customers (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, state TEXT NOT NULL, paperless INTEGER NOT NULL
);
CREATE TABLE policies (
    id TEXT PRIMARY KEY, customer_id TEXT NOT NULL REFERENCES customers(id), product TEXT NOT NULL,
    state TEXT NOT NULL, coverage TEXT NOT NULL, driver_age INTEGER, plan TEXT NOT NULL,
    effective_date TEXT NOT NULL, annual_premium TEXT NOT NULL, cancelled_on TEXT, refund TEXT
);
CREATE TABLE installments (
    policy_id TEXT NOT NULL REFERENCES policies(id), number INTEGER NOT NULL, due_date TEXT NOT NULL,
    amount TEXT NOT NULL, fee TEXT NOT NULL, paid_on TEXT, paid_amount TEXT, late_fee TEXT,
    PRIMARY KEY (policy_id, number)
);
"""

SEED_CUSTOMERS = [
    # id, name, state, paperless
    ("C-1001", "Asha Rao", "NJ", True),
    ("C-1002", "Ben Ortiz", "NY", False),
    ("C-1003", "Chen Li", "PA", False),
    ("C-1004", "Dana Park", "NJ", True),
    ("C-1005", "Eli Novak", "CT", False),
]

SEED_POLICIES = [
    # id, customer, product, coverage, driver age, plan, effective date, installments paid
    ("P-5001", "C-1001", "auto", "standard", 34, "monthly", "2026-01-31", 10),
    ("P-5002", "C-1001", "home", "plus", None, "quarterly", "2026-04-01", 3),
    ("P-5003", "C-1002", "auto", "basic", 22, "annual", "2026-06-15", 1),
    ("P-5004", "C-1003", "auto", "standard", 41, "monthly", "2026-08-20", 2),
    ("P-5005", "C-1004", "auto", "plus", 67, "semiannual", "2026-05-01", 1),
    ("P-5006", "C-1004", "home", "standard", None, "annual", "2026-05-01", 1),
    ("P-5007", "C-1004", "renters", "basic", None, "annual", "2026-09-10", 1),
    ("P-5008", "C-1005", "renters", "standard", None, "semiannual", "2026-10-28", 1),
]


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as conn:
            conn.row_factory = sqlite3.Row
            with conn:
                yield conn

    def is_seeded(self) -> bool:
        with self._db() as conn:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'policies'").fetchone() is not None

    def reset(self, settings: Settings, rates: RateTable) -> None:
        """Drop everything and load the seed: premiums priced from the rate table, installments paid as listed."""
        with self._db() as conn:
            conn.executescript(
                "DROP TABLE IF EXISTS installments; DROP TABLE IF EXISTS policies; DROP TABLE IF EXISTS customers;"
                + SCHEMA
            )
            conn.executemany("INSERT INTO customers VALUES (?, ?, ?, ?)", SEED_CUSTOMERS)
            customers = {cid: (state, paperless) for cid, _, state, paperless in SEED_CUSTOMERS}
            counts = {cid: sum(1 for p in SEED_POLICIES if p[1] == cid) for cid in customers}
            for pid, cid, product, coverage, age, plan, effective, paid in SEED_POLICIES:
                state, paperless = customers[cid]
                premium = quote_premium(
                    rates, state=state, product=product, coverage=coverage, driver_age=age,
                    policy_count=counts[cid], paperless=paperless,
                ).premium
                conn.execute(
                    "INSERT INTO policies VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                    (pid, cid, product, state, coverage, age, plan, effective, fmt(premium)),
                )
                schedule = build_schedule(premium, plan, date.fromisoformat(effective), settings.installment_fees)
                for inst in schedule:
                    is_paid = inst.number <= paid
                    conn.execute(
                        "INSERT INTO installments VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (pid, inst.number, inst.due_date.isoformat(), fmt(inst.amount), fmt(inst.fee),
                         inst.due_date.isoformat() if is_paid else None, fmt(inst.total) if is_paid else None,
                         "0.00" if is_paid else None),
                    )

    def counts(self) -> dict[str, int]:
        with self._db() as conn:
            return {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in ("customers", "policies", "installments")
            }

    def customer(self, customer_id: str) -> dict[str, Any]:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
            if row is None:
                raise NotFound(f"no customer {customer_id}")
            policies = conn.execute(
                "SELECT id FROM policies WHERE customer_id = ? ORDER BY id", (customer_id,)
            ).fetchall()
        return {**dict(row), "paperless": bool(row["paperless"]), "policies": [p["id"] for p in policies]}

    def policy(self, policy_id: str) -> dict[str, Any]:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM policies WHERE id = ?", (policy_id,)).fetchone()
        if row is None:
            raise NotFound(f"no policy {policy_id}")
        return dict(row)

    def active_policy_count(self, customer_id: str) -> int:
        with self._db() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM policies WHERE customer_id = ? AND cancelled_on IS NULL", (customer_id,)
            ).fetchone()[0]

    def installments(self, policy_id: str) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM installments WHERE policy_id = ? ORDER BY number", (policy_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def as_installment(row: dict[str, Any]) -> Installment:
        return Installment(row["number"], date.fromisoformat(row["due_date"]), Decimal(row["amount"]),
                           Decimal(row["fee"]))

    def record_payment(self, policy_id: str, number: int, paid_on: date, amount: Decimal, late_fee: Decimal) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE installments SET paid_on = ?, paid_amount = ?, late_fee = ? WHERE policy_id = ? AND number = ?",
                (paid_on.isoformat(), fmt(amount), fmt(late_fee), policy_id, number),
            )

    def cancel(self, policy_id: str, cancel_date: date, refund: Decimal) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE policies SET cancelled_on = ?, refund = ? WHERE id = ?",
                (cancel_date.isoformat(), fmt(refund), policy_id),
            )
