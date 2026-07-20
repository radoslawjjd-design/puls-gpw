<!-- PLAN-REVIEW-REPORT -->
# Plan Review: E-mail Verification at Registration (PUL-86)

- **Plan**: `context/changes/email-verification-registration/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-20
- **Verdict**: REVISE → **SOUND after triage** (all 6 findings fixed in plan)
- **Findings**: 1 critical, 2 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING (F2, F5 — fixed) |
| Plan Completeness | FAIL (F1 critical + F3, F4, F6 — fixed) |

## Grounding

7/7 paths ✓ (src/auth.py, src/notifier.py, static/index.html, tests/test_auth_api.py,
tests/e2e/conftest.py, tests/e2e/test_password_reset.py, scripts/migrate_owner_identity.py),
symbols ✓, brief↔plan ✓. Deep verification: 1 sub-agent, 6 questions, all answered with
file:line evidence.

Verified non-findings: firebase-admin 7.5.0 supports `list_users`/`update_user` (backfill ✓);
only consumer of register response `role` is `_enterUserSession` (`static/index.html:1572`)
— contract change is safe; `#reset-confirmation` / `_showAuthTab` pattern maps 1:1 onto the
planned clone; `test_register_sixth_request_in_minute_returns_429` survives if register stays
status 200.

## Findings

### F1 — Progress↔Phase mismatch in Phase 4 (breaks /10x-implement parsing)

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4 / `## Progress`
- **Detail**: Phase 4 had 3 Manual Verification bullets but only 2 Progress rows (4.3 merged
  dry-run and `--apply`). The progress-format contract requires 1:1.
- **Fix**: Split into 4.3 (dry-run), 4.4 (`--apply` + re-run 0), 4.5 (post-deploy).
- **Decision**: FIXED

### F2 — `#/logowanie` fragment in continueUrl may not survive Firebase

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details → continueUrl; Phase 1.2
- **Detail**: Firebase validates/rewrites continueUrl; fragments can be dropped on the action
  page redirect. PUL-85 shipped with bare `origin` (prod-proven). Loss of fragment = user
  lands on landing instead of the login form (UX degradation, not breakage).
- **Fix A ⭐ (applied)**: Keep fragment; Phase 1 manual verification explicitly checks the
  Continue button lands on `#/logowanie`; documented fallback to `url=origin`. New Progress
  row 1.5.
  - Confidence: MED — fragment behavior only verifiable on real Firebase.
- **Fix B**: Bare `url=origin` from the start (PUL-85 pattern). Confidence: HIGH; worse UX.
- **Decision**: FIXED (Fix A)

### F3 — E2E audit already done — results belong in the plan

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 3.4
- **Detail**: Exactly ONE register-flow test breaks by design:
  `test_register_lands_in_dashboard_without_relogin` (`tests/e2e/test_landing_auth.py:38`) —
  rewrite as inverted contract (register → confirmation, no dashboard). Critically: all
  per-user E2E authenticate via `e2e_login_email` → fake `verify_password_rest`
  (`tests/e2e/conftest.py:44-63`); the login gate calls `firebase_auth.get_user`, which
  conftest does NOT patch today — the new patch must return
  `SimpleNamespace(uid=..., email_verified=True)` (style of `_fake_firebase_create_user`
  `:66-67`), not a bare MagicMock (truthy attr would pass accidentally). Session-scoped
  patch caveat (`:425-429`) noted.
- **Fix**: Bake the exact test name and the `get_user` patch contract into Phase 3.4.
- **Decision**: FIXED

### F4 — `/me` and `/logout` tests depend on register session — indirect breakage

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 1.3
- **Detail**: `_register` helper (`tests/test_auth_api.py:452-458`) supplies the session for
  `test_me_after_register_returns_identity_from_jwt_only` (`:470`) and
  `test_logout_returns_204_and_clears_cookie` (`:481`).
- **Fix**: Phase 1.3 now names both tests — switch to login-based session.
- **Decision**: FIXED

### F5 — Passwords remain in DOM inputs on the confirmation path

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Blind Spots
- **Location**: Phase 3.1
- **Detail**: `_enterUserSession` (`static/index.html:1501`) does the password-input wipe
  today; the new register path no longer calls it.
- **Fix**: Phase 3.1 now requires clearing `#reg-password`/`#reg-password2` when showing
  `#register-confirmation`.
- **Decision**: FIXED

### F6 — `-k "not e2e"` works but is name-fragile

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 1 criteria / Progress 1.3
- **Detail**: Verified empirically equal to `--ignore=tests/e2e` today (519/615 tests), but
  `-k` filters by name — a future test containing "e2e" would be silently deselected.
- **Fix**: Criterion 1.3 now uses `uv run pytest --ignore=tests/e2e --tb=short`.
- **Decision**: FIXED
