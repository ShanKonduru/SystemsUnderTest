"""The claims system of record (SQLite), scoped to one principal with explicit permissions.

Every operation checks the principal's permissions and the claim's state; refusals raise AccessDenied (missing
permission) or BackendError (invalid request). Each call is recorded in ``audit_log``; business tables are
policies, claims, documents, claim_notes, letters and payments. Letters are also written as text files.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Any

# claim status -> statuses it may move to
TRANSITIONS: dict[str, set[str]] = {
    "open": {"under_review", "withdrawn"},
    "under_review": {"approved", "denied"},
    "approved": {"paid"},
    "denied": set(),
    "paid": set(),
    "withdrawn": set(),
}
LETTER_KINDS = ("acknowledgement", "approval", "denial", "info_request")

SCHEMA = """
CREATE TABLE IF NOT EXISTS principals (id TEXT PRIMARY KEY, name TEXT, role TEXT, permissions TEXT);
CREATE TABLE IF NOT EXISTS policies (id TEXT PRIMARY KEY, holder TEXT, product TEXT, status TEXT);
CREATE TABLE IF NOT EXISTS claims (
    id TEXT PRIMARY KEY, policy_id TEXT, claim_type TEXT, status TEXT, amount_claimed REAL,
    approved_amount REAL, opened_on TEXT, updated_on TEXT);
CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, claim_id TEXT, kind TEXT, received_on TEXT);
CREATE TABLE IF NOT EXISTS claim_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT, claim_id TEXT, author TEXT, text TEXT, created_on TEXT);
CREATE TABLE IF NOT EXISTS letters (id TEXT PRIMARY KEY, claim_id TEXT, kind TEXT, path TEXT, created_on TEXT);
CREATE TABLE IF NOT EXISTS payments (id TEXT PRIMARY KEY, claim_id TEXT, amount REAL, created_on TEXT);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT, principal TEXT, operation TEXT, outcome TEXT, created_on TEXT);
"""

SEED_PRINCIPALS = [
    ("adjuster-07", "Avery Adjuster", "claims_adjuster", "claims:read,claims:update,letters:write"),
    ("supervisor-02", "Sam Supervisor", "claims_supervisor", "claims:read,claims:update,letters:write,payments:write"),
    ("intake-bot", "Intake service", "intake", "claims:read"),
]
SEED_POLICIES = [
    ("POL-1001", "Shan", "AUTO", "active"),
    ("POL-1002", "Shan", "HOME", "active"),
    ("POL-2001", "Indu", "AUTO", "active"),
    ("POL-3001", "Sri", "HOME", "lapsed"),
]
SEED_CLAIMS = [
    ("CLM-5001", "POL-1001", "collision", "approved", 2400.0, 2400.0, "2026-10-01"),
    ("CLM-5003", "POL-1002", "water_damage", "open", 6200.0, None, "2026-10-28"),
    ("CLM-5004", "POL-2001", "theft", "under_review", 1800.0, None, "2026-10-20"),
    ("CLM-5006", "POL-3001", "fire", "open", 9000.0, None, "2026-11-01"),
]
SEED_DOCUMENTS = [
    ("DOC-77", "CLM-5003", "photos", "2026-11-02"),
    ("DOC-78", "CLM-5003", "repair_estimate", "2026-11-03"),
    ("DOC-80", "CLM-5004", "police_report", "2026-10-22"),
    ("DOC-81", "CLM-5006", "fire_report", "2026-11-02"),
]


class BackendError(Exception):
    """An invalid request (unknown claim, bad transition, bad amount)."""


class AccessDenied(BackendError):
    """The principal lacks a permission the operation needs."""


def init_db(db_path: Path | str, letters_dir: Path | str, today: date) -> None:
    """Create (or recreate) the seeded database and an empty letters folder."""
    db_path, letters_dir = Path(db_path), Path(letters_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    letters_dir.mkdir(parents=True, exist_ok=True)
    for old in letters_dir.glob("*.txt"):
        old.unlink()
    con = sqlite3.connect(db_path)
    try:
        con.executescript(SCHEMA)
        con.executemany("INSERT INTO principals VALUES (?,?,?,?)", SEED_PRINCIPALS)
        con.executemany("INSERT INTO policies VALUES (?,?,?,?)", SEED_POLICIES)
        con.executemany("INSERT INTO claims VALUES (?,?,?,?,?,?,?,?)", [(*c, c[-1]) for c in SEED_CLAIMS])
        con.executemany("INSERT INTO documents VALUES (?,?,?,?)", SEED_DOCUMENTS)
        con.commit()
    finally:
        con.close()
    _ = today  # seed dates are fixed; today only matters for new rows


class ClaimsBackend:
    def __init__(self, db_path: Path | str, letters_dir: Path | str, principal: str, today: date) -> None:
        self.db_path, self.letters_dir, self.today = Path(db_path), Path(letters_dir), today
        with self._con() as con:
            row = con.execute("SELECT id, role, permissions FROM principals WHERE id=?", (principal,)).fetchone()
        if row is None:
            raise AccessDenied(f"unknown principal {principal!r}")
        self.principal, self.role = row["id"], row["role"]
        self.permissions = frozenset(p for p in row["permissions"].split(",") if p)

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # ------------------------------------------------------------------ helpers
    def _need(self, permission: str) -> None:
        if permission not in self.permissions:
            raise AccessDenied(f"ACCESS_DENIED: {self.principal} lacks {permission}")

    def audit(self, operation: str, outcome: str) -> None:
        with self._con() as con:
            con.execute(
                "INSERT INTO audit_log (principal, operation, outcome, created_on) VALUES (?,?,?,?)",
                (self.principal, operation, outcome, self.today.isoformat()),
            )

    def _claim(self, con: sqlite3.Connection, claim_id: str) -> sqlite3.Row:
        row = con.execute(
            "SELECT c.*, p.status AS policy_status, p.holder FROM claims c "
            "JOIN policies p ON p.id = c.policy_id WHERE c.id=?",
            (claim_id,),
        ).fetchone()
        if row is None:
            raise BackendError(f"claim {claim_id} not found")
        return row

    # ------------------------------------------------------------------ reads
    def get_claim(self, claim_id: str) -> dict[str, Any]:
        self._need("claims:read")
        with self._con() as con:
            row = self._claim(con, claim_id)
            docs = con.execute("SELECT COUNT(*) FROM documents WHERE claim_id=?", (claim_id,)).fetchone()[0]
        return {
            "claim_id": row["id"],
            "policy_id": row["policy_id"],
            "policy_status": row["policy_status"],
            "holder": row["holder"],
            "claim_type": row["claim_type"],
            "status": row["status"],
            "amount_claimed": row["amount_claimed"],
            "approved_amount": row["approved_amount"],
            "documents": docs,
            "allowed_next_status": sorted(TRANSITIONS[row["status"]]),
        }

    def list_documents(self, claim_id: str) -> dict[str, Any]:
        self._need("claims:read")
        with self._con() as con:
            self._claim(con, claim_id)
            rows = con.execute(
                "SELECT id, kind, received_on FROM documents WHERE claim_id=? ORDER BY id", (claim_id,)
            ).fetchall()
        return {"claim_id": claim_id, "documents": [dict(r) for r in rows]}

    # ------------------------------------------------------------------ writes
    def update_claim(
        self, claim_id: str, status: str, approved_amount: float | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        self._need("claims:update")
        with self._con() as con:
            row = self._claim(con, claim_id)
            if status not in TRANSITIONS.get(row["status"], set()):
                raise BackendError(f"claim {claim_id} is {row['status']}; it cannot move to {status}")
            if status == "approved":
                if row["policy_status"] != "active":
                    raise BackendError(
                        f"policy {row['policy_id']} is {row['policy_status']}; the claim cannot be approved"
                    )
                if approved_amount is None or not 0 < approved_amount <= row["amount_claimed"]:
                    raise BackendError(f"approved_amount must be between 0 and {row['amount_claimed']}")
            elif approved_amount is not None:
                raise BackendError("approved_amount is only allowed when approving")
            con.execute(
                "UPDATE claims SET status=?, approved_amount=COALESCE(?, approved_amount), updated_on=? WHERE id=?",
                (status, approved_amount, self.today.isoformat(), claim_id),
            )
            if reason:
                con.execute(
                    "INSERT INTO claim_notes (claim_id, author, text, created_on) VALUES (?,?,?,?)",
                    (claim_id, self.principal, f"{status}: {reason}", self.today.isoformat()),
                )
        return {"claim_id": claim_id, "status": status, "approved_amount": approved_amount or row["approved_amount"]}

    def add_note(self, claim_id: str, text: str) -> dict[str, Any]:
        self._need("claims:update")
        if not text.strip():
            raise BackendError("note text is required")
        with self._con() as con:
            self._claim(con, claim_id)
            cur = con.execute(
                "INSERT INTO claim_notes (claim_id, author, text, created_on) VALUES (?,?,?,?)",
                (claim_id, self.principal, text.strip(), self.today.isoformat()),
            )
        return {"claim_id": claim_id, "note_id": cur.lastrowid}

    def write_letter(self, claim_id: str, kind: str, body: str) -> dict[str, Any]:
        self._need("letters:write")
        if kind not in LETTER_KINDS:
            raise BackendError(f"kind must be one of {', '.join(LETTER_KINDS)}")
        if not body.strip():
            raise BackendError("letter body is required")
        with self._con() as con:
            row = self._claim(con, claim_id)
            letter_id = f"LTR-{claim_id}-{kind}"
            path = self.letters_dir / f"{claim_id}-{kind}.txt"
            self.letters_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"Harbor Mutual - {kind.replace('_', ' ')}\nClaim {claim_id} ({row['holder']})\n"
                f"Date: {self.today.isoformat()}\n\n{body.strip()}\n",
                encoding="utf-8",
            )
            con.execute(
                "INSERT OR REPLACE INTO letters VALUES (?,?,?,?,?)",
                (letter_id, claim_id, kind, path.name, self.today.isoformat()),
            )
        return {"letter_id": letter_id, "file": path.name}

    def make_payment(self, claim_id: str, amount: float) -> dict[str, Any]:
        self._need("payments:write")
        with self._con() as con:
            row = self._claim(con, claim_id)
            if row["status"] != "approved":
                raise BackendError(f"claim {claim_id} is {row['status']}; only approved claims can be paid")
            if not 0 < amount <= (row["approved_amount"] or 0):
                raise BackendError(f"amount must be between 0 and the approved {row['approved_amount']}")
            pay_id = f"PAY-{claim_id}"
            con.execute("INSERT INTO payments VALUES (?,?,?,?)", (pay_id, claim_id, amount, self.today.isoformat()))
            con.execute("UPDATE claims SET status='paid', updated_on=? WHERE id=?", (self.today.isoformat(), claim_id))
        return {"payment_id": pay_id, "claim_id": claim_id, "amount": amount, "status": "paid"}
