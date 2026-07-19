# Password Reset via Firebase E-mail Flow — Plan Brief

> Full plan: `context/changes/password-reset-firebase/plan.md`

## What & Why

Registration is open (PUL-72/74), but a user who forgets their password is locked out
with no self-service path. This adds the standard reset flow: request a link from the
login screen, Firebase e-mails it and hosts the password-change page — no SMTP or
custom handler on our side.

## Starting Point

`src/auth.py` already has the whole toolbox: the `/api/auth` router, per-endpoint
in-memory rate limiters, and the Identity Toolkit REST family
(`verify_password_rest`) with an anti-enumeration error taxonomy. The login panel has
hash-routed auth views (`#/logowanie`/`#/rejestracja`) and shared error/submit-guard
helpers. Since PUL-84 there is a single `index.html` — no file mirroring.

## Desired End State

"Nie pamiętasz hasła?" on the login form → `#/reset-hasla` view → e-mail form →
confirmation ("sprawdź skrzynkę") with resend + back-to-login. Existing account gets a
working reset e-mail whose page links back to the app; unknown e-mail gets the exact
same 204 and UI (no account enumeration); endpoint throttled 5/min per IP.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) |
| --- | --- | --- |
| Rate limit | Own `RateLimiter(5)` | Per-endpoint limiter convention; reset spam must not eat the login budget. |
| UI navigation | Hash `#/reset-hasla` | Consistent with existing auth hash routing; Back and deep-links work. |
| Confirmation UX | Confirmation + resend + back | User can retry a lost e-mail; backend 429 is the throttle (no FE cooldown). |
| continueUrl | Request origin | Reset page links back to the app; #20 has a comment to update Firebase authorized domains after the custom domain lands. |
| EMAIL_NOT_FOUND | Silent 204 | The one deliberate difference from login's taxonomy — enumeration safety. |

## Scope

**In scope:** `POST /api/auth/reset-password` + `send_password_reset_rest()`; reset
view + confirmation state in the login panel; unit tests (both levels) + new e2e file;
conftest mock for the OOB call.

**Out of scope:** custom SMTP/action page; changes to other auth endpoints;
password-change-in-profile; e-mail verification (PUL-86, next change); FE resend
cooldown; Firebase template edits in code (console human-step).

## Architecture / Approach

Copy the proven shapes: new REST helper next to `verify_password_rest` (same httpx +
error mapping, EMAIL_NOT_FOUND swallowed), endpoint in the same router with its own
limiter, UI as a third hash-routed auth view, tests mirroring the login/register
suites.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend | 204-always endpoint + helper + full unit contract | EMAIL_NOT_FOUND must not leak through any error path |
| 2. Frontend + E2E | Reset view, confirmation state, e2e proof | conftest must mock the new src.auth function (live-server lesson) |

**Prerequisites:** none — branch `pul-85-password-reset` open; Firebase project already
serves login/register.
**Estimated effort:** ~1 session, 2 phases.

## Open Risks & Assumptions

- Manual round-trip needs the real Firebase project (prod deploy or local env with
  FIREBASE_WEB_API_KEY) — automated tests never touch the network.
- continueUrl requires the Cloud Run origin in Firebase authorized domains (console
  human-step); custom domain follow-up noted on GitHub #20.

## Success Criteria (Summary)

- Owner account: reset e-mail arrives, new password logs in, continue link returns to
  the app.
- Unknown e-mail: byte-identical 204 + identical UI — no enumeration signal.
- 429 with Retry-After beyond 5 requests/min from one IP.
