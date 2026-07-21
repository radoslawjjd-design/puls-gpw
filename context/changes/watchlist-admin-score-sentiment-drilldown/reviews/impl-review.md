<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Watchlist Admin View — Score Column, Sentiment Period Info & Drill-Down (FULL PLAN)

- **Plan**: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
- **Scope**: Full plan (Phases 1–4 of 4)
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations (both from Phase 3, LOW; F1 fixed, F2 accepted)
- **Commits reviewed**: e6f86f6 (P1), dd4f2bd (P2), 1cb163c (P3), 95f6ab8 (P4), fc4b5d3 (epilogue)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Per-phase roll-up

- **P1 — Role-aware score column** (impl-review-phase-1, APPROVED): my-wallet table renders `_ADMIN_COLS`/`_USER_COLS` by role; user never receives `analysis_score` or `sentiment` (test `test_announcements_my_wallet_user_never_gets_sentiment_or_score`).
- **P2 — Sentiment summary endpoint + period info** (impl-review-phase-2, APPROVED): server-side `summarize_watchlist_sentiment`, admin-gated `/sentiment-summary`, real date-range + days-with-data label; shared `_SENTIMENT_BUCKET_SQL`; `_invalidate_wl_sentiment` on watchlist mutation (documented deviation).
- **P3 — Drill-down popup** (impl-review-phase-3, APPROVED): `list_watchlist_by_sentiment` shares the same bucket constant; admin-gated `/sentiment/{bucket}` with allowlist-validated bucket (422) + bound param; modal reuses `#modal-overlay`/`closeModal`.
- **P4 — doLogout cleanup**: resets `_myWalletViewBuilt`, `_wlData`, `_watchlistTickers`, `wlPage`, empties table body + tickers list; fixes P3-review F1 (hover CSS var).

## Cross-phase integration checks

- **Role-head rebuild (P1 ↔ P4)**: the my-wallet head is built once via `_buildMyWalletViewContent`, gated by `_myWalletViewBuilt` (`static/index.html:2319`). P4's `doLogout` resets that flag (`:1300`), so a same-document admin→user relogin rebuilds the head for the new role — closing the stale-admin-head-over-user-body gap. Verified.
- **Admin-only invariant (P2 ↔ P3, PUL-82 convention)**: both `/announcements/my-wallet/sentiment-summary` and `/announcements/my-wallet/sentiment/{bucket}` depend on `_require_admin`; sentiment/score never reach the user role. Verified in code + tests (403-for-user on both).
- **Count/contents consistency (P2 ↔ P3)**: `summarize_watchlist_sentiment` and `list_watchlist_by_sentiment` embed the identical `_SENTIMENT_BUCKET_SQL` fragment — consistency by construction, locked by `test_list_watchlist_by_sentiment_query_shares_normalization`.
- **Cache invalidation (P2 ↔ P3)**: `_invalidate_wl_sentiment` clears both `wl-sentiment-sum:{uid}` and every `wl-sentiment-list:{uid}:*` on watchlist add/remove.

## Success Criteria Verification

- Full pytest suite: **644 passed** (P3 added 4 tests; P1/P2 added their own).
- ruff: **clean** on all changed source/test files (pre-existing `tests/test_scraper.py` errors are out of scope, untouched by this change).
- Manual verification (P1 1.3–1.5, P2 2.4–2.7, P3 3.4–3.7, P4 4.3–4.4): user-confirmed across sessions.

## Findings (carried from Phase 3)

### F1 — Bucket hover used an undefined CSS var (dark-theme cosmetic)

- **Severity**: 🔭 OBSERVATION · **Impact**: 🏃 LOW
- **Location**: static/index.html — `.wss-item[data-bucket]:hover`
- **Detail**: `--brand-soft` was undefined; dark-theme hover was near-invisible.
- **Decision**: FIXED (95f6ab8) — replaced with theme-neutral `rgba(127,127,127,.14)`.

### F2 — Endpoint returns a plain dict, not a Pydantic response model

- **Severity**: 🔭 OBSERVATION · **Impact**: 🏃 LOW
- **Location**: src/api.py — `announcements_my_wallet_sentiment_list` (and the P2 summary endpoint)
- **Detail**: Returns `{items, truncated}` / summary dict raw. Admin-only + server-computed, nothing to strip; consistent choice across both PUL-87 endpoints.
- **Decision**: ACCEPTED — established pattern for these admin-only endpoints.
