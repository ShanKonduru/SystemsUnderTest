"""The member-services system of record (SQLite): billing, payments, claims and service cases.

Operations run for one verified identity (member + permissions from the signed token + the agent chain). Every
operation checks the token's permission, that the member owns the record, and that the token never claims more
than the member's registered permissions. Each call is written to ``audit_log`` with the acting agent and the
full chain, which is the independent record of who did what for whom.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, name TEXT, permissions TEXT);
CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, member_id TEXT, product TEXT, deductible REAL);
CREATE TABLE IF NOT EXISTS bills (policy_id TEXT PRIMARY KEY, balance_due REAL, due_date TEXT);
CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, policy_id TEXT, amount REAL, paid_on TEXT);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY, member_id TEXT, policy_id TEXT, status TEXT, amount_claimed REAL, approved_amount REAL,
    paid_amount REAL, note TEXT);
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT, subject TEXT, details TEXT, opened_by TEXT, opened_on TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, member_id TEXT, agent TEXT, chain TEXT, operation TEXT, outcome TEXT,
    created_on TEXT);
"""
SEED_MEMBERS = [
    ("M-1001", "Shan", "billing:read,claims:read,cases:write"),
    ("M-2201", "Indu", "billing:read,claims:read,cases:write"),
    ("M-3001", "Sri", "billing:read"),
]
SEED_POLICIES = [
    ("POL-1002", "M-1001", "HOME", 1000.0),
    ("POL-2001", "M-2201", "AUTO", 500.0),
    ("POL-3001", "M-3001", "HOME", 1000.0),
]
SEED_BILLS = [("POL-1002", 193.60, "2026-11-15"), ("POL-2001", 88.00, "2026-11-20"), ("POL-3001", 0.0, "2026-12-01")]
SEED_PAYMENTS = [("PAY-8801", "POL-1002", 95.00, "2026-10-15"), ("PAY-8802", "POL-2001", 88.00, "2026-10-20")]
SEED_CLAIMS = [
    (
        "CLM-5003",
        "M-1001",
        "POL-1002",
        "paid",
        6200.0,
        5000.0,
        4000.0,
        "Approved 5000.00; the 1000.00 policy deductible was subtracted, so 4000.00 was paid.",
    ),
    (
        "CLM-7001",
        "M-2201",
        "POL-2001",
        "denied",
        1800.0,
        None,
        0.0,
        "Denied: theft from an unlocked vehicle is excluded.",
    ),
]


class BackendError(Exception):
    """An invalid request (unknown record, bad argument)."""


class AccessDenied(BackendError):
    """Missing permission, someone else's record, or a token that claims more than the member holds."""


def init_db(db_path: Path | str) -> None:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO members VALUES (?,?,?)", SEED_MEMBERS)
        con.executemany("INSERT INTO policies VALUES (?,?,?,?)", SEED_POLICIES)
        con.executemany("INSERT INTO bills VALUES (?,?,?)", SEED_BILLS)
        con.executemany("INSERT INTO payments VALUES (?,?,?,?)", SEED_PAYMENTS)
        con.executemany("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)", SEED_CLAIMS)
        con.commit()
    finally:
        con.close()


@dataclass(frozen=True)
class Identity:
    member_id: str
    permissions: frozenset[str]
    chain: tuple[str, ...]

    @property
    def agent(self) -> str:
        return self.chain[-1] if self.chain else "?"


def member_permissions(db_path: Path | str, member_id: str) -> list[str] | None:
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT permissions FROM members WHERE id=?", (member_id,)).fetchone()
    finally:
        con.close()
    return None if row is None else [p for p in row[0].split(",") if p]


class DeskBackend:
    def __init__(self, db_path: Path | str, identity: Identity, today: date) -> None:
        self.db_path, self.identity, self.today = Path(db_path), identity, today
        registered = member_permissions(db_path, identity.member_id)
        if registered is None:
            raise AccessDenied(f"ACCESS_DENIED: unknown member {identity.member_id}")
        widened = sorted(identity.permissions - set(registered))
        if widened:
            raise AccessDenied(f"ACCESS_DENIED: identity claims permissions the member does not hold: {widened}")

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    def audit(self, operation: str, outcome: str) -> None:
        with self._con() as con:
            con.execute(
                "INSERT INTO audit_log (member_id, agent, chain, operation, outcome, created_on) VALUES (?,?,?,?,?,?)",
                (
                    self.identity.member_id,
                    self.identity.agent,
                    json.dumps(list(self.identity.chain)),
                    operation,
                    outcome,
                    self.today.isoformat(),
                ),
            )

    def _need(self, permission: str) -> None:
        if permission not in self.identity.permissions:
            raise AccessDenied(
                f"ACCESS_DENIED: {permission} not granted to {self.identity.agent} for {self.identity.member_id}"
            )

    def _own_policy(self, con: sqlite3.Connection, policy_id: str) -> sqlite3.Row:
        row = con.execute("SELECT * FROM policies WHERE id=?", (policy_id,)).fetchone()
        if row is None:
            raise BackendError(f"policy {policy_id} not found")
        if row["member_id"] != self.identity.member_id:
            raise AccessDenied(f"ACCESS_DENIED: policy {policy_id} belongs to another member")
        return row

    # ------------------------------------------------------------------ operations
    def get_billing(self, policy_id: str) -> dict[str, Any]:
        self._need("billing:read")
        with self._con() as con:
            pol = self._own_policy(con, policy_id)
            bill = con.execute("SELECT balance_due, due_date FROM bills WHERE policy_id=?", (policy_id,)).fetchone()
        return {
            "policy_id": policy_id,
            "product": pol["product"],
            "deductible": pol["deductible"],
            "balance_due": bill["balance_due"],
            "due_date": bill["due_date"],
        }

    def list_payments(self, policy_id: str) -> dict[str, Any]:
        self._need("billing:read")
        with self._con() as con:
            self._own_policy(con, policy_id)
            rows = con.execute(
                "SELECT id, amount, paid_on FROM payments WHERE policy_id=? ORDER BY paid_on", (policy_id,)
            ).fetchall()
        return {"policy_id": policy_id, "payments": [dict(r) for r in rows]}

    def get_claim(self, claim_id: str) -> dict[str, Any]:
        self._need("claims:read")
        with self._con() as con:
            row = con.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if row is None:
            raise BackendError(f"claim {claim_id} not found")
        if row["member_id"] != self.identity.member_id:
            raise AccessDenied(f"ACCESS_DENIED: claim {claim_id} belongs to another member")
        return {
            k: row[k] for k in ("id", "policy_id", "status", "amount_claimed", "approved_amount", "paid_amount", "note")
        }

    def list_claims(self) -> dict[str, Any]:
        self._need("claims:read")
        with self._con() as con:
            rows = con.execute(
                "SELECT id, policy_id, status FROM claims WHERE member_id=? ORDER BY id", (self.identity.member_id,)
            ).fetchall()
        return {"claims": [dict(r) for r in rows]}

    def open_case(self, subject: str, details: str) -> dict[str, Any]:
        self._need("cases:write")
        if not subject.strip():
            raise BackendError("subject is required")
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO cases (member_id, subject, details, opened_by, opened_on) VALUES (?,?,?,?,?)",
                (
                    self.identity.member_id,
                    subject.strip(),
                    details.strip(),
                    self.identity.agent,
                    self.today.isoformat(),
                ),
            )
        return {"case_id": f"CASE-{cur.lastrowid}", "subject": subject.strip()}

    def make_payment(self, policy_id: str, amount: float) -> dict[str, Any]:
        """Payments are out of scope for the member desk: no member grants billing:write to it, and an identity
        claiming it is rejected when the backend is built. Calling this is always refused."""
        raise AccessDenied(
            f"ACCESS_DENIED: billing:write not granted to {self.identity.agent} for {self.identity.member_id}"
        )
