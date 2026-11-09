"""The lab's system of record: SQLite (``data/lab.db``) plus uploaded files (``data/uploads/``).

A test run reads this (through the API, or the file read-only) to check what a script actually did, not only what
the page showed. ``reset()`` restores the seed.
"""

from __future__ import annotations

import json
import re
import shutil
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from automation_lab.domain import CARD_COLUMNS, seed_cards, seed_policies

SCHEMA = """
CREATE TABLE IF NOT EXISTS policies (policy_number TEXT PRIMARY KEY, holder TEXT, product TEXT, status TEXT,
    premium REAL, start_date TEXT, renewal_date TEXT, zip TEXT);
CREATE TABLE IF NOT EXISTS quotes (id TEXT PRIMARY KEY, created TEXT, request TEXT, result TEXT);
CREATE TABLE IF NOT EXISTS claims (id TEXT PRIMARY KEY, created TEXT, request TEXT);
CREATE TABLE IF NOT EXISTS uploads (id TEXT PRIMARY KEY, name TEXT, size INTEGER, content_type TEXT, area TEXT,
    created TEXT);
CREATE TABLE IF NOT EXISTS cards (id TEXT PRIMARY KEY, holder TEXT, type TEXT, amount REAL, col TEXT,
    position INTEGER);
CREATE TABLE IF NOT EXISTS activity (seq INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, source TEXT, kind TEXT,
    summary TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER);
"""
SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path, uploads_dir: Path, today: date) -> None:
        self.db_path, self.uploads_dir, self.today = db_path, uploads_dir, today
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)
            empty = db.execute("SELECT COUNT(*) FROM policies").fetchone()[0] == 0
        if empty:
            self.reset()

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            db = sqlite3.connect(self.db_path)
            db.row_factory = sqlite3.Row
            try:
                with db:
                    yield db
            finally:
                db.close()

    # ------------------------------------------------------------------ seed
    def reset(self) -> None:
        """Wipe everything and restore the seed data."""
        with self._db() as db:
            for table in ("policies", "quotes", "claims", "uploads", "cards", "activity", "counters"):
                db.execute(f"DELETE FROM {table}")  # fixed table names
            db.execute("DELETE FROM sqlite_sequence WHERE name = 'activity'")
            db.executemany(
                "INSERT INTO policies VALUES (:policy_number, :holder, :product, :status, :premium, :start_date, "
                ":renewal_date, :zip)",
                seed_policies(self.today),
            )
            db.executemany(
                "INSERT INTO cards VALUES (:id, :holder, :type, :amount, :column, :position)", seed_cards()
            )
        shutil.rmtree(self.uploads_dir, ignore_errors=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)

    def _next(self, db: sqlite3.Connection, name: str) -> int:
        row = db.execute("SELECT value FROM counters WHERE name = ?", (name,)).fetchone()
        value = (row[0] if row else 0) + 1
        db.execute("INSERT OR REPLACE INTO counters VALUES (?, ?)", (name, value))
        return value

    # ------------------------------------------------------------------ activity
    def log(self, source: str, kind: str, summary: str, detail: dict[str, Any] | None = None) -> int:
        with self._db() as db:
            cur = db.execute(
                "INSERT INTO activity (at, source, kind, summary, detail) VALUES (?, ?, ?, ?, ?)",
                (_now(), source, kind, summary, json.dumps(detail or {})),
            )
            return int(cur.lastrowid or 0)

    def activity(self, after: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM activity WHERE seq > ? ORDER BY seq LIMIT ?", (after, limit)
            ).fetchall()
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # ------------------------------------------------------------------ policies
    def policies(self) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM policies ORDER BY policy_number")]

    def policy_map(self) -> dict[str, dict[str, Any]]:
        return {p["policy_number"]: p for p in self.policies()}

    # ------------------------------------------------------------------ quotes and claims
    def add_quote(self, request: dict[str, Any], result: dict[str, Any]) -> str:
        with self._db() as db:
            quote_id = f"Q-{self.today.year}-{self._next(db, 'quote'):04d}"
            db.execute("INSERT INTO quotes VALUES (?, ?, ?, ?)",
                       (quote_id, _now(), json.dumps(request), json.dumps(result)))
        return quote_id

    def quotes(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM quotes ORDER BY id").fetchall()
        return [{"id": r["id"], "created": r["created"], "request": json.loads(r["request"]),
                 **json.loads(r["result"])} for r in rows]

    def next_claim_id(self) -> str:
        with self._db() as db:
            return f"CLM-{9000 + self._next(db, 'claim')}"

    def add_claim(self, claim_id: str, request: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute("INSERT INTO claims VALUES (?, ?, ?)", (claim_id, _now(), json.dumps(request)))

    def claims(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM claims ORDER BY id").fetchall()
        return [{"id": r["id"], "created": r["created"], **json.loads(r["request"])} for r in rows]

    # ------------------------------------------------------------------ uploads
    def add_upload(self, name: str, content: bytes, content_type: str, area: str, *, store: bool = True) -> dict:
        clean = SAFE_NAME.sub("_", Path(name).name).strip() or "file"
        with self._db() as db:
            upload_id = f"UPL-{self._next(db, 'upload'):04d}"
            row = {"id": upload_id, "name": clean, "size": len(content), "content_type": content_type,
                   "area": area, "created": _now()}
            if store:
                db.execute("INSERT INTO uploads VALUES (:id, :name, :size, :content_type, :area, :created)", row)
        if store:
            (self.uploads_dir / upload_id).write_bytes(content)
        return row

    def uploads(self) -> list[dict[str, Any]]:
        with self._db() as db:
            return [dict(r) for r in db.execute("SELECT * FROM uploads ORDER BY id")]

    def upload(self, upload_id: str) -> tuple[dict[str, Any], Path] | None:
        with self._db() as db:
            row = db.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        return (dict(row), self.uploads_dir / upload_id) if row else None

    def delete_upload(self, upload_id: str) -> bool:
        with self._db() as db:
            deleted = db.execute("DELETE FROM uploads WHERE id = ?", (upload_id,)).rowcount > 0
        (self.uploads_dir / upload_id).unlink(missing_ok=True)
        return deleted

    # ------------------------------------------------------------------ claims board
    def cards(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM cards ORDER BY col, position").fetchall()
        return [{"id": r["id"], "holder": r["holder"], "type": r["type"], "amount": r["amount"],
                 "column": r["col"], "position": r["position"]} for r in rows]

    def move_card(self, card_id: str, column: str, position: int) -> dict[str, Any] | None:
        """Move a card to ``column`` at ``position`` (clamped), renumbering both columns."""
        if column not in CARD_COLUMNS:
            raise ValueError(f"unknown column {column!r}")
        with self._db() as db:
            card = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
            if card is None:
                return None
            source = card["col"]
            for col in {source, column}:
                ids = [r["id"] for r in db.execute(
                    "SELECT id FROM cards WHERE col = ? AND id != ? ORDER BY position", (col, card_id))]
                if col == column:
                    ids.insert(max(0, min(position, len(ids))), card_id)
                for i, cid in enumerate(ids):
                    db.execute("UPDATE cards SET col = ?, position = ? WHERE id = ?", (col, i, cid))
            row = db.execute("SELECT * FROM cards WHERE id = ?", (card_id,)).fetchone()
        return {"id": row["id"], "column": row["col"], "position": row["position"], "from": source}

    def counts(self) -> dict[str, int]:
        with self._db() as db:
            return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # fixed table names
                    for t in ("quotes", "claims", "uploads", "activity")}
