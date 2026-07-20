<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: E-mail Verification at Registration (PUL-86)

- **Plan**: `context/changes/email-verification-registration/plan.md`
- **Scope**: Phase 1 of 4 (commit 3f37f33)
- **Date**: 2026-07-20
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING (F1 — fixed in triage) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Drift agent: 3/3 planned changes MATCH, no out-of-scope leakage (login/reset untouched, no
SPA/backfill files), extras benign (mirror escape test, mechanical patch additions).
Safety agent: escaping equivalent to `_password_reset_html`, no new enumeration/timing
signal, background task crash-safe. Success criteria: 40/40 auth tests, lint clean on
touched files, 523/523 non-e2e; manual 1.4/1.5 confirmed by the user on real Firebase
(branded mail delivered, link verifies, Continue lands on `#/logowanie`).

## Findings

### F1 — E2E conftest mock gap: bare `uv run pytest` can fire a REAL SMTP alert

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: tests/e2e/conftest.py:488-506
- **Detail**: Conftest did not patch `generate_email_verification_link` /
  `send_verification_email`. An e2e register submission triggered the real background task:
  link-gen fails against the mocked Firebase app → `send_alert()` runs with real SMTP env
  vars if set — spurious owner alert per run. First e2e path where an unmocked `send_alert`
  could actually fire; CI runs full pytest on every PR. Plan scheduled these mocks for
  Phase 3 — pulled forward.
- **Fix**: Added the two patches to the conftest live-server stack (fake verifyEmail
  oobCode link + no-op mailer), matching the recorded conftest lesson.
- **Decision**: FIXED

### F2 — Comment/mail copy describe the Phase 2 gate that doesn't exist yet

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Adherence
- **Location**: src/auth.py:429-430 + verification mail body
- **Detail**: "konto jest nieaktywne dopóki nie potwierdzisz" is true only after Phase 2's
  login gate. Acceptable because phases 1-3 merge as one PR (PUL-85 precedent).
- **Decision**: SKIPPED (accepted — whole-change merge)

### F3 — Docstring copied from the reset sibling is inaccurate

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: src/notifier.py — `send_verification_email` docstring
- **Detail**: "Never called for unknown accounts" doesn't apply to register (the account
  was just created).
- **Fix**: Docstring corrected ("the resend endpoint is the recovery path").
- **Decision**: FIXED

## Triage summary

- Fixed: F1, F3
- Skipped (accepted): F2
- Post-fix verification: lint clean, 523/523 non-e2e green.
