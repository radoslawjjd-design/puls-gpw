<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Month selector for Dywidendy and Zrealizowane

- **Plan**: `context/changes/dividends-realized-month-selector/plan.md`
- **Scope**: Phases 1–4 of 4
- **Date**: 2026-08-04
- **Verdict**: APPROVED (after F1 fixed in review)
- **Findings**: 0 critical, 1 warning, 2 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (automated); manual pending human check |

## Findings

### F1 — Invalidation tests seeded pre-month cache keys and proved nothing

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Success Criteria
- **Location**: `tests/test_api.py` (`test_perf_invalidate_clears_history_the_all_sentinel_and_dividends`, `test_deleting_a_wallet_clears_the_caches_that_still_show_it`)
- **Detail**: Both tests seed literal keys of the shape
  `dividends:{user}:{wallet}:2026` — the pre-month format. They passed after the
  key gained a month segment, because `_perf_invalidate_portfolio` matches on the
  `dividends:{user}:` prefix and ignores the tail. That is a green test asserting
  a key the endpoints no longer write. The prefix scan happens to be robust here,
  but the test was no longer evidence of it.
- **Fix**: Seed the current key shape, including the month, and add the `realized:`
  counterpart which was never covered at all.
  - Strength: Restores the test to evidence rather than coincidence, and closes a
    real gap — `realized:` invalidation had no assertion before this change.
  - Tradeoff: None; the literals have to be maintained alongside the format either
    way, and leaving them stale is the more expensive option.
  - Confidence: HIGH — verified both tests still pass with the new literals.
  - Blind spot: None significant.
- **Decision**: FIXED.

### F2 — `parsed_year or 'all'` treats year 0 as "all"

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `src/api.py` (both endpoints' cache-key lines)
- **Detail**: `year` is validated with `isdigit()` only, so `year=0` parses to `0`
  and `0 or 'all'` yields `'all'` — a request for year zero is silently answered
  from the unfiltered entry. Nonsensical input with a harmless outcome, and it
  predates this change. The month cannot hit it: the new range check rejects 0.
- **Fix**: Range-check the year the way the month now is.
- **Decision**: SKIPPED — the plan's "What We're NOT Doing" explicitly excludes
  widening year validation, so that a behaviour change unrelated to the month does
  not ride along in this diff. Recorded here as a known choice.

### F3 — The month persists across wallet and view switches

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `static/index.html` (`_ppDivMonth`, `_ppRealMonth`)
- **Detail**: Neither state variable is cleared when the user changes wallet or
  leaves the tab — so a chosen month survives. Verified this is exactly what
  `_ppDivYear` / `_ppRealYear` already do: neither is reset anywhere either. The
  selector re-renders with its choice intact, so the control and the numbers agree.
- **Decision**: SKIPPED — matching the year's behaviour is the right call; diverging
  would be the surprise.

## Success Criteria

| Check | Result |
|-------|--------|
| Phase 1 — Warsaw attribution, both sides | PASS |
| Phase 2 — month at the data layer, FIFO intact | PASS |
| Phase 3 — validation, cache-key separation | PASS |
| Phase 4 — selector, all four combinations | PASS |
| `uv run pytest --tb=short` | PASS — 1037 passed |
| `uv run ruff check` | PASS |
| Manual verification (4.5–4.6) | PENDING — human check on the local build |

## Notes

- **The SQL is not covered by the unit tests**, which mock the BigQuery client. Both
  the timezone change and the month predicate were therefore executed against
  production data directly: the query parses, and September 2025 returns 893.44 of
  the year's 2622.40 gross. Without that step a syntax error would have reached
  master with a green suite.
- **The ticket's timezone claim was verified rather than taken on trust.** Reading
  the write path alone argues the opposite conclusion (openpyxl yields naive
  datetimes, serialized without an offset). The stored hour distribution — 7–15
  UTC, the GPW session in CEST — settles it: the instants are true UTC and the
  ticket is right. Acting on the code reading instead would have left the bug in.
- **The realized side had the same defect and the ticket did not mention it.**
  Fixing only the SQL would have left the two views disagreeing about which month
  a late-evening sale belongs to.
- No row moves today: no stored operation has a Warsaw month or year differing
  from its UTC one. This is a latent defect closed before month granularity
  multiplied its exposure, not a live wrong number corrected.
- The e2e dividend fake previously ignored its `year` argument, so dividend
  filtering had never been asserted end to end. It now narrows by period while
  still returning every year on record — the meta-first contract the selector
  depends on.
