<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Watchlist Admin View — Phase 2 (Sentiment Summary + Period Info)

- **Plan**: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
- **Scope**: Phase 2 of 4
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 1 observation
- **Commit reviewed**: dd4f2bd

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS (invalidation deviation documented + necessary) |
| Safety & Quality | PASS (admin-gated, parameterized, SQL dry-run-validated) |
| Architecture | PASS (shared `_SENTIMENT_BUCKET_SQL`; mirrors template) |
| Pattern Consistency | PASS (watchlist-join fn + treemap cache idiom) |
| Success Criteria | PASS (640 tests, ruff clean, SQL validates on prod schema) |

## Success Criteria Verification

- 2.1 pytest incl. summary 403/200 + structural normalization lock: **PASS** (3 new tests).
- 2.2 E2E `test_watchlist_sentiment.py`: **PASS** (3, via conftest fake `_fake_summarize_watchlist_sentiment`).
- 2.3 ruff: **PASS**.
- Extra: `summarize_watchlist_sentiment` SQL **dry-run validated** against `puls-gpw.espi_ebi` (~1.9 MB) — confirms the new CTE + `JSON_VALUE` + `COUNTIF` compiles against the real schema (tests mock BQ, so this is otherwise unverified).

## Notable positives

- Shared `_SENTIMENT_BUCKET_SQL` referenced once; Phase 3 reuses the same constant (consistency by construction).
- `_invalidate_wl_sentiment` on watchlist add/remove — a correct, necessary deviation from the plan's "no invalidation" line (the bar refetches right after a mutation and must not serve a 60s stale empty summary). Plan's "What We're NOT Doing" updated to reflect this.

## Findings

### F1 — Summary endpoint returns a plain dict, not a Pydantic response model

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/api.py — `announcements_my_wallet_sentiment_summary`
- **Detail**: Plan Phase 2 §2 said "Response model (new, admin-only)"; the handler returns the BQ function's raw dict. Safe and correct — the endpoint is admin-gated (nothing to strip) and all fields are server-computed — but it skips the Pydantic output contract the other announcement endpoints use. No behavioral impact.
- **Fix**: Optional — add a Pydantic response model for output-schema parity; otherwise accept the plain dict.
- **Decision**: ACCEPTED — plain dict is fine for an admin-only, server-computed payload.
