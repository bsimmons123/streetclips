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
