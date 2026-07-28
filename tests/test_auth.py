from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from streetclip.accounts import Accounts
from streetclip.auth import (
    COOKIE_NAME,
    NoAdminConfigured,
    bootstrap_admin,
    hash_password,
    make_dependencies,
    verify_password,
)
from streetclip.config import Settings


@pytest.fixture
def accounts(tmp_path: Path) -> Accounts:
    return Accounts(tmp_path / "s.db")


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
