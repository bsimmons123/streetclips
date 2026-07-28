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
