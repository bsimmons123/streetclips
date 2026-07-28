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
