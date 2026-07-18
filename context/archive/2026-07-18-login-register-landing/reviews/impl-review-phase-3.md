<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Onboarding Landing + Login/Register (PUL-72) — Phase 3

- **Plan**: context/changes/login-register-landing/plan.md
- **Scope**: Phase 3 of 3 (commit 27f2e9a — auth wiring + boot probe + e2e)
- **Date**: 2026-07-18
- **Verdict**: APPROVED
- **Findings**: 0 critical, 2 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (4/4 planned items MATCH, no MISSING; validation mirror faithful to src/auth.py:33-56) |
| Scope Discipline | PASS (5 extras, all in service: conftest wrong-password mock, showLogin form resets, boot-loader, repeat-password check, error helpers) |
| Safety & Quality | WARNING (F1/F2 — both LOW impact, fixed or plan-sanctioned) |
| Architecture | PASS (XSS clean — all error rendering via textContent; HttpOnly cookie stays authoritative, hasSession is a pure UX hint; CSRF posture unchanged) |
| Pattern Consistency | PASS (handler style mirrors API-key login; e2e matches sibling conventions) |
| Success Criteria | PASS (485 unit + 82 e2e green; re-verified after fixes) |

## Findings

### F1 — Fire-and-forget logout: failed request leaves a valid cookie

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: static/index.html (doLogout)
- **Detail**: POST /api/auth/logout is fire-and-forget (keepalive). If it fails,
  the HttpOnly session cookie stays valid up to 7 days while the UI reads
  "logged out" — JS cannot delete an HttpOnly cookie. Relevant on shared
  machines. The plan explicitly sanctioned this ("fire-and-forget with
  keepalive is fine"); awaiting the response would improve the odds, not the
  guarantee.
- **Decision**: SKIPPED (plan-sanctioned trade-off)

### F2 — Boot probe removed hasSession on ANY failure (incl. network/5xx)

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: static/index.html (_bootProbeSession)
- **Detail**: A transient server/network blip during reload permanently
  removed the flag, forcing manual re-login despite a still-valid cookie.
  The plan's "anything else → remove" wording aimed at loader-loop
  prevention, which is not at risk (every path ends on showLogin()).
- **Fix**: remove the flag only on 401/403; network error/5xx keeps the flag
  (next reload retries the probe — one cheap request).
- **Decision**: FIXED

### F3 — Boot-probe success path kept the stale #/logowanie hash

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Detail**: F3-from-phase-2 fix lived only in _enterUserSession; reloading
  on #/logowanie with a valid session reached the dashboard with the auth
  hash still in the URL.
- **Fix**: history.replaceState before showDashboard in the probe success path.
- **Decision**: FIXED

### F4 — Password lingered in the hidden input for the whole dashboard session

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Detail**: cleared only on the next showLogin(); DOM hygiene on shared machines.
- **Fix**: clear login-password/reg-password/reg-password2 in _enterUserSession.
- **Decision**: FIXED

## Triage summary

- Fixed: F2 (401/403-only flag removal), F3 (probe hash clear), F4 (password wipe on success)
- Skipped: F1 (plan-sanctioned fire-and-forget logout)
- Post-fix verification: 82 e2e passed; faro-v8.html re-synced byte-identical

## Notes

- No-action notes: JS `/\d/` stricter than Python `isdigit()` (client-stricter,
  safe); UTF-16 vs code-point length at 8/128 bounds (astral-only edge, English
  pydantic fallback message); e2e CSS-container scoping matches repo convention
  (204 occurrences); `_setFieldError`/`_setAuthError` are duplicate identical
  helpers (cosmetic).
- conftest verify_password_rest side_effect verified non-breaking: all other
  e2e files authenticate via API key only; the mock now echoes the submitted
  email (improvement over the fixed "e2e@example.com").
