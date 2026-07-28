from __future__ import annotations

import time as _time
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
