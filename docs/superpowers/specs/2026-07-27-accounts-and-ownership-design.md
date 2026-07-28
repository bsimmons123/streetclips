# Accounts, ownership, and admin approval

Date: 2026-07-27

## Problem

streetclip has no authentication. Every workspace is visible to anyone who can
reach the port, which is why the README says to keep it on a LAN. To let other
street preachers use it, the app needs accounts, per-user ownership of
workspaces, and a gate so an unapproved account cannot spend the machine's disk,
CPU, or API budget.

## Scope

This spec covers **accounts, sessions, ownership, and the approval gate**. It is
the first of five subsystems identified during design:

| # | Subsystem | Status |
|---|---|---|
| 1 | Accounts + sessions | **this spec** |
| 2 | Workspace ownership | **this spec** |
| 3 | Per-user API keys, encrypted at rest | later |
| 4 | Rate limits, upload quotas, TLS | later — explicitly deferred |
| 5 | Email verification and password reset | later — needs an email provider |

1 and 2 are specced together because both migrate the `jobs` table, and doing
them separately would migrate it twice.

**Explicitly out of scope:** rate limiting, upload quotas, TLS termination,
password reset, email of any kind, per-user API keys.

> **2026-07-28 addendum:** Upload quotas and per-user provider keys were brought
> into scope after implementation. Approved non-admins must configure encrypted
> personal Groq and Anthropic keys and have a 15 GiB stored-upload quota. An
> admin may grant a separate unlimited-storage override. Admin jobs alone use
> the server environment keys.

## What this does and does not make safe

This makes the app **multi-user**. It does not make it safe to expose publicly.
An unapproved account costs nothing, but an *approved* one can still upload
without limit and saturate the CPU with encodes. Keep the app on a LAN or
Tailscale until subsystem 4 lands.

## Schema

Two new tables and one new column, all added by the same idempotent `ALTER`
pattern already proven against the live database.

```sql
CREATE TABLE users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    approved_at   REAL,
    approved_by   INTEGER REFERENCES users(id),
    disabled_at   REAL,
    created_at    REAL NOT NULL
);

CREATE TABLE sessions (
    id           TEXT PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at   REAL NOT NULL,
    expires_at   REAL NOT NULL,
    last_seen_at REAL NOT NULL
);

ALTER TABLE jobs ADD COLUMN user_id INTEGER REFERENCES users(id);
```

`email` is `COLLATE NOCASE` so `Ben@x.com` and `ben@x.com` are one account.

### Why a sessions table rather than a signed cookie

A signed cookie cannot be invalidated. Revocation is required here: logging out
everywhere, killing sessions when a password changes, and cutting off a disabled
account immediately. That is one table and a `DELETE`.

### Account states

Two nullable timestamps rather than a status string, so "who approved this, and
when" is answerable.

| State | Condition | Can log in | Can consume resources |
|---|---|---|---|
| pending | `approved_at IS NULL` | yes | no |
| approved | `approved_at` set | yes | yes |
| disabled | `disabled_at` set | no | no |

Admin-created accounts are approved at creation — the admin has already
vouched. Pending is what self-registration produces once signup opens, so the
gate is built and exercised now and opening signup later is a config flag
rather than new security code.

### Migration

`jobs.user_id` is backfilled to the admin's id for every existing row. The live
database has four analyze jobs and two render jobs that predate accounts; they
become the admin's.

## Password and session handling

- **argon2id** via `argon2-cffi` at library defaults. Not bcrypt, which
  truncates at 72 bytes; never a bare hash function.
- Session id is `secrets.token_urlsafe(32)`, stored as issued. It is a bearer
  token: 30-day expiry, reissued on login, `last_seen_at` refreshed on use.
- Cookie is `HttpOnly` and `SameSite=Lax`. `Secure` is set **only when
  `STREETCLIP_HTTPS=true`** — setting it unconditionally means the browser
  silently never sends the cookie over plain HTTP and login appears to do
  nothing at all.
- `SameSite=Lax` is sufficient CSRF protection here: the SPA is same-origin with
  the API, and Lax blocks cross-site POSTs.
- A failed login is constant-time and reports "email or password is incorrect"
  without distinguishing which was wrong.

## Authorization

One rule, applied everywhere:

> You may act on a workspace when `workspace.user_id == session.user_id`.

The admin is a normal user with extra powers. Admin can create, approve, revoke,
and delete accounts; admin cannot read anyone else's workspaces.

**A workspace owned by someone else returns 404, not 403.** A 403 confirms that
a workspace exists at that id, which leaks that another user has one.

**An unapproved account returns 403, not 404**, on the resource routes. That is
the user's own account and the honest answer is "awaiting approval"; a 404 would
just look broken.

### Two dependencies

`current_user` proves identity. `approved_user` builds on it and proves the
account may spend the machine's resources. Only three routes take the second:

```
POST /api/workspaces               analysis: disk, CPU, and API spend
POST /api/workspaces/upload        disk
POST /api/workspaces/{id}/render   CPU
```

Every other `/api/*` route takes `current_user`. A pending user can log in,
change their password, and sign out, and has no workspaces to read because they
were never able to create one.

## Per-user upload directories

Uploads currently land in `data/uploads/<filename>`. With more than one user,
two people uploading `session.mp4` overwrite each other. Uploads move to
`data/uploads/<user_id>/<filename>`.

The existing shared-source guard in workspace deletion is unaffected — it
compares full paths, which are now distinct per user.

## Routes

```
POST   /api/session          log in    → sets cookie
DELETE /api/session          log out   → clears cookie and deletes the row
GET    /api/session          current user, or 401
POST   /api/session/password change own password; revokes all other sessions

GET    /api/users            admin: all accounts, pending first
POST   /api/users            admin: create an account (approved on creation)
POST   /api/users/{id}/approve   admin: set approved_at and approved_by
POST   /api/users/{id}/revoke    admin: clear approved_at; workspaces survive
POST   /api/users/{id}/disable   admin: set disabled_at and kill live sessions
DELETE /api/users/{id}       admin: delete account, sessions, and workspaces

POST   /api/signup           404 unless STREETCLIP_OPEN_SIGNUP=true
```

Revoking is deliberately not deletion: an account that abuses the machine can be
switched off without destroying the clips it already made.

## Admin bootstrap

The admin is seeded at startup from `STREETCLIP_ADMIN_EMAIL` and
`STREETCLIP_ADMIN_PASSWORD`. If that email already exists, nothing happens —
the password is not reset from the environment on every boot.

If no users exist and no admin is configured, the app **refuses to serve the
API** and logs a loud error. Starting wide open because configuration is missing
is the failure mode worth designing out.

## The worker

Unchanged. It drains the queue regardless of who owns a job, and API keys still
come from the environment. Per-user keys are subsystem 3.

## UI

- A login screen renders before the shell when there is no session.
- A **pending** user sees a waiting screen instead of the workspace home: no
  upload control and no "+ New recording". Offering a button that is guaranteed
  to 403 is worse than not offering it.
- The topbar gains the signed-in email and a Sign out control.
- Admin gets a Users panel listing accounts with Approve, Revoke, and Disable.
- No signup form is rendered while `STREETCLIP_OPEN_SIGNUP` is false.

## Testing

| Area | Tests |
|---|---|
| Route coverage | Walk the app's route table: every `/api/*` route except `/api/session` and `/api/signup` requires `current_user`; the three resource routes additionally require `approved_user`. This is what stops the next endpoint from shipping unprotected. |
| Isolation | User B receives **404** — not 403 — on user A's workspace detail, transcript, poster, source, clip patch, delete, and render |
| Approval | A pending user gets 403 on all three resource routes and 200 on the read routes; approving flips exactly those three; revoking flips them back and leaves existing workspaces intact |
| Sessions | Expired session rejected; logout deletes the row; changing a password revokes other sessions; a disabled user cannot log in and their live sessions die |
| Passwords | argon2 verifies a correct password and rejects a wrong one; a hash never appears in any response body |
| Bootstrap | Admin seeded from env; re-seeding an existing email is a no-op and does not reset the password; no admin and no users refuses to serve |
| Migration | `users` and `sessions` created on an existing database; `jobs.user_id` backfilled to admin; running twice does not raise |
| Uploads | Two users uploading the same filename get separate files |

The route-table test is the load-bearing one. Every other test checks a rule
that exists today; that one checks rules that do not exist yet, on endpoints
nobody has written.
