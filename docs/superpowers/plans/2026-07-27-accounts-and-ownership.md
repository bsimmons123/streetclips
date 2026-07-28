# Accounts and Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give streetclip user accounts, per-user workspace ownership, and an admin approval gate so an unapproved account cannot spend the machine's disk, CPU, or API budget.

**Architecture:** Accounts live in two new modules rather than being bolted onto `db.py` (356 lines) and `api.py` (343 lines): `accounts.py` owns the users and sessions tables, `auth.py` owns hashing, cookies, and the FastAPI dependencies. Authorization is one rule — you may act on a workspace you own — enforced by a dependency on every route, with a route-table test that fails when a new endpoint forgets it.

**Tech Stack:** Python 3.12, FastAPI, SQLite, argon2-cffi, React 18, Vite.

## Global Constraints

- Python 3.12; ruff line-length 100, rules `E,F,I,UP,B`. Lint must pass.
- Run tests with `.venv/bin/python -m pytest`; lint with `.venv/bin/python -m ruff check .`
- Build the SPA with `npm --prefix web run build`.
- Work from `/Users/bsimmons/Coding_Projects/streetclip`.
- Conventional Commits. Never add a `Co-Authored-By` trailer.
- **Never write a secret into a tracked file.** Keys and admin passwords live in `.env`, which is gitignored.
- **Someone else's workspace returns 404, never 403** — a 403 confirms it exists.
- **An unapproved account returns 403, never 404**, on the three resource routes.
- Passwords are hashed with argon2id via `argon2-cffi`. Never bcrypt, never a bare hash.
- A password hash must never appear in any response body.
- Out of scope, do not build: rate limiting, upload quotas, TLS, password reset, email, per-user API keys.

## File Structure

| File | Responsibility |
|---|---|
| `src/streetclip/accounts.py` (**new**) | Users and sessions tables: schema, migration, CRUD |
| `src/streetclip/auth.py` (**new**) | argon2 hashing, cookie handling, `current_user` / `approved_user` dependencies, admin bootstrap |
| `src/streetclip/routes_auth.py` (**new**) | `APIRouter` for `/api/session` and `/api/users` |
| `src/streetclip/db.py` (modify) | `jobs.user_id` column, backfill, user-scoped queries |
| `src/streetclip/api.py` (modify) | Mount the router, apply dependencies, scope every workspace route |
| `web/src/Login.jsx` (**new**) | Login screen |
| `web/src/Pending.jsx` (**new**) | Awaiting-approval screen |
| `web/src/Users.jsx` (**new**) | Admin user management panel |
| `web/src/App.jsx` (modify) | Session gate before the shell renders |

---

### Task 1: Users and sessions schema

**Files:**
- Create: `src/streetclip/accounts.py`
- Test: `tests/test_accounts.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Consumes: nothing
- Produces: `Accounts(path: Path)` with `create_user(email, password_hash, is_admin=False, approved=False) -> int`, `get_user(user_id) -> dict | None`, `get_user_by_email(email) -> dict | None`, `list_users() -> list[dict]`, `set_approved(user_id, approved_by) -> None`, `clear_approved(user_id) -> None`, `set_disabled(user_id) -> None`, `delete_user(user_id) -> None`

`Accounts` reuses the same connection pattern as `Database` in `src/streetclip/db.py` — read that file's `connect()` contextmanager and copy its shape, including `PRAGMA foreign_keys`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `dependencies`:

```toml
    "argon2-cffi>=23.1",
```

Then run: `.venv/bin/python -m pip install -e "."`

- [ ] **Step 2: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from streetclip.accounts import Accounts


@pytest.fixture
def accounts(tmp_path: Path) -> Accounts:
    return Accounts(tmp_path / "s.db")


def test_create_and_read_a_user(accounts: Accounts):
    user_id = accounts.create_user("ben@example.com", "hash", is_admin=True)
    user = accounts.get_user(user_id)

    assert user["email"] == "ben@example.com"
    assert user["is_admin"] == 1
    assert user["approved_at"] is None, "accounts start pending unless approved"
    assert user["disabled_at"] is None


def test_email_lookup_ignores_case(accounts: Accounts):
    """Ben@x.com and ben@x.com must be one account, not two."""
    accounts.create_user("Ben@Example.com", "hash")
    assert accounts.get_user_by_email("ben@example.com") is not None


def test_duplicate_emails_are_refused(accounts: Accounts):
    accounts.create_user("ben@example.com", "hash")
    with pytest.raises(ValueError, match="already exists"):
        accounts.create_user("BEN@example.com", "other")


def test_create_approved(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h", approved=True)
    assert accounts.get_user(user_id)["approved_at"] is not None


def test_approve_records_who_and_when(accounts: Accounts):
    admin = accounts.create_user("admin@x.com", "h", is_admin=True, approved=True)
    user = accounts.create_user("user@x.com", "h")

    accounts.set_approved(user, approved_by=admin)
    row = accounts.get_user(user)
    assert row["approved_at"] > 0
    assert row["approved_by"] == admin


def test_revoke_clears_approval(accounts: Accounts):
    user = accounts.create_user("user@x.com", "h", approved=True)
    accounts.clear_approved(user)
    assert accounts.get_user(user)["approved_at"] is None


def test_disable_marks_the_account(accounts: Accounts):
    user = accounts.create_user("user@x.com", "h", approved=True)
    accounts.set_disabled(user)
    assert accounts.get_user(user)["disabled_at"] is not None


def test_list_users_puts_pending_first(accounts: Accounts):
    accounts.create_user("approved@x.com", "h", approved=True)
    accounts.create_user("pending@x.com", "h")

    emails = [u["email"] for u in accounts.list_users()]
    assert emails[0] == "pending@x.com", "pending accounts need the admin's attention"


def test_delete_removes_the_user(accounts: Accounts):
    user = accounts.create_user("user@x.com", "h")
    accounts.delete_user(user)
    assert accounts.get_user(user) is None


def test_migrate_is_idempotent(tmp_path: Path):
    path = tmp_path / "s.db"
    Accounts(path)
    Accounts(path)  # must not raise
    assert Accounts(path).list_users() == []
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'streetclip.accounts'`

- [ ] **Step 4: Write the module**

Create `src/streetclip/accounts.py`:

```python
"""User accounts and login sessions.

Separate from `db` so that module stays about jobs and clips. Both open the
same SQLite file; each owns its own tables.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

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
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
```

- [ ] **Step 5: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS

- [ ] **Step 6: Run the full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`
Expected: all pass, no lint errors.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/streetclip/accounts.py tests/test_accounts.py
git commit -m "feat(accounts): add users and sessions schema"
```

---

### Task 2: Session lifecycle

**Files:**
- Modify: `src/streetclip/accounts.py`
- Test: `tests/test_accounts.py`

**Interfaces:**
- Consumes: `Accounts` from Task 1
- Produces: `Accounts.create_session(user_id, ttl_seconds=SESSION_TTL) -> str`, `Accounts.resolve_session(session_id) -> dict | None` (returns the **user** row, refreshing `last_seen_at`), `Accounts.delete_session(session_id) -> None`, `Accounts.delete_user_sessions(user_id, keep: str | None = None) -> int`; module constant `SESSION_TTL = 30 * 24 * 3600`

`resolve_session` returning the user row (not the session row) is what the
FastAPI dependency needs — one call, one round trip.

- [ ] **Step 1: Write the failing tests**

```python
import time as _time


def test_a_session_resolves_to_its_user(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h", approved=True)
    token = accounts.create_session(user_id)

    user = accounts.resolve_session(token)
    assert user["id"] == user_id
    assert user["email"] == "a@b.com"


def test_session_tokens_are_unguessable(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h")
    tokens = {accounts.create_session(user_id) for _ in range(5)}
    assert len(tokens) == 5
    assert all(len(t) >= 32 for t in tokens)


def test_an_expired_session_does_not_resolve(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h")
    token = accounts.create_session(user_id, ttl_seconds=-1)
    assert accounts.resolve_session(token) is None


def test_an_unknown_session_does_not_resolve(accounts: Accounts):
    assert accounts.resolve_session("nonsense") is None


def test_a_disabled_user_cannot_resolve_a_live_session(accounts: Accounts):
    """Disabling must cut off sessions that already exist."""
    user_id = accounts.create_user("a@b.com", "h", approved=True)
    token = accounts.create_session(user_id)
    accounts.set_disabled(user_id)
    assert accounts.resolve_session(token) is None


def test_deleting_a_session_logs_it_out(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h")
    token = accounts.create_session(user_id)
    accounts.delete_session(token)
    assert accounts.resolve_session(token) is None


def test_deleting_user_sessions_can_keep_the_current_one(accounts: Accounts):
    """Changing a password logs out everywhere except here."""
    user_id = accounts.create_user("a@b.com", "h")
    keep = accounts.create_session(user_id)
    other = accounts.create_session(user_id)

    assert accounts.delete_user_sessions(user_id, keep=keep) == 1
    assert accounts.resolve_session(keep) is not None
    assert accounts.resolve_session(other) is None


def test_deleting_a_user_removes_their_sessions(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h")
    token = accounts.create_session(user_id)
    accounts.delete_user(user_id)
    assert accounts.resolve_session(token) is None


def test_resolving_refreshes_last_seen(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", "h")
    token = accounts.create_session(user_id)
    with accounts.connect() as conn:
        before = conn.execute(
            "SELECT last_seen_at FROM sessions WHERE id = ?", (token,)
        ).fetchone()["last_seen_at"]

    _time.sleep(0.01)
    accounts.resolve_session(token)

    with accounts.connect() as conn:
        after = conn.execute(
            "SELECT last_seen_at FROM sessions WHERE id = ?", (token,)
        ).fetchone()["last_seen_at"]
    assert after > before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -k session -v`
Expected: FAIL — `AttributeError: 'Accounts' object has no attribute 'create_session'`

- [ ] **Step 3: Implement**

Add to the top of `src/streetclip/accounts.py`:

```python
import secrets

# Sessions are bearer tokens. Thirty days balances not re-authenticating a
# preacher every week against how long a stolen cookie stays useful.
SESSION_TTL = 30 * 24 * 3600
```

Add to the `Accounts` class:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_accounts.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite and lint, then commit**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add src/streetclip/accounts.py tests/test_accounts.py
git commit -m "feat(accounts): revocable login sessions"
```

---

### Task 3: Password hashing and admin bootstrap

**Files:**
- Create: `src/streetclip/auth.py`
- Test: `tests/test_auth.py`
- Modify: `src/streetclip/config.py`

**Interfaces:**
- Consumes: `Accounts` from Tasks 1-2
- Produces: `hash_password(password: str) -> str`, `verify_password(hash: str, password: str) -> bool`, `bootstrap_admin(accounts: Accounts, settings: Settings) -> int | None`, `NoAdminConfigured` exception
- New settings: `admin_email: str = ""`, `admin_password: str = ""`, `https: bool = False`, `open_signup: bool = False`

- [ ] **Step 1: Add the settings**

In `src/streetclip/config.py`, after the `input_dir` field:

```python
    # Accounts. The admin is seeded from these at startup; an existing account
    # with this email is left alone rather than having its password reset.
    admin_email: str = ""
    admin_password: str = ""
    # Set true behind TLS. The session cookie is marked Secure only when this
    # is on — a Secure cookie over plain HTTP is silently never sent, and login
    # appears to do nothing at all.
    https: bool = False
    open_signup: bool = False
```

- [ ] **Step 2: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest

from streetclip.accounts import Accounts
from streetclip.auth import (
    NoAdminConfigured,
    bootstrap_admin,
    hash_password,
    verify_password,
)
from streetclip.config import Settings


@pytest.fixture
def accounts(tmp_path: Path) -> Accounts:
    return Accounts(tmp_path / "s.db")


def test_hashing_round_trips():
    digest = hash_password("correct horse battery staple")
    assert verify_password(digest, "correct horse battery staple")


def test_a_wrong_password_is_rejected():
    digest = hash_password("right")
    assert not verify_password(digest, "wrong")


def test_the_hash_is_argon2_and_not_the_password():
    digest = hash_password("hunter2")
    assert digest.startswith("$argon2")
    assert "hunter2" not in digest


def test_hashes_are_salted():
    """Two identical passwords must not produce the same hash."""
    assert hash_password("same") != hash_password("same")


def test_verify_survives_a_corrupt_hash():
    """A mangled column must read as a failed login, not a crash."""
    assert not verify_password("not-a-hash", "anything")


def test_bootstrap_creates_an_approved_admin(accounts: Accounts):
    settings = Settings(admin_email="ben@x.com", admin_password="secret")
    user_id = bootstrap_admin(accounts, settings)

    user = accounts.get_user(user_id)
    assert user["is_admin"] == 1
    assert user["approved_at"] is not None, "the admin must not need approving"
    assert verify_password(user["password_hash"], "secret")


def test_bootstrap_leaves_an_existing_admin_alone(accounts: Accounts):
    """A compose restart must not reset a password the operator changed."""
    settings = Settings(admin_email="ben@x.com", admin_password="original")
    first = bootstrap_admin(accounts, settings)
    accounts_hash = accounts.get_user(first)["password_hash"]

    settings = Settings(admin_email="ben@x.com", admin_password="different")
    second = bootstrap_admin(accounts, settings)

    assert second == first
    assert accounts.get_user(first)["password_hash"] == accounts_hash


def test_bootstrap_refuses_when_nothing_is_configured(accounts: Accounts):
    """Starting with no accounts and no admin would serve the API wide open."""
    with pytest.raises(NoAdminConfigured):
        bootstrap_admin(accounts, Settings(admin_email="", admin_password=""))


def test_bootstrap_is_a_noop_when_users_already_exist(accounts: Accounts):
    accounts.create_user("someone@x.com", hash_password("x"), is_admin=True, approved=True)
    assert bootstrap_admin(accounts, Settings()) is None
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'streetclip.auth'`

- [ ] **Step 4: Implement**

Create `src/streetclip/auth.py`:

```python
"""Passwords, sessions cookies, and the dependencies that guard the API."""

from __future__ import annotations

import logging

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError

from streetclip.accounts import Accounts
from streetclip.config import Settings

log = logging.getLogger(__name__)

COOKIE_NAME = "streetclip_session"

_hasher = PasswordHasher()


class NoAdminConfigured(RuntimeError):
    """No accounts exist and no admin was configured."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    """False rather than raising: every failure path is a failed login."""
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, Argon2Error, TypeError, ValueError):
        return False


def bootstrap_admin(accounts: Accounts, settings: Settings) -> int | None:
    """Seed the admin from the environment.

    Returns the admin's id when one is created, None when accounts already
    exist. Raises when there is nothing to log in as — refusing to start is
    better than serving the API with no way to authenticate.
    """
    if accounts.list_users():
        return None

    if not settings.admin_email or not settings.admin_password:
        raise NoAdminConfigured(
            "no accounts exist and no admin is configured. Set "
            "STREETCLIP_ADMIN_EMAIL and STREETCLIP_ADMIN_PASSWORD."
        )

    existing = accounts.get_user_by_email(settings.admin_email)
    if existing is not None:
        return int(existing["id"])

    user_id = accounts.create_user(
        settings.admin_email,
        hash_password(settings.admin_password),
        is_admin=True,
        approved=True,
    )
    log.info("seeded admin account %s", settings.admin_email)
    return user_id
```

- [ ] **Step 5: Run the tests, full suite, lint, and commit**

```bash
.venv/bin/python -m pytest tests/test_auth.py -v
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add src/streetclip/auth.py src/streetclip/config.py tests/test_auth.py
git commit -m "feat(auth): argon2 password hashing and admin bootstrap"
```

---

### Task 4: Request dependencies

**Files:**
- Modify: `src/streetclip/auth.py`
- Test: `tests/test_auth.py`

**Interfaces:**
- Consumes: `Accounts.resolve_session`, `COOKIE_NAME`
- Produces: `set_session_cookie(response, token, settings) -> None`, `clear_session_cookie(response) -> None`, `make_dependencies(accounts: Accounts) -> tuple[Callable, Callable, Callable]` returning `(current_user, approved_user, admin_user)` FastAPI dependencies

`make_dependencies` is a factory because `Accounts` is built inside
`create_app` — a module-level dependency would have nothing to bind to.

- [ ] **Step 1: Write the failing tests**

```python
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from streetclip.auth import COOKIE_NAME, make_dependencies


def _app(accounts: Accounts) -> FastAPI:
    current_user, approved_user, admin_user = make_dependencies(accounts)
    app = FastAPI()

    @app.get("/who")
    def who(user=Depends(current_user)):
        return {"email": user["email"]}

    @app.get("/spend")
    def spend(user=Depends(approved_user)):
        return {"ok": True}

    @app.get("/admin")
    def admin(user=Depends(admin_user)):
        return {"ok": True}

    return app


def test_no_cookie_is_401(accounts: Accounts):
    with TestClient(_app(accounts)) as client:
        assert client.get("/who").status_code == 401


def test_a_valid_session_identifies_the_user(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", hash_password("x"), approved=True)
    token = accounts.create_session(user_id)

    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/who").json()["email"] == "a@b.com"


def test_a_pending_user_is_403_on_resource_routes(accounts: Accounts):
    """Identity is fine; spending the machine's resources is not."""
    user_id = accounts.create_user("a@b.com", hash_password("x"))
    token = accounts.create_session(user_id)

    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/who").status_code == 200
        assert client.get("/spend").status_code == 403


def test_an_approved_user_may_spend(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", hash_password("x"), approved=True)
    token = accounts.create_session(user_id)

    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/spend").status_code == 200


def test_a_non_admin_is_403_on_admin_routes(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", hash_password("x"), approved=True)
    token = accounts.create_session(user_id)

    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/admin").status_code == 403


def test_an_admin_passes(accounts: Accounts):
    user_id = accounts.create_user("a@b.com", hash_password("x"), is_admin=True, approved=True)
    token = accounts.create_session(user_id)

    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, token)
        assert client.get("/admin").status_code == 200


def test_a_stale_cookie_is_401(accounts: Accounts):
    with TestClient(_app(accounts)) as client:
        client.cookies.set(COOKIE_NAME, "long-gone")
        assert client.get("/who").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_auth.py -k "cookie or session or pending or admin or spend" -v`
Expected: FAIL — `ImportError: cannot import name 'make_dependencies'`

- [ ] **Step 3: Implement**

Add to `src/streetclip/auth.py`:

```python
from collections.abc import Callable

from fastapi import Cookie, HTTPException, Response


def set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        # Only behind TLS: a Secure cookie on plain HTTP is never sent back,
        # which looks exactly like a login that silently does nothing.
        secure=settings.https,
        max_age=SESSION_TTL,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


def make_dependencies(accounts: Accounts) -> tuple[Callable, Callable, Callable]:
    """Build the request dependencies against a live Accounts instance."""

    def current_user(session: str | None = Cookie(default=None, alias=COOKIE_NAME)):
        user = accounts.resolve_session(session or "")
        if user is None:
            raise HTTPException(401, "not signed in")
        return user

    def approved_user(user=Depends(current_user)):
        if user["approved_at"] is None:
            raise HTTPException(403, "this account is awaiting approval")
        return user

    def admin_user(user=Depends(current_user)):
        if not user["is_admin"]:
            raise HTTPException(403, "admin only")
        return user

    return current_user, approved_user, admin_user
```

Add `Depends` and `SESSION_TTL` to the imports at the top:

```python
from fastapi import Cookie, Depends, HTTPException, Response

from streetclip.accounts import SESSION_TTL, Accounts
```

- [ ] **Step 4: Run the tests, full suite, lint, and commit**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add src/streetclip/auth.py tests/test_auth.py
git commit -m "feat(auth): request dependencies for identity, approval, and admin"
```

---

### Task 5: Session and user routes

**Files:**
- Create: `src/streetclip/routes_auth.py`
- Test: `tests/test_routes_auth.py`

**Interfaces:**
- Consumes: `Accounts`, `make_dependencies`, `hash_password`, `verify_password`, `set_session_cookie`, `clear_session_cookie`
- Produces: `build_auth_router(accounts: Accounts, settings: Settings) -> APIRouter`, `user_payload(row: dict) -> dict`

`user_payload` must never include `password_hash`.

- [ ] **Step 1: Write the failing tests**

```python
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from streetclip.accounts import Accounts
from streetclip.auth import COOKIE_NAME, hash_password
from streetclip.config import Settings
from streetclip.routes_auth import build_auth_router


@pytest.fixture
def env(tmp_path: Path):
    accounts = Accounts(tmp_path / "s.db")
    admin_id = accounts.create_user(
        "admin@x.com", hash_password("adminpw"), is_admin=True, approved=True
    )
    app = FastAPI()
    app.include_router(build_auth_router(accounts, Settings()))
    with TestClient(app) as client:
        yield client, accounts, admin_id


def _login(client, email, password):
    return client.post("/api/session", json={"email": email, "password": password})


def test_login_sets_a_cookie(env):
    client, _, _ = env
    response = _login(client, "admin@x.com", "adminpw")
    assert response.status_code == 200
    assert COOKIE_NAME in response.cookies
    assert response.json()["email"] == "admin@x.com"


def test_login_never_returns_the_hash(env):
    client, _, _ = env
    assert "password_hash" not in _login(client, "admin@x.com", "adminpw").json()


def test_a_wrong_password_is_401(env):
    client, _, _ = env
    assert _login(client, "admin@x.com", "nope").status_code == 401


def test_an_unknown_email_is_401_with_the_same_message(env):
    """The error must not reveal whether the account exists."""
    client, _, _ = env
    wrong_password = _login(client, "admin@x.com", "nope")
    unknown = _login(client, "ghost@x.com", "nope")
    assert unknown.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]


def test_a_disabled_user_cannot_log_in(env):
    client, accounts, _ = env
    user_id = accounts.create_user("gone@x.com", hash_password("pw"), approved=True)
    accounts.set_disabled(user_id)
    assert _login(client, "gone@x.com", "pw").status_code == 401


def test_reading_the_session(env):
    client, _, _ = env
    _login(client, "admin@x.com", "adminpw")
    body = client.get("/api/session").json()
    assert body["email"] == "admin@x.com"
    assert body["is_admin"] is True


def test_reading_the_session_without_one_is_401(env):
    client, _, _ = env
    assert client.get("/api/session").status_code == 401


def test_logout_clears_the_session(env):
    client, _, _ = env
    _login(client, "admin@x.com", "adminpw")
    assert client.delete("/api/session").status_code == 204
    assert client.get("/api/session").status_code == 401


def test_changing_a_password_logs_out_other_sessions(env):
    client, accounts, admin_id = env
    other = accounts.create_session(admin_id)
    _login(client, "admin@x.com", "adminpw")

    response = client.post(
        "/api/session/password", json={"current": "adminpw", "new": "longer-secret"}
    )
    assert response.status_code == 204
    assert accounts.resolve_session(other) is None, "other devices must be signed out"
    assert client.get("/api/session").status_code == 200, "this one stays signed in"


def test_changing_a_password_requires_the_current_one(env):
    client, _, _ = env
    _login(client, "admin@x.com", "adminpw")
    response = client.post(
        "/api/session/password", json={"current": "wrong", "new": "longer-secret"}
    )
    assert response.status_code == 403


def test_admin_creates_an_approved_account(env):
    client, accounts, _ = env
    _login(client, "admin@x.com", "adminpw")

    response = client.post("/api/users", json={"email": "new@x.com", "password": "pw123456"})
    assert response.status_code == 201
    assert response.json()["approved"] is True, "admin-created accounts are vouched for"


def test_a_non_admin_cannot_list_users(env):
    client, accounts, _ = env
    accounts.create_user("plain@x.com", hash_password("pw"), approved=True)
    _login(client, "plain@x.com", "pw")
    assert client.get("/api/users").status_code == 403


def test_approve_and_revoke(env):
    client, accounts, _ = env
    user_id = accounts.create_user("pending@x.com", hash_password("pw"))
    _login(client, "admin@x.com", "adminpw")

    assert client.post(f"/api/users/{user_id}/approve").status_code == 200
    assert accounts.get_user(user_id)["approved_at"] is not None

    assert client.post(f"/api/users/{user_id}/revoke").status_code == 200
    assert accounts.get_user(user_id)["approved_at"] is None


def test_disabling_kills_live_sessions(env):
    client, accounts, _ = env
    user_id = accounts.create_user("bad@x.com", hash_password("pw"), approved=True)
    token = accounts.create_session(user_id)
    _login(client, "admin@x.com", "adminpw")

    assert client.post(f"/api/users/{user_id}/disable").status_code == 200
    assert accounts.resolve_session(token) is None


def test_signup_is_closed_by_default(env):
    client, _, _ = env
    response = client.post("/api/signup", json={"email": "x@y.com", "password": "pw123456"})
    assert response.status_code == 404


def test_signup_creates_a_pending_account_when_open(tmp_path: Path):
    accounts = Accounts(tmp_path / "s.db")
    app = FastAPI()
    app.include_router(build_auth_router(accounts, Settings(open_signup=True)))

    with TestClient(app) as client:
        response = client.post(
            "/api/signup", json={"email": "x@y.com", "password": "pw123456"}
        )
        assert response.status_code == 201
        assert response.json()["approved"] is False, "self-signup must await approval"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_routes_auth.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'streetclip.routes_auth'`

- [ ] **Step 3: Implement**

Create `src/streetclip/routes_auth.py`:

```python
"""Routes for signing in and, for the admin, managing who may sign in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field

from streetclip.accounts import Accounts
from streetclip.auth import (
    clear_session_cookie,
    hash_password,
    make_dependencies,
    set_session_cookie,
    verify_password,
)
from streetclip.config import Settings

# Deliberately identical for a wrong password and an unknown address, so the
# response cannot be used to discover which accounts exist.
BAD_LOGIN = "email or password is incorrect"


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class NewAccount(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class PasswordChange(BaseModel):
    current: str = Field(min_length=1)
    new: str = Field(min_length=8)


def user_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Never includes password_hash."""
    return {
        "id": row["id"],
        "email": row["email"],
        "is_admin": bool(row["is_admin"]),
        "approved": row["approved_at"] is not None,
        "disabled": row["disabled_at"] is not None,
        "created_at": row["created_at"],
    }


def build_auth_router(accounts: Accounts, settings: Settings) -> APIRouter:
    router = APIRouter()
    current_user, _approved_user, admin_user = make_dependencies(accounts)

    @router.post("/api/session")
    def log_in(credentials: Credentials, response: Response) -> dict[str, Any]:
        user = accounts.get_user_by_email(credentials.email)
        # Verify even when the account is missing, so a wrong address is not
        # measurably faster to reject than a wrong password.
        digest = user["password_hash"] if user else hash_password("placeholder")
        ok = verify_password(digest, credentials.password)

        if user is None or not ok or user["disabled_at"] is not None:
            raise HTTPException(401, BAD_LOGIN)

        set_session_cookie(response, accounts.create_session(user["id"]), settings)
        return user_payload(user)

    @router.get("/api/session")
    def read_session(user=Depends(current_user)) -> dict[str, Any]:
        return user_payload(user)

    @router.delete("/api/session", status_code=204)
    def log_out(response: Response, user=Depends(current_user)) -> Response:
        accounts.delete_user_sessions(user["id"], keep=None)
        clear_session_cookie(response)
        return Response(status_code=204)

    @router.post("/api/session/password", status_code=204)
    def change_password(change: PasswordChange, user=Depends(current_user)) -> Response:
        if not verify_password(user["password_hash"], change.current):
            raise HTTPException(403, "current password is incorrect")

        with accounts.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(change.new), user["id"]),
            )
        # Everything else signed in as this user is now suspect.
        accounts.delete_user_sessions(user["id"], keep=None)
        return Response(status_code=204)

    # --- admin ---------------------------------------------------------------

    @router.get("/api/users")
    def list_users(admin=Depends(admin_user)) -> list[dict[str, Any]]:
        return [user_payload(u) for u in accounts.list_users()]

    @router.post("/api/users", status_code=201)
    def create_user(account: NewAccount, admin=Depends(admin_user)) -> dict[str, Any]:
        try:
            user_id = accounts.create_user(
                account.email, hash_password(account.password), approved=True
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return user_payload(accounts.get_user(user_id))

    @router.post("/api/users/{user_id}/approve")
    def approve(user_id: int, admin=Depends(admin_user)) -> dict[str, Any]:
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "no such account")
        accounts.set_approved(user_id, approved_by=admin["id"])
        return user_payload(accounts.get_user(user_id))

    @router.post("/api/users/{user_id}/revoke")
    def revoke(user_id: int, admin=Depends(admin_user)) -> dict[str, Any]:
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "no such account")
        accounts.clear_approved(user_id)
        return user_payload(accounts.get_user(user_id))

    @router.post("/api/users/{user_id}/disable")
    def disable(user_id: int, admin=Depends(admin_user)) -> dict[str, Any]:
        if accounts.get_user(user_id) is None:
            raise HTTPException(404, "no such account")
        accounts.set_disabled(user_id)
        # Disabling has to take effect now, not when the cookie expires.
        accounts.delete_user_sessions(user_id, keep=None)
        return user_payload(accounts.get_user(user_id))

    @router.post("/api/signup", status_code=201)
    def sign_up(account: NewAccount) -> dict[str, Any]:
        if not settings.open_signup:
            raise HTTPException(404, "not found")
        try:
            user_id = accounts.create_user(
                account.email, hash_password(account.password), approved=False
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return user_payload(accounts.get_user(user_id))

    return router
```

Add `"email-validator>=2.0",` to `dependencies` in `pyproject.toml` — pydantic's
`EmailStr` requires it — then `.venv/bin/python -m pip install -e "."`.

- [ ] **Step 4: Run the tests, full suite, lint, and commit**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add pyproject.toml src/streetclip/routes_auth.py tests/test_routes_auth.py
git commit -m "feat(auth): session and admin user routes"
```

---

### Task 6: Workspace ownership in the database

**Files:**
- Modify: `src/streetclip/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: nothing
- Produces: `jobs.user_id` column; `Database.create_job(kind, source_path, source_name, user_id: int | None = None) -> int`; `Database.list_workspaces(user_id: int, limit: int = 100)`; `Database.backfill_owner(user_id: int) -> int`

`list_workspaces` **gains a required `user_id` first argument.** Every existing
caller must pass one — that is the point, so an unscoped call fails loudly at
import rather than quietly listing everyone's work.

- [ ] **Step 1: Write the failing tests**

```python
def test_jobs_carry_an_owner(db: Database):
    job_id = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=7)
    assert db.get_job(job_id)["user_id"] == 7


def test_list_workspaces_only_returns_your_own(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    db.create_job(JobKind.ANALYZE, Path("/x/b.mp4"), "b.mp4", user_id=2)

    mine = db.list_workspaces(user_id=1)
    assert [w["source_name"] for w in mine] == ["a.mp4"]


def test_list_workspaces_for_a_user_with_none(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=1)
    assert db.list_workspaces(user_id=99) == []


def test_backfill_assigns_ownerless_jobs(db: Database):
    """Workspaces created before accounts existed become the admin's."""
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4")
    db.create_job(JobKind.ANALYZE, Path("/x/b.mp4"), "b.mp4")

    assert db.backfill_owner(user_id=1) == 2
    assert db.list_workspaces(user_id=1)[0]["user_id"] == 1
    # Idempotent: nothing left without an owner.
    assert db.backfill_owner(user_id=1) == 0


def test_backfill_leaves_owned_jobs_alone(db: Database):
    db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=5)
    db.backfill_owner(user_id=1)
    assert db.list_workspaces(user_id=5)[0]["user_id"] == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_db.py -k "owner or only_returns" -v`
Expected: FAIL — `TypeError: create_job() got an unexpected keyword argument 'user_id'`

- [ ] **Step 3: Implement**

In `src/streetclip/db.py`, add `user_id INTEGER REFERENCES users(id),` to the
`jobs` table in `SCHEMA`, and add `("user_id", "INTEGER REFERENCES users(id)")`
to the `ADDED_COLUMNS` tuple so existing databases gain it by `ALTER`.

Change `create_job`:

```python
    def create_job(
        self,
        kind: JobKind,
        source_path: Path,
        source_name: str,
        user_id: int | None = None,
    ) -> int:
        now = time.time()
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO jobs (kind, status, source_path, source_name, user_id,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    kind.value,
                    JobStatus.QUEUED.value,
                    str(source_path),
                    source_name,
                    user_id,
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)
```

Change `list_workspaces` to take the owner:

```python
    def list_workspaces(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        """One user's analyze jobs with their clip tallies, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT j.*,"
                " COUNT(c.id) AS clip_count,"
                " COALESCE(SUM(c.selected), 0) AS kept_count,"
                " COUNT(c.rendered_path) AS rendered_count"
                " FROM jobs j LEFT JOIN clips c ON c.job_id = j.id"
                " WHERE j.kind = ? AND j.user_id = ?"
                " GROUP BY j.id ORDER BY j.id DESC LIMIT ?",
                (JobKind.ANALYZE.value, user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def backfill_owner(self, user_id: int) -> int:
        """Give ownerless jobs to a user. Returns how many were claimed."""
        with self.connect() as conn:
            cursor = conn.execute(
                "UPDATE jobs SET user_id = ? WHERE user_id IS NULL", (user_id,)
            )
            return cursor.rowcount
```

- [ ] **Step 4: Run the tests, full suite, lint, and commit**

The full suite will show failures in `tests/test_api.py` because
`list_workspaces` now requires `user_id` — that is expected and Task 7 fixes
them. Confirm `tests/test_db.py` passes on its own before committing.

```bash
.venv/bin/python -m pytest tests/test_db.py -v
git add src/streetclip/db.py tests/test_db.py
git commit -m "feat(db): give jobs an owner and scope workspace listing"
```

---

### Task 7: Guard the API

**Files:**
- Modify: `src/streetclip/api.py`
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `build_auth_router`, `make_dependencies`, `bootstrap_admin`, `Accounts`, `Database.list_workspaces(user_id)`, `Database.backfill_owner`
- Produces: an app where every `/api/*` route requires a session

Every workspace route gains a `_owned(job_id, user)` check that raises **404**
when the job does not exist *or* belongs to someone else. The three resource
routes take `approved_user` instead of `current_user`.

- [ ] **Step 1: Write the failing tests**

Replace the `env` fixture in `tests/test_api.py` with one that signs in, and add
the isolation tests:

```python
@pytest.fixture
def env(tmp_path: Path):
    """An app with a signed-in approved user, and no worker thread."""
    from streetclip.accounts import Accounts
    from streetclip.auth import COOKIE_NAME, hash_password

    data_dir = tmp_path / "data"
    input_dir = tmp_path / "input"
    input_dir.mkdir(parents=True)

    settings = Settings(
        data_dir=str(data_dir),
        input_dir=str(input_dir),
        admin_email="admin@x.com",
        admin_password="adminpw",
    )
    app = create_app(settings, data_dir=data_dir, start_worker=False)
    accounts = Accounts(data_dir / "streetclip.db")
    db = Database(data_dir / "streetclip.db")

    user_id = accounts.create_user("me@x.com", hash_password("pw"), approved=True)
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, accounts.create_session(user_id))
        yield client, db, input_dir, user_id, accounts


def test_every_api_route_requires_a_session(env):
    """The check that stops the next endpoint shipping unprotected."""
    client, _, _, _, _ = env
    client.cookies.clear()

    for path, method in [
        ("/api/inputs", "get"),
        ("/api/workspaces", "get"),
        ("/api/workspaces/1", "get"),
        ("/api/workspaces/1/transcript", "get"),
        ("/api/workspaces/1/poster", "get"),
        ("/api/workspaces/1/source", "get"),
        ("/api/workspaces/1/render", "post"),
        ("/api/clips/1", "patch"),
        ("/api/clips/1/download", "get"),
    ]:
        response = getattr(client, method)(path, json={} if method == "patch" else None)
        assert response.status_code == 401, f"{method.upper()} {path} is unprotected"


def test_another_users_workspace_is_404_not_403(env):
    """403 would confirm the workspace exists."""
    client, db, _, _, _ = env
    theirs = db.create_job(JobKind.ANALYZE, Path("/x/a.mp4"), "a.mp4", user_id=999)

    assert client.get(f"/api/workspaces/{theirs}").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/transcript").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/poster").status_code == 404
    assert client.get(f"/api/workspaces/{theirs}/source").status_code == 404
    assert client.delete(f"/api/workspaces/{theirs}").status_code == 404
    assert client.patch(f"/api/workspaces/{theirs}", json={"title": "x"}).status_code == 404
    assert client.post(f"/api/workspaces/{theirs}/render").status_code == 404


def test_the_list_shows_only_your_own(env):
    client, db, _, user_id, _ = env
    db.create_job(JobKind.ANALYZE, Path("/x/mine.mp4"), "mine.mp4", user_id=user_id)
    db.create_job(JobKind.ANALYZE, Path("/x/theirs.mp4"), "theirs.mp4", user_id=999)

    names = [w["source_name"] for w in client.get("/api/workspaces").json()]
    assert names == ["mine.mp4"]


def test_a_pending_user_cannot_spend_resources(env, tmp_path: Path):
    client, _, input_dir, _, accounts = env
    from streetclip.auth import COOKIE_NAME, hash_password

    pending = accounts.create_user("pending@x.com", hash_password("pw"))
    client.cookies.set(COOKIE_NAME, accounts.create_session(pending))

    source = input_dir / "s.mp4"
    source.write_bytes(b"x")
    assert client.post("/api/workspaces", json={"path": str(source)}).status_code == 403
    upload = client.post(
        "/api/workspaces/upload", files={"file": ("s.mp4", b"x", "video/mp4")}
    )
    assert upload.status_code == 403


def test_a_pending_user_can_still_read(env):
    client, _, _, _, accounts = env
    from streetclip.auth import COOKIE_NAME, hash_password

    pending = accounts.create_user("pending@x.com", hash_password("pw"))
    client.cookies.set(COOKIE_NAME, accounts.create_session(pending))

    assert client.get("/api/workspaces").status_code == 200
    assert client.get("/api/inputs").status_code == 200


def test_uploads_go_to_a_per_user_directory(env, tmp_path: Path):
    """Two users uploading the same filename must not overwrite each other."""
    client, _, _, user_id, _ = env
    client.post("/api/workspaces/upload", files={"file": ("s.mp4", b"x", "video/mp4")})
    assert (tmp_path / "data" / "uploads" / str(user_id) / "s.mp4").is_file()
```

Every other test in the file that called `client, db, input_dir = env` must be
updated to unpack five values.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_api.py -v`
Expected: FAIL — 200s where 401 is expected; `ValueError: too many values to unpack`.

- [ ] **Step 3: Implement**

In `src/streetclip/api.py`, inside `create_app` after `db = Database(...)`:

```python
    accounts = Accounts(data_dir / "streetclip.db")
    admin_id = bootstrap_admin(accounts, settings)
    if admin_id is not None:
        # Workspaces that predate accounts become the admin's.
        db.backfill_owner(admin_id)

    current_user, approved_user, _admin_user = make_dependencies(accounts)
    app.include_router(build_auth_router(accounts, settings))
```

Add a helper inside `create_app`:

```python
    def _owned(job_id: int, user: dict[str, Any]) -> dict[str, Any]:
        """The job, if this user owns it.

        404 rather than 403 for someone else's workspace: a 403 confirms one
        exists at that id.
        """
        job = db.get_job(job_id)
        if job is None or job["user_id"] != user["id"]:
            raise HTTPException(404, "no such workspace")
        return job
```

Then, for every workspace and clip route:

- add `user=Depends(current_user)` (or `Depends(approved_user)` for the three
  resource routes) as the last parameter
- replace `db.get_job(job_id)` ownership checks with `_owned(job_id, user)`
- pass `user_id=user["id"]` to `db.create_job(...)`
- change `db.list_workspaces()` to `db.list_workspaces(user["id"])`
- change the upload destination to `data_dir / "uploads" / str(user["id"])`

For clip routes, ownership is checked through the clip's job:

```python
    def _owned_clip(clip_id: int, user: dict[str, Any]) -> dict[str, Any]:
        clip = db.get_clip(clip_id)
        if clip is None:
            raise HTTPException(404, "no such clip")
        _owned(clip["job_id"], user)
        return clip
```

Add the imports:

```python
from streetclip.accounts import Accounts
from streetclip.auth import bootstrap_admin, make_dependencies
from streetclip.routes_auth import build_auth_router
```

- [ ] **Step 4: Run the tests, full suite, lint, and commit**

```bash
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add src/streetclip/api.py tests/test_api.py
git commit -m "feat(api): scope every workspace route to its owner"
```

---

### Task 8: Login, pending, and admin screens

**Files:**
- Create: `web/src/Login.jsx`, `web/src/Pending.jsx`, `web/src/Users.jsx`
- Modify: `web/src/App.jsx`, `web/src/api.js`, `web/src/styles.css`

**Interfaces:**
- Consumes: `/api/session`, `/api/users`
- Produces: a session gate — nothing renders until `/api/session` returns a user

- [ ] **Step 1: Add the client functions**

In `web/src/api.js`:

```js
export const readSession = () => request("/api/session");
export const logIn = (email, password) => request("/api/session", json({ email, password }));
export const listUsers = () => request("/api/users");
export const createUser = (email, password) => request("/api/users", json({ email, password }));
export const approveUser = (id) => request(`/api/users/${id}/approve`, { method: "POST" });
export const revokeUser = (id) => request(`/api/users/${id}/revoke`, { method: "POST" });
export const disableUser = (id) => request(`/api/users/${id}/disable`, { method: "POST" });

export async function logOut() {
  const response = await fetch("/api/session", { method: "DELETE" });
  if (!response.ok) throw new Error("could not sign out");
}
```

- [ ] **Step 2: Write the login screen**

Create `web/src/Login.jsx`:

```jsx
import { useState } from "react";
import * as api from "./api";

export default function Login({ onSignedIn }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    api
      .logIn(email, password)
      .then(onSignedIn)
      .catch((err) => setError(err.message))
      .finally(() => setBusy(false));
  }

  return (
    <form className="login" onSubmit={submit}>
      <h1>
        street<span>clip</span>
      </h1>
      <label className="section-label" htmlFor="email">
        Email
      </label>
      <input
        id="email"
        type="email"
        autoComplete="username"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
      />
      <label className="section-label" htmlFor="password">
        Password
      </label>
      <input
        id="password"
        type="password"
        autoComplete="current-password"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
        required
      />
      {error && <p className="login-error">{error}</p>}
      <button className="btn primary" type="submit" disabled={busy}>
        {busy ? "Signing in…" : "Sign in"}
      </button>
    </form>
  );
}
```

- [ ] **Step 3: Write the pending screen**

Create `web/src/Pending.jsx`:

```jsx
export default function Pending({ email, onSignOut }) {
  return (
    <div className="progress">
      <h2>Waiting for approval</h2>
      <p className="stage">{email}</p>
      <p className="reason">
        Your account exists but has not been approved yet. Once the administrator
        approves it you will be able to add recordings.
      </p>
      <p>
        <button className="btn" onClick={onSignOut}>
          Sign out
        </button>
      </p>
    </div>
  );
}
```

- [ ] **Step 4: Write the admin panel**

Create `web/src/Users.jsx`:

```jsx
import { useCallback, useEffect, useState } from "react";
import * as api from "./api";

export default function Users({ onError }) {
  const [rows, setRows] = useState([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");

  const refresh = useCallback(() => {
    api.listUsers().then(setRows).catch(onError);
  }, [onError]);

  useEffect(refresh, [refresh]);

  function act(promise) {
    promise.then(refresh).catch(onError);
  }

  function add(event) {
    event.preventDefault();
    act(api.createUser(email, password));
    setEmail("");
    setPassword("");
  }

  return (
    <div className="home">
      <div className="home-head">
        <h1>Accounts</h1>
      </div>

      <form className="new-user" onSubmit={add}>
        <input
          type="email"
          placeholder="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <input
          type="password"
          placeholder="password (8+ characters)"
          value={password}
          minLength={8}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        <button className="btn primary" type="submit">
          Create
        </button>
      </form>

      <div className="file-list">
        {rows.map((u) => (
          <div key={u.id} className="file-row">
            <span className="name">{u.email}</span>
            <span className="size">
              {u.disabled ? "disabled" : u.approved ? "approved" : "pending"}
              {u.is_admin && " · admin"}
            </span>
            {!u.approved && !u.disabled && (
              <button className="btn ghost" onClick={() => act(api.approveUser(u.id))}>
                Approve
              </button>
            )}
            {u.approved && !u.is_admin && (
              <button className="btn ghost" onClick={() => act(api.revokeUser(u.id))}>
                Revoke
              </button>
            )}
            {!u.is_admin && !u.disabled && (
              <button className="btn ghost" onClick={() => act(api.disableUser(u.id))}>
                Disable
              </button>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Gate the app on a session**

In `web/src/App.jsx`, add near the other state:

```jsx
  const [session, setSession] = useState(undefined); // undefined = still checking
  const [showUsers, setShowUsers] = useState(false);

  useEffect(() => {
    api
      .readSession()
      .then(setSession)
      .catch(() => setSession(null));
  }, []);

  function signOut() {
    api.logOut().then(() => setSession(null)).catch(fail);
  }
```

Then, before the existing body selection:

```jsx
  if (session === undefined) return <div className="progress"><p className="stage">…</p></div>;
  if (session === null) return <Login onSignedIn={setSession} />;
  if (!session.approved) return <Pending email={session.email} onSignOut={signOut} />;
```

Add to the topbar, before the existing back button:

```jsx
        {session.is_admin && (
          <button className="btn ghost" onClick={() => setShowUsers((s) => !s)}>
            {showUsers ? "Workspaces" : "Accounts"}
          </button>
        )}
        <span className="source">{session.email}</span>
        <button className="btn ghost" onClick={signOut}>
          Sign out
        </button>
```

And when `showUsers` is true, render `<Users onError={fail} />` as the body.

Import `Login`, `Pending`, and `Users` at the top of the file.

- [ ] **Step 6: Add the styles**

Append to `web/src/styles.css`:

```css
/* --- login ---------------------------------------------------------------- */

.login {
  width: min(360px, 100%);
  margin: 0 auto;
  padding: 90px 28px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.login h1 {
  font-family: var(--display);
  font-weight: 800;
  font-size: 28px;
  text-transform: lowercase;
  letter-spacing: -0.02em;
  margin: 0 0 24px;
}

.login h1 span {
  color: var(--sodium);
}

.login input {
  background: var(--slab);
  border: 1px solid var(--edge);
  border-radius: 3px;
  padding: 10px 12px;
  margin-bottom: 10px;
}

.login input:focus {
  outline: none;
  border-color: var(--sodium);
}

.login .btn {
  margin-top: 10px;
}

.login-error {
  color: var(--alarm);
  font-size: 14px;
  margin: 4px 0 0;
}

.new-user {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
}

.new-user input {
  background: var(--slab);
  border: 1px solid var(--edge);
  border-radius: 3px;
  padding: 8px 10px;
}
```

- [ ] **Step 7: Build, verify, and commit**

```bash
npm --prefix web run build
.venv/bin/python -m pytest && .venv/bin/python -m ruff check .
git add web/src
git commit -m "feat(web): login, pending, and admin account screens"
```

---

### Task 9: End-to-end verification

**Files:** none — verification only.

- [ ] **Step 1: Full suite and lint**

Run: `.venv/bin/python -m pytest && .venv/bin/python -m ruff check .`
Expected: all pass.

- [ ] **Step 2: Configure the admin and rebuild**

Add to `.env` (gitignored — never to `compose.yaml`):

```
STREETCLIP_ADMIN_EMAIL=simmons.b2277@gmail.com
STREETCLIP_ADMIN_PASSWORD=<choose one>
```

Add both to `compose.yaml`'s `environment` block as `${VAR:-}` passthroughs,
with no inline defaults.

```bash
docker compose up -d --build
```

- [ ] **Step 3: Confirm the migration against the real database**

```bash
.venv/bin/python -c "
from pathlib import Path
from streetclip.accounts import Accounts
from streetclip.db import Database
a = Accounts(Path('data/streetclip.db'))
print('users:', [(u['email'], bool(u['is_admin']), u['approved_at'] is not None) for u in a.list_users()])
admin = a.list_users()[0]['id']
print('workspaces owned by admin:', len(Database(Path('data/streetclip.db')).list_workspaces(admin)))
"
```

Expected: one admin, approved, owning the four existing workspaces.

- [ ] **Step 4: Walk it in a browser**

Open `http://localhost:8080`. Confirm: login is required; signing in shows your
four workspaces; Accounts panel appears for the admin; create a second account,
sign in as it in a private window, and confirm it sees **no** workspaces and
cannot open one of yours by id. Revoke its approval and confirm uploading is
refused with the awaiting-approval message.

- [ ] **Step 5: Commit any fixes**

```bash
git add -A
git commit -m "fix: address issues found in end-to-end verification"
```

---

## Self-Review

**Spec coverage:** schema → Task 1; sessions → Task 2; argon2 + bootstrap →
Task 3; dependencies → Task 4; routes → Task 5; ownership + backfill → Task 6;
authorization, 404-not-403, approval gate, per-user uploads, route-table test →
Task 7; UI → Task 8; real-database migration → Task 9. Every spec section maps
to a task.

**Type consistency:** `Accounts.resolve_session` returns a **user** row and is
used that way in Task 4. `list_workspaces(user_id, limit)` is defined in Task 6
and called with that signature in Task 7. `user_payload` is defined in Task 5
and is the only shape the web client in Task 8 reads.

**Known risk:** Task 7 is the largest and touches every route in `api.py`. It is
deliberately last among the Python tasks so it lands on a green suite, and its
route-table test is the safety net for anything missed.
