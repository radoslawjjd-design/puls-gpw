<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Watchlist Admin View — Phase 3 (Sentiment Drill-Down Popup)

- **Plan**: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
- **Scope**: Phase 3 of 4
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 2 observations
- **Commit reviewed**: 1cb163c

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS (bucket allowlist-validated AND bound param; admin-gated; esc() on all modal interpolation) |
| Architecture | PASS (mirrors summary endpoint + list_announcements_for_watchlist + treemap cache idiom) |
| Pattern Consistency | PASS (1 observation, consistent with Phase 2's accepted plain-dict choice) |
| Success Criteria | PASS (644 tests, drill-down 403/422/list green, structural lock green, ruff clean on changed files) |

## Success Criteria Verification

- 3.1 pytest incl. drill-down 403-for-user / 422-invalid-bucket / bounded list: **PASS** (3 tests).
- 3.2 Structural consistency — both BQ fns embed identical `_SENTIMENT_BUCKET_SQL`: **PASS** (`test_list_watchlist_by_sentiment_query_shares_normalization`).
- 3.3 Lint: **PASS** on all changed files (pre-existing ruff errors in `tests/test_scraper.py` are out of scope).
- 3.4 Manual — each bucket click opens modal listing exactly the counted announcements: **PASS** (user-confirmed "działa").

## Notable positives

- `bucket` is defended twice: allowlist-validated against `_SENTIMENT_BUCKETS` (→422) before it reaches BQ, and passed as a bound `@bucket` param. No injection surface.
- `list_watchlist_by_sentiment` embeds `_SENTIMENT_BUCKET_SQL` verbatim — the same constant the summary uses — so popup contents equal bar counts by construction, not by a brittle behavioral test.
- `_WL_SENTIMENT_LIST_CAP` passed explicitly to the BQ fn so the `truncated` flag reflects the exact bound the query used.
- E2E conftest patches `list_watchlist_by_sentiment` (per plan §Testing Strategy), keeping `test_watchlist_sentiment.py` green.
- Modal reuses `#modal-overlay` + `closeModal` (✕/backdrop/Escape) with `esc()` on every interpolated field.

## Findings

### F1 — Bucket hover uses an undefined CSS var (dark-theme cosmetic)

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: static/index.html — `.wss-item[data-bucket]:hover`
- **Detail**: `background:var(--brand-soft, rgba(0,0,0,.05))` — `--brand-soft` is defined nowhere in the file (single occurrence = this usage), so the fallback always wins. On dark theme `rgba(0,0,0,.05)` (black at 5%) is nearly invisible, weakening the hover affordance. Light theme (tested) is fine.
- **Fix**: Drop `--brand-soft` and use a theme-aware token or add a dark override; e.g. `rgba(127,127,127,.12)` reads on both themes. Purely cosmetic.
- **Decision**: PENDING

### F2 — Endpoint returns a plain dict, not a Pydantic response model

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/api.py — `announcements_my_wallet_sentiment_list`
- **Detail**: Returns `{items, truncated}` as a raw dict — the same choice as Phase 2's summary endpoint (F1 there, ACCEPTED). Admin-only + server-computed, so there is nothing to strip. Flagged only for the record; it is consistent with the Phase 2 decision.
- **Fix**: None needed — accepted as the established pattern for these admin-only PUL-87 endpoints.
- **Decision**: ACCEPTED — consistent with Phase 2 F1.
