<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Sentiment Bar Admin Fix (PUL-82)

- **Plan**: context/changes/sentiment-bar-admin-fix/plan.md
- **Scope**: Full plan (Phase 1-2 of 2)
- **Date**: 2026-07-19
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS (2 extras — both justified and test-covered) |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Review context

Reviewed at HEAD d3a436b (branch pul-82-closeout), i.e. **after** PUL-83 (admin role
from JWT claim) and PUL-74 (JWT-only per-user endpoints) landed on top of this change.
Squash commit: 2f1340d (#142), epilogue 2de8e78 (#143).

Key confirmations:

- All 6 planned items MATCH at HEAD. PUL-74/83 changed the auth plumbing around the
  feature (`_get_user_id` JWT-only, role from signed JWT claim) without breaking the
  admin/user sentiment split.
- User contract is model-enforced, not branch-enforced: `pop("sentiment")` +
  `AnnouncementUser` (`extra="ignore"`, no `analysis_score` field) — no leak path;
  locked by `test_announcements_my_wallet_user_never_gets_sentiment_or_score`.
- Frontend role gate is cosmetic defense-in-depth; the real gate is server-side.
- BQ SELECT change fully parameterized; no injection surface. Read-only — no data-safety
  concerns.
- Two unplanned extras, both justified: (A) `doLogout` stale-bar reset
  (static/index.html:1255) — real bug found in manual testing, pinned by a dedicated
  same-document-relogin e2e test; (B) bar label "Sentyment 7 dni" → "Ostatnie 7 dni" —
  cosmetic, consistent with the e2e assertion.
- Success criteria re-run 2026-07-19: unit `uv run pytest tests/ --ignore=tests/e2e -q`
  → 509 passed; e2e `uv run pytest tests/e2e -q` → 85 passed, 1 skipped
  (test_portfolio_calendar — waiting on PUL-84, unrelated). Manual criteria (BQ
  round-trip, prod verification) recorded as done in plan Progress (2.4 — 2f1340d).

## Findings

### F1 — doLogout does not clear the watchlist table/chips (post-PUL-74 cousin)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: static/index.html:1255
- **Detail**: The reset covers `_watchlistFetched` and the sentiment bar, but not
  `#my-wallet-table-body`, `#wl-tickers-list`, `_wlData`. After logout→relogin as a
  different account in the same document, the previous user's rows/chips can flash
  until the fresh fetch resolves. No sentiment/score in that markup, but post-PUL-74
  it is per-user data.
- **Fix**: Extend the `doLogout` reset with 2 lines clearing the table body and chips
  (same pattern as the bar).
- **Decision**: SKIPPED (save-report-only; candidate for a future UX/cleanup change)

### F2 — 7-day aggregate computed from at most 100 rows

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality
- **Location**: static/index.html:2432
- **Detail**: `page=1&page_size=100`, newest-first — a watchlist producing >100
  announcements in 7 days would silently undercount. Unlikely at current scale; the
  plan explicitly excluded changing the fetch size.
- **Fix**: Nothing now; revisit if watchlists grow.
- **Decision**: SKIPPED (accepted threshold)

### F3 — Duplicate my-wallet fetch per view open (admin)

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Safety & Quality (performance)
- **Location**: static/index.html:2144, 2432
- **Detail**: Table and bar each hit `/announcements/my-wallet` → 2 BQ queries per
  view open. Admin-only and bounded.
- **Fix**: Nothing now; share the fetch if BQ cost ever matters.
- **Decision**: SKIPPED (accepted cost)

### F4 — Vestigial X-API-Key/X-Client-Id headers in frontend fetches

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: static/index.html:2433, 2467
- **Detail**: Session is cookie-JWT since PUL-74, but the sentiment-bar and
  watchlist-tickers fetches still send `X-API-Key`/`X-Client-Id` (often null).
  Harmless — PUL-74 leftover, not PUL-82 drift.
- **Fix**: Clean up together with the "DROP watchlist.client_id" chore (~2026-07-26);
  add to that task's scope.
- **Decision**: DEFERRED → fold into DROP client_id chore
