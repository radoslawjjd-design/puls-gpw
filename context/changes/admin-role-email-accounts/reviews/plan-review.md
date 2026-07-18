<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Admin Role for Email Accounts (PUL-83)

- **Plan**: context/changes/admin-role-email-accounts/plan.md
- **Mode**: Deep
- **Date**: 2026-07-18
- **Verdict**: SOUND (after triage fixes; pre-fix: REVISE)
- **Findings**: 0 critical, 3 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING (F3 — fixed) |
| Plan Completeness | WARNING (F1, F2 — fixed) |

## Grounding

6/6 paths ✓ · symbols ✓ (schema migration confirmed at startup: src/api.py:290-291) ·
brief↔plan ✓ · Progress↔Phases ✓ · no contract-surfaces.md (skipped)

Deep verification highlights (sub-agent, file:line evidence):
- Refresh demotion trap real (`refresh_session_if_stale` re-issues from old payload,
  src/auth.py:159) — plan already mandates `role=payload.get("role","user")` carry-over. OK.
- `_bootProbeSession` (index.html:1390) overwrites role with 'user' — plan's Phase 2
  contract already routes the `/me` role through it. OK.
- users-table SQL surface = exactly 4 functions in db/bigquery.py; no other SELECT
  readers anywhere. Confirms containment.

## Findings

### F1 — Exact-equality auth response asserts break when "role" is added

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness · **Location**: Phase 1 — Unit tests
- **Detail**: tests/test_auth_api.py:60,134,239 assert register/login/me bodies
  exactly equal {"user_id","email"}; plan didn't name the update.
- **Fix**: Phase 1 test contract now lists updating the three asserts.
- **Decision**: FIXED

### F2 — BQ smoke script asserts the users column list

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness · **Location**: Phase 1 — Manual 1.5
- **Detail**: scripts/test_bq_users.py (~:90) asserts the expected schema — the new
  column breaks the very tool used for the 1.5 round-trip.
- **Fix**: Manual 1.5 now names the script and its expected-columns update.
- **Decision**: FIXED

### F3 — Register mock must issue the same uid as the login mock

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Dimension**: Blind Spots · **Location**: Phase 1 — E2E mock registration
- **Detail**: Plan changed verify_password_rest's uid to "e2e-uid-<email>" but the
  firebase_auth.create_user mock (conftest:439) still returned the constant
  "e2e-firebase-uid" — register→login would split state across two identities.
- **Fix**: Contract now requires both mocks to derive uid from the email kwarg.
- **Decision**: FIXED

## Triage summary

- Fixed: F1, F2, F3 (plan edits applied 2026-07-18)
- Verdict after fixes: SOUND
