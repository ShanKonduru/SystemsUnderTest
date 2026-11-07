"""App-side persistence: users, login sessions, conversations, chat messages, engine state, traces.

Lives in the same SQLite file as the insurance backend (Docker volume /app/data), in its own tables.
Passwords are stored as salted PBKDF2 hashes; session tokens are stored as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

APP_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY, display_name TEXT NOT NULL, customer_id TEXT NOT NULL,
    password_salt TEXT NOT NULL, password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY, username TEXT NOT NULL, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    engine_state TEXT  -- JSON: LLM history + transcript, used to continue the chat
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL,
    role TEXT NOT NULL, text TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS turn_traces (
    conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, trace TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (conversation_id, turn)
);
CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(username, updated_at);
CREATE INDEX IF NOT EXISTS idx_msg_conv ON chat_messages(conversation_id, id);
"""

DEMO_USERS = [("shan", "Shan", "CUST-001"), ("indu", "Indu", "CUST-002"), ("sri", "Sri", "CUST-003"),
              # Harbor Mutual representatives (backend table representatives): service rep, licensed agent, supervisor
              ("rivera", "J. Rivera", "REP-201"), ("chen", "A. Chen", "REP-202"), ("patel", "M. Patel", "REP-203")]
DEFAULT_DEMO_PASSWORD = "P@$$w0rd123"
SESSION_HOURS = 12
PBKDF2_ITERATIONS = 200_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return salt, digest.hex()


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class AppStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        try:
            yield con
            con.commit()
        finally:
            con.close()

    # ------------------------------------------------------------------ setup
    def ensure_schema(self, demo_password: str | None = None) -> None:
        password = demo_password or os.environ.get("DEMO_USER_PASSWORD") or DEFAULT_DEMO_PASSWORD
        with self._conn() as con:
            con.executescript(APP_SCHEMA)
            for username, display, customer in DEMO_USERS:
                if not con.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                    salt, digest = hash_password(password)
                    con.execute("INSERT INTO users VALUES (?,?,?,?,?)", (username, display, customer, salt, digest))

    def has_app_schema(self) -> bool:
        if not self.db_path.exists():
            return False
        with self._conn() as con:
            return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'").fetchone() is not None

    # ------------------------------------------------------------------ auth
    def authenticate(self, username: str, password: str) -> dict[str, Any] | None:
        with self._conn() as con:
            row = con.execute("SELECT * FROM users WHERE username=?", (username.strip().lower(),)).fetchone()
        if row is None:
            hash_password(password)  # same work either way, so timing doesn't reveal valid usernames
            return None
        _, digest = hash_password(password, row["password_salt"])
        if not hmac.compare_digest(digest, row["password_hash"]):
            return None
        return {"username": row["username"], "display_name": row["display_name"], "customer_id": row["customer_id"]}

    def create_guest(self) -> dict[str, Any]:
        """A guest (prospective customer): a user with no password and a GUEST- id, so the tool server gives
        them only the prospect tools. Their chats are kept like anyone else's for the session's lifetime."""
        suffix = secrets.token_hex(4).upper()
        user = {"username": f"guest-{suffix.lower()}", "display_name": "Guest", "customer_id": f"GUEST-{suffix}"}
        salt, digest = hash_password(secrets.token_urlsafe(24))  # unknowable password: guests can't sign in
        with self._conn() as con:
            con.execute("INSERT INTO users VALUES (?,?,?,?,?)",
                        (user["username"], user["display_name"], user["customer_id"], salt, digest))
        return user

    def create_session(self, username: str) -> str:
        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)).isoformat(timespec="seconds")
        with self._conn() as con:
            con.execute("DELETE FROM auth_sessions WHERE expires_at < ?", (_now(),))
            con.execute("INSERT INTO auth_sessions VALUES (?,?,?)", (_token_hash(token), username, expires))
        return token

    def user_for_token(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._conn() as con:
            row = con.execute(
                "SELECT u.username, u.display_name, u.customer_id FROM auth_sessions s "
                "JOIN users u ON u.username = s.username WHERE s.token_hash=? AND s.expires_at > ?",
                (_token_hash(token), _now()),
            ).fetchone()
        return dict(row) if row else None

    def delete_session(self, token: str | None) -> None:
        if token:
            with self._conn() as con:
                con.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_token_hash(token),))

    # ------------------------------------------------------------------ conversations
    def create_conversation(self, username: str, title: str = "New chat") -> dict[str, Any]:
        conv = {"id": uuid.uuid4().hex, "title": title, "created_at": _now(), "updated_at": _now()}
        with self._conn() as con:
            # Tidy up this user's chats that never got a message.
            con.execute("DELETE FROM conversations WHERE username=? AND NOT EXISTS "
                        "(SELECT 1 FROM chat_messages m WHERE m.conversation_id = conversations.id)", (username,))
            con.execute("INSERT INTO conversations (id, username, title, created_at, updated_at) VALUES (?,?,?,?,?)",
                        (conv["id"], username, title, conv["created_at"], conv["updated_at"]))
        return conv

    def list_conversations(self, username: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            # Only chats with at least one message, most recently active first.
            return [dict(r) for r in con.execute(
                "SELECT c.id, c.title, c.created_at, c.updated_at FROM conversations c WHERE c.username=? "
                "AND EXISTS (SELECT 1 FROM chat_messages m WHERE m.conversation_id = c.id) "
                "ORDER BY c.updated_at DESC, c.created_at DESC", (username,))]

    def get_conversation(self, username: str, conversation_id: str) -> dict[str, Any] | None:
        """Returns the conversation only if it belongs to this user."""
        with self._conn() as con:
            row = con.execute("SELECT * FROM conversations WHERE id=? AND username=?",
                              (conversation_id, username)).fetchone()
        if row is None:
            return None
        conv = dict(row)
        conv["engine_state"] = json.loads(conv["engine_state"]) if conv["engine_state"] else None
        return conv

    def delete_conversation(self, username: str, conversation_id: str) -> bool:
        with self._conn() as con:
            cur = con.execute("DELETE FROM conversations WHERE id=? AND username=?", (conversation_id, username))
            if cur.rowcount:
                con.execute("DELETE FROM chat_messages WHERE conversation_id=?", (conversation_id,))
                con.execute("DELETE FROM turn_traces WHERE conversation_id=?", (conversation_id,))
            return bool(cur.rowcount)

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            return [dict(r) for r in con.execute(
                "SELECT role, text, created_at FROM chat_messages WHERE conversation_id=? ORDER BY id",
                (conversation_id,))]

    def add_message(self, conversation_id: str, role: str, text: str) -> None:
        with self._conn() as con:
            con.execute("INSERT INTO chat_messages (conversation_id, role, text, created_at) VALUES (?,?,?,?)",
                        (conversation_id, role, text, _now()))
            # The first customer message becomes the chat title.
            con.execute("UPDATE conversations SET updated_at=?, title=CASE WHEN title='New chat' AND ?='user' "
                        "THEN ? ELSE title END WHERE id=?",
                        (_now(), role, (text[:60] + ("…" if len(text) > 60 else "")), conversation_id))

    def save_turn(self, conversation_id: str, engine_state: dict[str, Any], trace: dict[str, Any]) -> None:
        with self._conn() as con:
            con.execute("UPDATE conversations SET engine_state=?, updated_at=? WHERE id=?",
                        (json.dumps(engine_state, default=str), _now(), conversation_id))
            con.execute("INSERT OR REPLACE INTO turn_traces VALUES (?,?,?,?)",
                        (conversation_id, trace["turn"], json.dumps(trace, default=str), _now()))

    def traces(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._conn() as con:
            return [json.loads(r["trace"]) for r in con.execute(
                "SELECT trace FROM turn_traces WHERE conversation_id=? ORDER BY turn", (conversation_id,))]
