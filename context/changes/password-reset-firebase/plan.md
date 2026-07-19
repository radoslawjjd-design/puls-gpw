# Password Reset via Firebase E-mail Flow Implementation Plan

## Overview

Self-service password reset from the login screen: `POST /api/auth/reset-password` calls
Identity Toolkit `accounts:sendOobCode` (`requestType: PASSWORD_RESET`), always answers
204 for a syntactically valid e-mail (no account enumeration), is rate-limited 5/min per
IP, and passes `continueUrl` derived from the request origin. The login panel gains a
`#/reset-hasla` view (e-mail form → confirmation state with resend + back-to-login).
Firebase sends the e-mail and hosts the reset action page — no custom SMTP or handler.

Tracking: Linear PUL-85, GitHub #153.

## Current State Analysis

- `src/auth.py` owns the `/api/auth` router (`:328`) with per-endpoint in-memory rate
  limiters (`_register_rate_limiter = RateLimiter(5)`, `_login_rate_limiter =
  RateLimiter(10)` — `:330-331`) and the Identity Toolkit REST family:
  `verify_password_rest` (`:287-323`) with error mapping `InvalidCredentialsError` /
  `FirebaseRateLimitedError` / `AuthUnavailableError` and anti-enumeration code list
  `_INVALID_CREDENTIAL_CODES` (`:279-284`). Endpoints are sync `def` on purpose
  (threadpool — blocking Firebase calls never freeze the event loop, comment `:352-353`).
- E-mail validation pattern: `RegisterIn._valid_email` (`:37-45`) —
  `validate_email(..., check_deliverability=False).normalized`.
- Login panel (`static/index.html:996-1038`): auth tabs + `#email-login-form` /
  `#register-form` with `field-error` / `auth-error` divs, helpers `_setFieldError` /
  `_setAuthError` / `_clearAuthFormErrors` / `_renderAuthError`, and the synchronous
  double-submit guard (lessons.md) — `btn.disabled = true` BEFORE the fetch (`:1509-1511`).
- Auth-view routing on the login screen is hash-based: `#/logowanie` / `#/rejestracja`
  via `_applyAuthHash()` (`:1710-1739`), nav/link handlers set `location.hash`;
  `showLogin()` resets panel state and error divs (`:1794-1806`).
- Tests: `tests/test_auth.py` (core logic, 29 tests), `tests/test_auth_api.py`
  (endpoint contracts), e2e conftest patches `src.auth.*` inside `live_server_url`
  (`tests/e2e/conftest.py:485-495`) — per the conftest-mocking lesson, every new
  `src.auth` function the live server can hit must be patched there.
- `static/index.html` is the ONLY dashboard HTML since PUL-84 (faro-v8/index_old
  deleted) — no file mirroring needed.

## Desired End State

- Login screen shows "Nie pamiętasz hasła?"; clicking it opens the reset view
  (`#/reset-hasla`): e-mail field + submit. After submit: confirmation state
  ("Jeśli konto istnieje, wysłaliśmy link…") with an active resend button and a
  back-to-login link.
- Existing account → reset e-mail arrives (Firebase template), the hosted action page
  changes the password, the new password logs in, and the page links back to the app
  (`continueUrl`).
- Unknown e-mail → identical 204 + identical confirmation state (no enumeration).
- Endpoint rate-limited 5/min per IP with `Retry-After` (429 shown as a friendly
  message in the form).

### Key Discoveries:

- `accounts:sendOobCode` is the same REST family as `_IDENTITY_TOOLKIT_URL` (`:276`) —
  same api-key param, same error envelope; `EMAIL_NOT_FOUND` arrives as an error code
  and MUST be swallowed into the 204 path (anti-enumeration), unlike in login where it
  maps to 401.
- `RateLimiter` instances are patched per-name in unit/e2e tests — a new
  `_reset_rate_limiter` follows `_register_rate_limiter`'s wiring exactly.
- `showLogin()` clears `.field-error`/`.auth-error` and re-enables buttons by id list
  (`:1798-1806`) — the reset form's button id must join that list to survive logout
  resets.
- continueUrl: derive from the request (`str(request.base_url)` origin) — no hardcoded
  domain. Human step: current Cloud Run origin must be in Firebase authorized domains;
  note for the future custom domain landed as a comment on GitHub #20.

## What We're NOT Doing

- No custom SMTP, no custom action handler/page — Firebase hosts the reset page.
- No change to login/register/logout/me endpoints or their limiters.
- No password-change-while-logged-in (profile settings) — separate feature.
- No e-mail verification at registration — that is PUL-86, the next change.
- No frontend resend cooldown timer — the backend 429 is the throttle.
- No Firebase template/sender customization in code — human step in the console.

## Implementation Approach

Copy the proven shapes: `send_password_reset_rest()` next to `verify_password_rest()`
(same httpx call pattern, same error taxonomy, EMAIL_NOT_FOUND → silent success),
endpoint in the same router with its own limiter, UI as a third auth view driven by the
existing hash routing. Tests mirror the login/register suites at both levels.

## Phase 1: Backend — endpoint + REST helper + unit tests

### Overview

`POST /api/auth/reset-password` returning 204 always (for valid syntax), with the OOB
REST call, rate limiting, and the full unit-test contract.

### Changes Required:

#### 1. REST helper

**File**: `src/auth.py`

**Intent**: Add `send_password_reset_rest(email, continue_url)` mirroring
`verify_password_rest`'s httpx/error handling against
`https://identitytoolkit.googleapis.com/v1/accounts:sendOobCode`.

**Contract**: POST body `{"requestType": "PASSWORD_RESET", "email": <email>,
"continueUrl": <continue_url>}` with `params={"key": FIREBASE_WEB_API_KEY}`. Returns
None on success. Error mapping: `EMAIL_NOT_FOUND` → return None (anti-enumeration —
the ONE deliberate difference from login's taxonomy, comment required);
`TOO_MANY_ATTEMPTS_TRIED_LATER` → `FirebaseRateLimitedError`; 5xx/malformed/network →
`AuthUnavailableError`.

#### 2. Endpoint + limiter

**File**: `src/auth.py`

**Intent**: `POST /api/auth/reset-password` in the existing router with a new
`_reset_rate_limiter = RateLimiter(5)` + `_reset_rate_dep` (mirror of
`_register_rate_dep`). Body model `ResetPasswordIn` reusing the e-mail validator only
(no password field).

**Contract**: `status_code=204`, returns None. `continue_url` = request origin
(`str(request.base_url).rstrip('/')`). Exception mapping like login: rate-limit dep →
429 (limiter), `FirebaseRateLimitedError` → 429, `AuthUnavailableError` → 503 with
`_AUTH_UNAVAILABLE_DETAIL`; invalid e-mail syntax → 422 (Pydantic). No 404/409 paths —
enumeration-safe by construction.

#### 3. Unit tests

**File**: `tests/test_auth.py`, `tests/test_auth_api.py`

**Intent**: Lock the contract at both levels, following the existing login/register
test patterns (httpx mocked in test_auth.py; endpoint-level patches in
test_auth_api.py).

**Contract**: test_auth.py — helper: success 200→None, EMAIL_NOT_FOUND→None (the
anti-enumeration case), TOO_MANY→FirebaseRateLimitedError, 5xx/network→
AuthUnavailableError, request body carries requestType+continueUrl. test_auth_api.py —
endpoint: 204 for existing e-mail, 204 for unknown e-mail (helper patched to the
EMAIL_NOT_FOUND path), identical bodies for both (no enumeration), 422 invalid syntax,
429 after limiter exhaustion (patch limiter like login tests), 503 on
AuthUnavailableError.

### Success Criteria:

#### Automated Verification:

- Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- Reset contract tests green: 204 known/unknown identical, 422, 429, 503, helper error
  taxonomy incl. EMAIL_NOT_FOUND→success

#### Manual Verification:

- Local (or prod after deploy) round-trip against real Firebase: reset request for the
  owner account delivers an e-mail; unknown e-mail returns 204 with no e-mail sent

**Implementation Note**: After completing this phase and all automated verification
passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: Frontend — #/reset-hasla view + E2E

### Overview

Reset view wired into the auth hash routing, confirmation state with resend, and the
browser-level proof with a faked OOB call.

### Changes Required:

#### 1. Reset view markup + link

**File**: `static/index.html`

**Intent**: Add "Nie pamiętasz hasła?" link in `#email-login-form` (above/near the
submit button) and a `#reset-form` in `#email-auth-panel` (e-mail input +
`field-error` + submit "Wyślij link" + `auth-error` + back-to-login link), plus a
hidden confirmation block ("Jeśli konto istnieje, wysłaliśmy link… sprawdź skrzynkę")
with "Wyślij ponownie" and "Wróć do logowania".

**Contract**: ids: `reset-form`, `reset-email`, `reset-email-error`, `reset-btn`,
`reset-error`, `reset-confirmation`, `reset-resend-btn`. `reset-btn` joins the
re-enable list in `showLogin()`; `reset-email-error`/`reset-error` are `.field-error`/
`.auth-error` so the existing global clears cover them.

#### 2. Hash routing + submit handler

**File**: `static/index.html`

**Intent**: `#/reset-hasla` becomes the third auth view in `_applyAuthHash` (login
tabs hidden, reset form shown); the "Nie pamiętasz hasła?" link sets `location.hash`.
Submit handler mirrors the login form: client-side e-mail validation, synchronous
button disable BEFORE fetch (lessons.md double-submit guard), `POST
/api/auth/reset-password`, on 204 → show confirmation state; on 429 → friendly "Zbyt
wiele prób…" in `reset-error`; on 5xx/network → generic error. Resend re-submits the
same e-mail; back-to-login sets `#/logowanie`.

**Contract**: For ANY 204 (known or unknown e-mail) the UI state is identical.
Leaving the view (hash change / logout) resets it to the form state.

#### 3. E2E conftest mock

**File**: `tests/e2e/conftest.py`

**Intent**: Patch `src.auth.send_password_reset_rest` inside the `live_server_url`
patch stack (conftest-mocking lesson: every `src.auth` function the live server can
hit must be mocked). Fake records calls (for assertions) and returns None; optionally
raises for a rate-limit scenario if needed.

**Contract**: Existing tests stay green; the fake never performs network I/O.

#### 4. E2E tests

**File**: `tests/e2e/test_password_reset.py` (new)

**Intent**: Browser-level proof, `/10x-e2e` hard rules (role/label/text locators, no
`waitForTimeout`, independent tests). Scenarios: (a) happy path — login screen →
"Nie pamiętasz hasła?" → form → submit valid e-mail → confirmation visible with resend
+ back links; (b) unknown e-mail → identical confirmation (anti-enumeration at the UI
level); (c) invalid e-mail syntax → inline field error, no request issued; (d) back to
login restores the login form (hash `#/logowanie`).

**Contract**: Assertions on visible text/roles; deep-link `#/reset-hasla` renders the
reset view directly (hash routing regression).

### Success Criteria:

#### Automated Verification:

- Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- Full e2e suite passes: `uv run pytest tests/e2e -q`
- New reset e2e tests green (happy path, enumeration-identical UI, validation, back)

#### Manual Verification:

- Prod after deploy: full round-trip — request reset for the owner account, receive
  e-mail, set new password on the Firebase page, log in with the new password;
  continue link points back at the app
- Unknown e-mail on prod: same confirmation, no e-mail
- Firebase console: template/sender reviewed; Cloud Run origin present in authorized
  domains (required for continueUrl)

---

## Testing Strategy

### Unit Tests:

- Helper error taxonomy (incl. EMAIL_NOT_FOUND→silent success) + request body shape
- Endpoint: 204×2 identical, 422, 429 (limiter + Firebase), 503

### Integration Tests:

- E2E: reset flow happy path, enumeration-identical UI, client validation, back nav,
  deep-link to `#/reset-hasla`

### Manual Testing Steps:

1. Prod: reset for owner e-mail → mail arrives → new password works; continue link OK
2. Prod: unknown e-mail → identical confirmation, no mail
3. Firebase console: template + authorized domains verified

## Migration Notes

None — no schema or data changes. Human steps: Firebase console template/sender review
and authorized-domain check; future custom domain → update authorized domains +
continueUrl reachability (noted on GitHub #20).

## References

- Change notes: `context/changes/password-reset-firebase/change.md`
- REST family pattern: `src/auth.py:287-323`; limiter wiring: `src/auth.py:330-339`
- Auth-view hash routing: `static/index.html:1710-1739`; login submit pattern: `:1496-1529`
- Conftest-mocking lesson (all src.auth functions patched in live_server_url):
  memory + `tests/e2e/conftest.py:485-495`
- Follow-up note for custom domain: GitHub #20 comment (2026-07-19)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Backend — endpoint + REST helper + unit tests

#### Automated

- [x] 1.1 Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- [x] 1.2 Reset contract tests green (204×2 identical, 422, 429, 503, helper taxonomy)

#### Manual

- [x] 1.3 Real-Firebase round-trip: owner e-mail delivers, unknown e-mail 204 + no mail

### Phase 2: Frontend — #/reset-hasla view + E2E

#### Automated

- [ ] 2.1 Unit suite passes: `uv run pytest tests/ --ignore=tests/e2e -q`
- [ ] 2.2 Full e2e suite passes: `uv run pytest tests/e2e -q`
- [ ] 2.3 New reset e2e tests green (happy, enumeration, validation, back)

#### Manual

- [ ] 2.4 Prod round-trip: e-mail → new password → login; continue link OK; unknown
      e-mail identical
- [ ] 2.5 Firebase console: template/sender + authorized domains reviewed
