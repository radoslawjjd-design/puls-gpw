<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Watchlist Admin View — Score Column, Sentiment Period Info & Drill-Down Popup

- **Plan**: `context/changes/watchlist-admin-score-sentiment-drilldown/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-21
- **Verdict**: REVISE → SOUND (all findings fixed)
- **Findings**: 0 critical, 2 warnings, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING → resolved (F1) |
| Blind Spots | WARNING → resolved (F2, F4) |
| Plan Completeness | WARNING → resolved (F3) |

## Grounding

4/4 paths ✓, 7/7 symbols ✓, brief↔plan ✓. Extra verified: admin my-wallet branch returns full `structured_analysis` (sentiment kept, `src/api.py:471-477`) ✓; no GET `/announcements/{id}` shadow for the new sub-paths ✓; `fetchWlSentimentSummary` callers (2322/2417/2747/2768) all no-arg ✓.

## Findings

### F1 — Parameterized INTERVAL contradicts codebase pattern (and BQ)

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real correctness risk; fix small but must be chosen
- **Dimension**: Architectural Fitness
- **Location**: Phase 2 §1 (Contract) + Critical Implementation Details
- **Detail**: Contract bound the window as `TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL @days DAY)` with `days` as a `ScalarQueryParameter`. Every INTERVAL in `db/bigquery.py` is a literal/f-string constant (663, 673, 1433, 1742); `grep "INTERVAL @"` = 0 hits. BQ rejects a param in the INTERVAL slot of `TIMESTAMP_SUB`; `days` is a fixed constant (7), so the param buys nothing.
- **Fix**: f-string-interpolate the day count via a new module constant `_WL_SENTIMENT_WINDOW_DAYS = 7` (`INTERVAL {_WL_SENTIMENT_WINDOW_DAYS} DAY`); drop the `@days` param. Applied to Phase 2 §1, Phase 3 §1, and Critical Implementation Details.
- **Decision**: FIXED (Fix in plan)

### F2 — Phase 2 breaks the existing sentiment-bar E2E suite

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — known test breakage; needs concrete conftest + render constraints
- **Dimension**: Blind Spots
- **Location**: Phase 2 §2/§3 + Testing Strategy
- **Detail**: `tests/e2e/test_watchlist_sentiment.py:46-48` asserts the bar contains `"Ostatnie 7 dni"`, `"Pozytywny: 1"`, `"Śr. score: 85"` — client-derived today from the faked my-wallet rows (conftest patches `src.api.list_announcements_for_watchlist`, line 462). Phase 2 moves aggregation to a new endpoint whose BQ fn `summarize_watchlist_sentiment` isn't patched → the endpoint hits real BQ / errors; the new render must also preserve those substrings.
- **Fix**: Added Phase 2 §4 (patch `src.api.summarize_watchlist_sentiment` in `tests/e2e/conftest.py` returning a shape rendering `"Pozytywny: 1"`/`"Śr. score: 85"` for the seeded PKO row); added render-substring + int-rounding constraints to §3; added a Testing-Strategy note naming both new BQ fns to patch; added Progress item 2.2.
- **Decision**: FIXED (Fix in plan)

### F3 — Phase 4 lists closeModal() as new, but doLogout already calls it

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 4 §1 (Contract)
- **Detail**: `doLogout` already calls `closeModal()` (`static/index.html:1279`) and clears `_watchlistFetched` (1289) + the sentiment bar (1290). The real gap is only `#my-wallet-table-body`, `#wl-tickers-list`, `_wlData`.
- **Fix**: Trimmed Phase 4 contract to the actual gap; noted `closeModal()` + bar clearing already exist.
- **Decision**: FIXED (Fix in plan)

### F4 — "Consistency lock" test can't truly verify via mocked BQ

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 3 §Automated (3.2) + Testing Strategy
- **Detail**: BQ is mocked at the API layer (no emulator), so a summary-count vs drill-down-rowcount comparison just compares fixture returns — false confidence. The real guarantee is by construction: both queries share the `_SENTIMENT_BUCKET_SQL` constant.
- **Fix**: Reframed 3.2 (plan + Progress + Testing Strategy) as a structural assertion that both BQ functions embed the identical `_SENTIMENT_BUCKET_SQL` fragment; stated the consistency guarantee is by-construction.
- **Decision**: FIXED (Fix in plan)
