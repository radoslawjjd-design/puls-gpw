<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Watchlist Admin View — Phase 1 (Score Column for Admin)

- **Plan**: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
- **Scope**: Phase 1 of 4
- **Date**: 2026-07-21
- **Verdict**: NEEDS ATTENTION → resolved (F1 folded into Phase 4)
- **Findings**: 0 critical, 1 warning, 0 observations
- **Commit reviewed**: e6f86f6

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING → resolved (F1) |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Success Criteria Verification

- 1.1 Lint (ruff on touched Python): **PASS** (`All checks passed!`).
- 1.2 Full suite: **PASS** (637 passed). New E2E: `test_admin_sees_score_column_in_my_wallet` (RED→GREEN), `test_user_does_not_see_score_column_in_my_wallet` (leak-guard).
- Manual 1.3–1.5: pending (informational; E2E covers 1.3/1.4 substantively).

## Findings

### F1 — Stale admin "Score" header leaks to user on same-document relogin

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real cross-role interaction; small fix, needs placement decision
- **Dimension**: Safety & Quality
- **Location**: static/index.html:2303-2305 (build guard) + doLogout 1276-1298
- **Detail**: The my-wallet head is built once in `_buildMyWalletViewContent`, gated by `_myWalletViewBuilt` (set true at 2305, never reset). Phase 1 made that head role-dependent (`_ADMIN_COLS` vs `_USER_COLS`). On an admin→user same-document relogin (no page reload), `_showMyWalletViewDom` skips the rebuild → the user sees the admin "Score" header, and the 8-col head misaligns with the 5-col user body. No score *data* leaks (body emits no score cells; backend strips score for users), but the Score header is visibly exposed — the PUL-82 F1 same-document-relogin class. The announcements table avoids this via `renderHeaders(role)` per fetch; my-wallet builds once. E2E leak-guard passed only because each test runs in a fresh document.
- **Fix A ⭐ Recommended**: Fold into Phase 4 — amend the Phase 4 `doLogout` contract to also reset `_myWalletViewBuilt = false` (head rebuilds for the next role). One line, ships in the same PR.
- **Fix B**: Fix now in Phase 1 (add `_myWalletViewBuilt = false` to doLogout).
- **Decision**: FIXED via Fix A — Phase 4 contract amended (plan.md Phase 4 §1 + manual verification 4.4 added). No code change in the Phase 1 commit; the reset lands when Phase 4 is implemented (same PR, nothing deployed yet).
