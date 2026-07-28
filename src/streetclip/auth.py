"""Passwords, sessions cookies, and the dependencies that guard the API."""

from __future__ import annotations

import logging
from collections.abc import Callable

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Response

from streetclip.accounts import SESSION_TTL, Accounts
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
    # Checked before the "any users exist" guard below: once the configured
    # admin has been created, later calls (e.g. every `docker compose up`)
    # must find it here and return without touching its password.
    if settings.admin_email:
        existing = accounts.get_user_by_email(settings.admin_email)
        if existing is not None:
            return int(existing["id"])

    if accounts.list_users():
        return None

    if not settings.admin_email or not settings.admin_password:
        raise NoAdminConfigured(
            "no accounts exist and no admin is configured. Set "
            "STREETCLIP_ADMIN_EMAIL and STREETCLIP_ADMIN_PASSWORD."
        )

    user_id = accounts.create_user(
        settings.admin_email,
        hash_password(settings.admin_password),
        is_admin=True,
        approved=True,
    )
    log.info("seeded admin account %s", settings.admin_email)
    return user_id


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

    def approved_user(user=Depends(current_user)):  # noqa: B008
        if user["approved_at"] is None:
            raise HTTPException(403, "this account is awaiting approval")
        return user

    def admin_user(user=Depends(current_user)):  # noqa: B008
        if not user["is_admin"]:
            raise HTTPException(403, "admin only")
        return user

    return current_user, approved_user, admin_user
