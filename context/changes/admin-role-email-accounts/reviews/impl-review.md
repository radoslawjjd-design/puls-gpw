<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Admin Role for Email Accounts (PUL-83) — Full

- **Plan**: context/changes/admin-role-email-accounts/plan.md
- **Scope**: Full — Phase 1 (c076e65) + Phase 2 (d426e19)
- **Date**: 2026-07-18
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (all items MATCH; _get_role mapping tests live in test_auth_api.py instead of test_api.py — equivalent-or-better coverage through the full app) |
| Scope Discipline | PASS (extras in-service: /me normalization hardening, call-contract assert, helper email param) |
| Safety & Quality | PASS (privilege-escalation surface clean: role only from BQ→signed HS256 claim; no role in request models; sessionStorage never sent as authority; alg pinned) |
| Architecture | PASS (gates untouched; JWT admin gets exactly the API-key-admin serialization; _get_client_id prefers JWT user_id so watchlist/portfolio identity works) |
| Pattern Consistency | PASS |
| Success Criteria | PASS (493 unit + 84 e2e; post-fix re-run of landing-auth file 10/10) |

## Findings

### F1 — Admin revocation propagates only at re-login (up to 30 days)

- **Severity**: ⚠️ WARNING · **Impact**: 🏃 LOW
- **Location**: src/auth.py (refresh path) + get_user_role ("login only")
- **Detail**: The claim rides the sliding refresh without a BQ re-read — a BQ
  demotion has no effect on live tokens until the 30-day absolute cap. This is
  the explicit Q1 planning decision; the only immediate kill-switch is rotating
  JWT_SECRET (logs out every session).
- **Fix**: kill-switch note added to the plan's Migration Notes.
- **Decision**: FIXED (documented; architecture intentionally unchanged)

### F2 — User-regression e2e claimed a sentiment assert it didn't have

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Location**: tests/e2e/test_landing_auth.py (test_user_email_login_sees_no_admin_surface)
- **Detail**: docstring promised "no sentiment text"; body only checked
  Score header + admin-table class.
- **Fix**: added `#table-body [data-score]` count-0 assert (the actual leak
  channel — admin rows carry data-score/data-sc) and corrected the docstring.
- **Decision**: FIXED

### F3 — JWT sessions have no URL-state persistence (pre-existing PUL-72)

- **Severity**: 💡 OBSERVATION · **Impact**: 🏃 LOW
- **Detail**: _writeUrl/popstate gate on apiKey; deep links and back/forward
  don't work for cookie sessions (incl. new JWT admins). Functional, not
  security — data stays server-gated.
- **Decision**: TICKETED — Linear PUL-84 / GitHub #148

## Notes (no action)

- get_user_role uses LIMIT 1 without ORDER BY — duplicates impossible in
  current write paths (Firebase uid uniqueness + insert/MERGE).
- Manually editing sessionStorage.role yields cosmetic-only self-tampering
  (server gates hold) — pre-existing.
- BQ blip at login degrades an admin to a 7-day "user" token — safe direction,
  logged (availability over freshness, as planned).
- Register with an email whose old uid had admin yields a fresh Firebase uid →
  "user" (role keyed by user_id, not email) — verified clean.

## Triage summary

- Fixed: F1 (kill-switch note), F2 (data-score assert)
- Ticketed: F3 → PUL-84 / GH #148
- Post-fix verification: tests/e2e/test_landing_auth.py 10/10 green
