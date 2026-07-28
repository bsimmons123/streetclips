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
