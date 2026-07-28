"""Routes for signing in and, for the admin, managing who may sign in."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field

from streetclip.accounts import Accounts
from streetclip.auth import (
    COOKIE_NAME,
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
    def change_password(
        change: PasswordChange,
        user=Depends(current_user),
        session: str | None = Cookie(default=None, alias=COOKIE_NAME),
    ) -> Response:
        if not verify_password(user["password_hash"], change.current):
            raise HTTPException(403, "current password is incorrect")

        with accounts.connect() as conn:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (hash_password(change.new), user["id"]),
            )
        # Everything else signed in as this user is now suspect — but not the
        # session that just proved it knows the new password.
        accounts.delete_user_sessions(user["id"], keep=session)
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
