"""User accounts and login sessions.

Separate from `db` so that module stays about jobs and clips. Both open the
same SQLite file; each owns its own tables.
"""

from __future__ import annotations

import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

# Sessions are bearer tokens. Thirty days balances not re-authenticating a
# preacher every week against how long a stolen cookie stays useful.
SESSION_TTL = 30 * 24 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    approved_at   REAL,
    approved_by   INTEGER REFERENCES users(id),
    disabled_at   REAL,
    created_at    REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
"""


class Accounts:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._shared = sqlite3.connect(":memory:") if str(path) == ":memory:" else None
        self.migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = self._shared or sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            if self._shared is None:
                conn.close()

    def migrate(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)

    # --- users ---------------------------------------------------------------

    def create_user(
        self,
        email: str,
        password_hash: str,
        is_admin: bool = False,
        approved: bool = False,
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            try:
                cursor = conn.execute(
                    "INSERT INTO users (email, password_hash, is_admin, approved_at,"
                    " created_at) VALUES (?, ?, ?, ?, ?)",
                    (email.strip(), password_hash, int(is_admin), now if approved else None, now),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"an account for {email} already exists") from exc
            return int(cursor.lastrowid)

    def get_user(self, user_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE email = ?", (email.strip(),)
            ).fetchone()
        return dict(row) if row else None

    def list_users(self) -> list[dict[str, Any]]:
        """Pending accounts first — they are the ones needing a decision."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users"
                " ORDER BY (approved_at IS NOT NULL), created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_approved(self, user_id: int, approved_by: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET approved_at = ?, approved_by = ? WHERE id = ?",
                (time.time(), approved_by, user_id),
            )

    def clear_approved(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET approved_at = NULL, approved_by = NULL WHERE id = ?",
                (user_id,),
            )

    def set_disabled(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET disabled_at = ? WHERE id = ?", (time.time(), user_id)
            )

    def delete_user(self, user_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE users SET approved_by = NULL WHERE approved_by = ?", (user_id,)
            )
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    # --- sessions ------------------------------------------------------------

    def create_session(self, user_id: int, ttl_seconds: float = SESSION_TTL) -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO sessions (id, user_id, created_at, expires_at, last_seen_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (token, user_id, now, now + ttl_seconds, now),
            )
        return token

    def resolve_session(self, session_id: str) -> dict[str, Any] | None:
        """The user behind a live session, or None.

        Returns the user rather than the session because that is what every
        caller wants, and it keeps the disabled check in one place.
        """
        if not session_id:
            return None

        now = time.time()
        with self.connect() as conn:
            row = conn.execute(
                "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
                " WHERE s.id = ? AND s.expires_at > ? AND u.disabled_at IS NULL",
                (session_id, now),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE id = ?", (now, session_id)
            )
        return dict(row)

    def delete_session(self, session_id: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def delete_user_sessions(self, user_id: int, keep: str | None = None) -> int:
        """Log a user out everywhere. Returns how many sessions were killed."""
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND id IS NOT ?", (user_id, keep)
            )
            return cursor.rowcount
