# E-mail Verification at Registration — Implementation Plan

## Overview

Gate login on Firebase's `emailVerified` flag (variant A from Linear PUL-86). Registration
still creates the account immediately, but no longer issues a session — instead it sends a
branded verification e-mail (same Faro/SMTP pattern as PUL-85 password reset). Login rejects
unverified accounts with 403; a new enumeration-safe `POST /api/auth/resend-verification`
endpoint lets users re-request the link. Existing prod accounts (all `emailVerified=false`,
including the owner) get a one-time backfill to `emailVerified=True` **before** the gate
deploys.

## Current State Analysis

- `register` (`src/auth.py:397-419`): creates the Firebase user, inserts the BQ row, then
  **immediately issues a session** via `_session_response` (`src/auth.py:389-394`). No
  verification anywhere.
- `login` (`src/auth.py:422-448`): `verify_password_rest` → BQ upsert → role → session.
  Never reads `emailVerified`.
- PUL-85 delivered the full reuse surface:
  - `_request_origin` (`src/auth.py:373-386`) — origin from `X-Forwarded-Proto`+`Host`,
    strict-shape validated, → 503 on crafted Host.
  - Enumeration-safe reset flow (`src/auth.py:475-503`): explicit
    `get_user_by_email` pre-check (correct `UserNotFoundError` mapping), always-204,
    link-gen + SMTP moved to `BackgroundTasks` (impl-review F1 — no post-check work on the
    response path; background failure → `logger.error` + `send_alert`, silent for requester).
  - Branded mail: `send_password_reset_email` + `_password_reset_html`
    (`src/notifier.py:183-239`) — Faro header `#14304A`, gold CTA `#b8964f`, PL copy,
    `html.escape(..., quote=True)` on link and logo URL (AI-sec finding PR #159).
  - Rate limiting: `RateLimiter` per-IP sliding window (`src/auth.py:210-243`),
    `_reset_rate_limiter = RateLimiter(5)` + dep (`src/auth.py:351,362-363`).
- SPA (`static/index.html`, single file): register handler (`:1547-1580`) calls
  `_enterUserSession` on `r.ok` (auto-login today); `_submitPasswordReset` +
  `#reset-confirmation` + `_lastResetEmail` (`:1767-1811`) is the exact resend pattern to
  clone; `_renderAuthError` (`:1464-1483`) maps 422 field errors / Polish `detail` strings;
  hash router `_applyAuthHash` (`:1744-1760`) + `_showAuthTab` (`:1731-1740`).
- Tests: unit reset suite `tests/test_auth_api.py:285-444` (patches at `src.auth.*`,
  autouse limiter-reset fixture `:29-39`); E2E conftest patches every `src.auth` function
  the live server can hit (`tests/e2e/conftest.py:485-506` — documented lesson);
  `tests/e2e/test_password_reset.py` is the E2E template.
- Prod constraint: **all existing accounts have `emailVerified=false`** (owner included —
  seen during PUL-85 diagnostics). An unconditional gate locks everyone out.

## Desired End State

- A fresh registration cannot log in until the e-mail link is clicked; the SPA shows a
  "check your inbox" confirmation with a resend button after registering.
- Unverified login attempt → 403 + "Potwierdź e-mail" message + resend button in the SPA.
- `POST /api/auth/resend-verification`: rate-limited 5/min/IP, silent 204 for unknown AND
  already-verified accounts (no enumeration signal, no redundant mail).
- Existing prod accounts keep working (backfilled to verified before deploy); owner login
  verified manually post-deploy.
- `uv run pytest --tb=short` green (unit + Playwright E2E).

### Key Discoveries:

- `generate_password_reset_link` for a nonexistent e-mail does NOT raise
  `UserNotFoundError` — generic `UnexpectedResponseError` instead (`src/auth.py:488-493`
  comment; caught on prod as an enumeration signal). Assume the same for
  `generate_email_verification_link`; the `get_user_by_email` pre-check pattern makes the
  behavior irrelevant, but verify on real Firebase during manual testing
  ([[project-firebase-auth-gotchas]]).
- impl-review F1 (PUL-85): any post-existence-check work on the request path is a timing /
  failure-mode account oracle — link-gen + SMTP must run in `BackgroundTasks` after the
  response goes out.
- `_send` in `src/notifier.py:133-153`: `from_name` sets display name only; Gmail SMTP
  rewrites the From address (custom domain is #20, out of scope here).
- SPA double-submit guard must be synchronous (`btn.disabled = true` before `fetch`) —
  repo lesson, applied in all three existing handlers.
- `scripts/migrate_owner_identity.py` is the precedent for one-off prod scripts.

## What We're NOT Doing

- Variant B (pending-registrations table + custom token flow) — rejected in the ticket.
- Changing the 409-on-existing-email semantics of `/register` (existing, accepted
  enumeration property; the SPA only adds a resend hint next to it).
- Custom sending domain / From-address change (issue #20).
- Firebase-hosted e-mail templates — the mail is ours; Firebase only hosts the action page.
- An "already verified" informational e-mail (resend is a silent no-op for verified accounts).
- Any change to session/JWT mechanics, roles, or the reset-password flow.

## Implementation Approach

Clone the PUL-85 pattern end to end: new branded mail template in `notifier.py`, background
send after pre-checks in `auth.py`, silent-204 resend endpoint, SPA confirmation block
mirroring `#reset-confirmation`, unit tests mirroring the reset suite, E2E with faked
Admin-SDK calls in conftest. The login gate is one Admin SDK `get_user(uid)` call after
password verification. The backfill is a one-off dry-run-first script the human runs on prod
**before** merging the gate to master (deploy = merge → CI).

## Critical Implementation Details

**Timing & lifecycle — backfill before deploy.** The gate must not reach prod before every
existing account is `emailVerified=True`. Order: land the PR branch → human runs
`scripts/backfill_email_verified.py --apply` against prod → merge to master (CI deploys).
The script is idempotent; accounts self-registered in the minutes between backfill and
deploy would be auto-trusted — accepted at current traffic (re-run if paranoid).

**State sequencing — gate before side effects.** In `login`, the `email_verified` check runs
after `verify_password_rest` but BEFORE `upsert_user_login` / `get_user_role` — a blocked
login must not record a login or touch BQ.

**Enumeration invariants (F1).** Both `register`'s mail send and `resend-verification` return
before any link-gen/SMTP work: pre-checks on the request path, mail in `BackgroundTasks`,
background failure = `logger.error` + `send_alert`, silent for the requester. The resend
endpoint returns the identical empty 204 for unknown, unverified, and already-verified
e-mails; the verified check is in-memory on the already-fetched user record (no extra
timing signal).

**403 is the distinct signal.** `login` has no other 403 path, so the SPA keys the
"Potwierdź e-mail" state on `r.status === 403` — no structured error-code envelope needed
(`detail` stays a Polish string like every other auth error).

**continueUrl.** `ActionCodeSettings(url=f"{origin}/#/logowanie")` — after clicking the
verification link the Firebase action page's "Continue" lands the user on the login form.
`origin` is already `_ORIGIN_RE`-validated; the appended fragment is a literal.
UNVERIFIED ASSUMPTION: Firebase validates/rewrites continueUrl and may drop URL fragments
(PUL-85 shipped with bare `origin`). Phase 1 manual verification must explicitly confirm
the Continue button lands on `#/logowanie`; if the fragment is stripped, fall back to
`url=origin` (proven on prod) — degraded-but-fine UX, user clicks "Zaloguj" from landing.

---

## Phase 1: Backend — register stops issuing a session, sends verification mail

### Overview

Registration creates the account, fires the branded verification e-mail in the background,
and returns a no-session "verification pending" response.

### Changes Required:

#### 1. Verification mail template + sender

**File**: `src/notifier.py`

**Intent**: Clone the password-reset mail pair as `_verification_html(verify_link, origin)`
and `send_verification_email(to_email, verify_link, origin)` — same Faro branding and
structure, verification copy (subject `"Faro — potwierdź adres e-mail"`, CTA
`"Potwierdź e-mail"`, PL body explaining the account is inactive until confirmed).

**Contract**: `send_verification_email(to_email: str, verify_link: str, origin: str) -> None`,
raises on SMTP failure (caller runs in background). Both `verify_link` and the logo URL go
through `html.escape(..., quote=True)` exactly like `_password_reset_html:187-188`.

#### 2. Register endpoint — no session, background mail

**File**: `src/auth.py`

**Intent**: `register` gains `request: Request` + `background_tasks: BackgroundTasks`;
computes `_request_origin(request)` up front (crafted Host → 503 before any account is
created); after `create_user` + `insert_user` it schedules
`_send_verification_email_background(email, origin)` and returns
`{"email": body.email, "verification_required": True}` — **no `_session_response`, no
cookie**. `EmailAlreadyExistsError` → 409 unchanged.

**Contract**: New module function `_send_verification_email_background(email, origin)`
mirroring `_send_reset_email_background` (`src/auth.py:451-472`):
`generate_email_verification_link(email, action_code_settings=ActionCodeSettings(url=f"{origin}/#/logowanie"))`
→ `send_verification_email(...)`; any failure → `logger.error` + `send_alert`, never
surfaces to the requester. Response contract consumed by Phase 3: 200 JSON
`{"email": ..., "verification_required": true}`, no `Set-Cookie`.

#### 3. Unit tests — register

**File**: `tests/test_auth_api.py`

**Intent**: Update existing register tests (they assert the session cookie today:
`test_register_happy_path_sets_cookie_and_inserts_user` `:55`,
`test_register_bq_failure_is_logged_not_blocking` `:98`). The `_register` helper (`:452-458`)
currently supplies the session for `test_me_after_register_returns_identity_from_jwt_only`
(`:470`) and `test_logout_returns_204_and_clears_cookie` (`:481`) — switch both to a
login-based session. Then add:
successful register → 200, no `session` cookie, background task generated link with
`url == "http://testserver/#/logowanie"` and called `send_verification_email`; link-gen or
SMTP failure → still 200 + `send_alert` called; crafted Host → 503 and `create_user` NOT
called; 409 path unchanged.

**Contract**: Patch at `src.auth.*` like the reset suite (`tests/test_auth_api.py:285-444`).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_auth_api.py --tb=short`
- Lint passes: `uv run ruff check .`
- Full suite still green (E2E register flows updated later in Phase 3 may be temporarily
  red — acceptable only if Phase 3 lands in the same PR; run
  `uv run pytest --ignore=tests/e2e --tb=short` to scope if needed)

#### Manual Verification:

- On real Firebase (dev creds): register a throwaway address → branded Faro mail arrives,
  link verifies the account; confirm `generate_email_verification_link` exception behavior
  for edge cases matches assumptions (memory: verify SDK exceptions on real Firebase, not mocks)
- Confirm the action page's "Continue" lands on `#/logowanie` (fragment survives) — if
  stripped, switch `ActionCodeSettings` to bare `url=origin` (PUL-85 fallback)

---

## Phase 2: Backend — login gate + resend-verification endpoint

### Overview

Login rejects unverified accounts with 403; a new enumeration-safe, rate-limited resend
endpoint mirrors the reset-password flow.

### Changes Required:

#### 1. Login gate

**File**: `src/auth.py`

**Intent**: After `verify_password_rest` succeeds, fetch the user record via Admin SDK
`firebase_auth.get_user(user_id)` and, when `email_verified` is false, raise 403 with PL
detail (e.g. `"Potwierdź adres e-mail, aby się zalogować"`). The check runs BEFORE
`upsert_user_login`/`get_user_role`. `get_user` failures map like other Firebase errors:
`AuthUnavailableError`/unexpected → 503 (`_AUTH_UNAVAILABLE_DETAIL`).

**Contract**: 403 status is the distinct "unverified" signal for the SPA (login has no other
403). Ticket wording said `accounts:lookup`; Admin SDK `get_user(uid)` is the same lookup
with known exception mapping and no new REST plumbing — equivalent semantics, one extra
RPC per login either way.

#### 2. Resend endpoint

**File**: `src/auth.py`

**Intent**: `POST /api/auth/resend-verification`, status 204, body = e-mail only (same
validator as `ResetPasswordIn`), new `_resend_verification_rate_limiter = RateLimiter(5)` +
dep. Flow mirrors `reset_password` (`src/auth.py:475-503`): `_request_origin` →
`_get_firebase_app` → `user = get_user_by_email(email)`; `UserNotFoundError` → silent 204;
`user.email_verified` already true → silent 204 (no task); otherwise schedule
`_send_verification_email_background(email, origin)`. `AuthUnavailableError`/unexpected → 503.

**Contract**: Identical empty 204 for unknown / unverified / verified e-mails. Request model
may subclass or duplicate `ResetPasswordIn` — implementer's choice.

#### 3. Unit tests — gate + resend

**File**: `tests/test_auth_api.py`

**Intent**: Login: unverified → 403 and `upsert_user_login` NOT called; verified → session
as today; `get_user` blowing up → 503. Resend: unverified existing → 204 + background
link+mail; unknown → 204 + nothing generated/sent; already-verified → 204 + nothing sent;
invalid syntax → 422; crafted Host → 503; SMTP/link failure → 204 + `send_alert`; 6th
request in a minute → 429. Add an autouse limiter-reset fixture for the new limiter
(pattern: `tests/test_auth_api.py:29-39`).

### Success Criteria:

#### Automated Verification:

- Unit tests pass: `uv run pytest tests/test_auth_api.py --tb=short`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- Real Firebase (dev): unverified account → login 403; click link → login succeeds;
  resend for a verified account sends nothing

---

## Phase 3: SPA states + E2E

### Overview

Post-register confirmation screen, unverified-login state with resend, 409 resend hint;
E2E coverage with faked Admin SDK calls.

### Changes Required:

#### 1. Post-register confirmation + shared resend helper

**File**: `static/index.html`

**Intent**: New `#register-confirmation` block cloning `#reset-confirmation`
(`static/index.html:1047-1052`): "sprawdź skrzynkę" copy, resend button, back-to-login
link. Register success handler (`:1547-1580`) stops calling `_enterUserSession`; instead
stores `_lastVerifyEmail`, hides `#register-form`, shows the confirmation. New shared
`_submitResendVerification(btn, email, authErrId)` mirroring `_submitPasswordReset`
(`:1769-1801`) but POSTing `/api/auth/resend-verification` (synchronous
`btn.disabled = true` guard; 204 → confirmation copy; 429 → "Zbyt wiele prób…").
`_showAuthTab` resets the new block hidden like it does `#reset-confirmation`. When showing
the confirmation, clear `#reg-password`/`#reg-password2` — today `_enterUserSession`
(`:1501`) does that wipe, and the new path no longer calls it.

**Contract**: Every resend surface (this block, Phase 3.2, 3.3) calls the one helper.

#### 2. Unverified login state

**File**: `static/index.html`

**Intent**: Login handler (`:1512-1545`): on `r.status === 403` show "Potwierdź swój adres
e-mail" in `#email-login-error` and reveal a (normally hidden) "Wyślij ponownie link
weryfikacyjny" button that calls `_submitResendVerification` with the `#login-email` value.
Hide the button again on the next submit/tab switch.

**Contract**: Keys strictly on status 403; all other statuses keep flowing through
`_renderAuthError`.

#### 3. Register 409 resend hint

**File**: `static/index.html`

**Intent**: On register 409, after `_renderAuthError` renders the Polish detail, additionally
reveal the same resend link/button wired to the `#reg-email` value — exit path for the
"registered but lost the mail" dead end. Backend semantics unchanged.

#### 4. E2E conftest mocks

**File**: `tests/e2e/conftest.py`

**Intent**: Extend the live-server patch stack (`:485-506`) with everything the new code
paths touch: `firebase_auth.get_user` (login gate),
`firebase_auth.generate_email_verification_link` (fake oobCode URL like the reset one),
`send_verification_email` (conftest lesson: mock ALL `src.auth` functions the live server
can hit).

**Contract**: The `get_user` patch must default to verified so ALL existing login-based E2E
(they authenticate via `e2e_login_email` → fake `verify_password_rest`,
`tests/e2e/conftest.py:44-63`) keep passing:
`patch("src.auth.firebase_auth.get_user", side_effect=lambda uid: SimpleNamespace(uid=uid, email_verified=True))`
— explicit `SimpleNamespace` like `_fake_firebase_create_user` (`:66-67`), NOT a bare
MagicMock (truthy `email_verified` would pass accidentally). E2E audit result (plan-review
verified): exactly ONE existing test breaks by design —
`test_register_lands_in_dashboard_without_relogin` (`tests/e2e/test_landing_auth.py:38`) —
rewrite it as the inverted contract: register → confirmation screen, NO dashboard. All
other register-touching E2E either don't submit or fail client-side before the API.

#### 5. E2E tests

**File**: `tests/e2e/test_email_verification.py`

**Intent**: Playwright suite modeled on `tests/e2e/test_password_reset.py`: (a) register →
confirmation screen visible, no dashboard, resend button works; (b) unverified login (flip
the `get_user` mock to `email_verified=False`) → "Potwierdź" message + resend visible;
(c) verified login unaffected; (d) register-409 → resend hint appears. Role/label/text
locators, `e2e_unique_email()`, no `waitForTimeout`, each test standalone.

### Success Criteria:

#### Automated Verification:

- Full suite green: `uv run pytest --tb=short`
- Lint passes: `uv run ruff check .`

#### Manual Verification:

- Browser walkthrough on local dev: register → confirmation screen; login before clicking →
  403 state + resend; click mail link → login works and lands in dashboard
- Resend button UX sane (disabled during flight, error copy on 429)

---

## Phase 4: Backfill script + rollout

### Overview

One-off idempotent script flips `emailVerified=True` for all pre-existing accounts; human
runs it on prod before the merge that deploys the gate.

### Changes Required:

#### 1. Backfill script

**File**: `scripts/backfill_email_verified.py`

**Intent**: Iterate `firebase_auth.list_users()`; for each account with
`email_verified=False`, `update_user(uid, email_verified=True)`. Dry-run by default
(prints affected e-mails + count); `--apply` performs the writes and prints a summary.
Reads `FIREBASE_SERVICE_ACCOUNT_JSON` like the app; follows the
`scripts/migrate_owner_identity.py` one-off precedent.

**Contract**: Idempotent — re-running after `--apply` reports 0 to update.

#### 2. Rollout documentation

**File**: `context/changes/email-verification-registration/change.md`

**Intent**: Record the rollout order in Notes: (1) PR ready + green, (2) human runs
`uv run python scripts/backfill_email_verified.py` (dry-run) then `--apply` on prod,
(3) merge → CI deploys, (4) owner logs in on prod to confirm no lockout.

### Success Criteria:

#### Automated Verification:

- Script passes lint: `uv run ruff check scripts/backfill_email_verified.py`
- Dry-run executes against mocked/list_users-patched unit context or errors cleanly
  without creds: `uv run python scripts/backfill_email_verified.py --help`

#### Manual Verification:

- Human (with prod creds): dry-run lists expected accounts (~all current users, incl. owner)
- `--apply` run; re-run reports 0 remaining
- Post-deploy: owner logs into prod successfully; a brand-new registration cannot log in
  until the link is clicked, then can

---

## Testing Strategy

### Unit Tests:

- Register: no cookie, background mail wiring, origin-503 ordering, silent mail failure +
  alert, 409 unchanged.
- Login: 403 gate before BQ side effects, verified path regression, `get_user` failure → 503.
- Resend: the full enumeration matrix (unknown / unverified / verified → identical 204),
  422, 429, crafted Host 503, background failure + alert.

### Integration Tests:

- E2E (Playwright, mocked Firebase in conftest): register confirmation flow, unverified
  login state + resend, verified login regression, 409 hint.

### Manual Testing Steps:

1. Dev Firebase: real register → real mail → click link → login works (verifies
   `generate_email_verification_link` behavior against real SDK, not mocks).
2. Unverified real account: login 403, resend delivers a fresh working link.
3. Prod after rollout: owner login OK; fresh registration gated until click.

## Performance Considerations

Login gains one Admin SDK `get_user` RPC (~100-300 ms) — same order as the existing
Identity Toolkit call and consistent with the accepted sync-endpoint threadpool model
(PUL-85 F3). Mail work is off the request path by design.

## Migration Notes

No BQ schema changes. Firebase user records: backfill sets `email_verified=True` once
(irreversible but harmless — it only widens access to accounts that already had it).
Rollback of the feature = revert the PR; backfilled flags need no rollback. Firebase console
verification-mail template is already PL (set during PUL-85) — no console work needed.

## References

- Change notes: `context/changes/email-verification-registration/change.md`
- PUL-85 archive (pattern source): `context/archive/2026-07-19-password-reset-firebase/`
  (plan.md phase 3, reviews/impl-review.md F1)
- Backend: `src/auth.py:373-503` (origin, reset flow, limiters), `src/notifier.py:133-239`
- SPA: `static/index.html:1001-1062` (auth markup), `:1438-1811` (auth JS)
- Tests: `tests/test_auth_api.py:285-444`, `tests/e2e/conftest.py:485-506`,
  `tests/e2e/test_password_reset.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backend — register stops issuing a session, sends verification mail

#### Automated

- [x] 1.1 Unit tests pass: `uv run pytest tests/test_auth_api.py --tb=short` — 3f37f33
- [x] 1.2 Lint passes: `uv run ruff check .` — 3f37f33
- [x] 1.3 Full suite green or E2E scoped out pending Phase 3: `uv run pytest --ignore=tests/e2e --tb=short` — 3f37f33

#### Manual

- [x] 1.4 Real-Firebase register → branded mail → link verifies; SDK exception behavior confirmed — 3f37f33
- [x] 1.5 Continue lands on `#/logowanie` (fragment survives) — else fall back to `url=origin` — 3f37f33

### Phase 2: Backend — login gate + resend-verification endpoint

#### Automated

- [x] 2.1 Unit tests pass: `uv run pytest tests/test_auth_api.py --tb=short`
- [x] 2.2 Lint passes: `uv run ruff check .`

#### Manual

- [x] 2.3 Real-Firebase: unverified → 403; after click → login OK; verified resend sends nothing

### Phase 3: SPA states + E2E

#### Automated

- [ ] 3.1 Full suite green: `uv run pytest --tb=short`
- [ ] 3.2 Lint passes: `uv run ruff check .`

#### Manual

- [ ] 3.3 Browser walkthrough: register confirmation, 403 state + resend, post-click login to dashboard
- [ ] 3.4 Resend button UX (in-flight disable, 429 copy)

### Phase 4: Backfill script + rollout

#### Automated

- [ ] 4.1 Script lints: `uv run ruff check scripts/backfill_email_verified.py`
- [ ] 4.2 `--help` / credless dry-run errors cleanly: `uv run python scripts/backfill_email_verified.py --help`

#### Manual

- [ ] 4.3 Human: prod dry-run lists expected accounts (incl. owner)
- [ ] 4.4 Human: `--apply` run; re-run reports 0 remaining
- [ ] 4.5 Post-deploy: owner login OK; new registration gated until link clicked
