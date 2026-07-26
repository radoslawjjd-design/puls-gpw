<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Full-coverage gate — backward-fill debut prices (PUL-100)

- **Plan**: `context/changes/history-coverage-gate/plan.md`
- **Mode**: Deep
- **Date**: 2026-07-26
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 1 critical, 3 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

7/7 paths ✓, 5/5 frontend cache call sites ✓, `docs/reference/contract-surfaces.md`
absent (check skipped), brief↔plan: 1 mismatch (F6), Progress↔Phase numbering ✓ /
titles drifted (F4).

Three SQL assumptions were verified against real BigQuery rather than reasoned about
(scratchpad `probe_bocf.py`):

| Assumption | Result |
|---|---|
| `COALESCE(LAST_VALUE …, FIRST_VALUE … CURRENT ROW AND UNBOUNDED FOLLOWING)` backward-fills | PASS — `[35.7, 35.7, 35.7, 35.7, 40.0]` as predicted |
| `ARRAY_AGG` over zero rows returns `NULL` requiring coercion | **FALSE** — client surfaces `[]` |
| `FROM meta LEFT JOIN daily ON TRUE` survives an empty `daily` | PASS — 1 row, metadata intact |

## Findings

### F1 — Metadata is lost in exactly the case it matters most

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1, change #2 (coverage metadata)
- **Detail**: The plan cross-joins `meta` onto `daily`. When every holding lacks prices,
  `covered > 0` empties `daily`, the cross join returns zero rows, and the `excluded`
  list never reaches the user — a blank chart with no explanation, which is precisely
  the failure the ticket's "report it" requirement exists to prevent.
- **Fix**: Reverse the join direction — `FROM meta m LEFT JOIN daily d ON TRUE`. Python
  drops rows with a `NULL` `snapshot_date` before building the series.
  - Strength: Verified on real BigQuery — the degenerate case returns one row carrying
    the metadata.
  - Tradeoff: Python must filter `NULL` dates; one extra guard.
  - Confidence: HIGH — probe 3 confirms the behaviour directly.
  - Blind spot: None significant.
- **Decision**: FIXED

### F2 — The note asserts a listing date the query cannot know

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 3, affordance markup
- **Detail**: The ticket's wording is *"S2B notowany od 16.04.2026"*, but `first_px_date`
  is the first price in our data, not a listing date. The 12 universe tickers with no
  archive file (`AAS`, `QUANTUM`, `REXA`, …) have data only from 2026-06-26 because that
  is when the scraper started — the note would state a falsehood in a finance app.
- **Fix**: Word it as a data-coverage statement, true regardless of cause: *"brak
  notowań przed <date> — wcześniejsze dni wycenione kursem <price> zł"*.
  - Strength: Correct for both causes; no extra query work to distinguish them.
  - Tradeoff: Slightly less friendly than "notowany od" for the genuine-IPO case.
  - Confidence: HIGH — the ticker list with scraper-only history is already known.
  - Blind spot: None significant.
- **Decision**: FIXED

### F3 — BOCF moves the P&L curve, and the plan says nothing about it

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Critical Implementation Details
- **Detail**: The chart toggles Wartość↔Zysk/strata. Backward-filling contributes a
  constant `shares × (first_px − avg_buy_price)` to every pre-debut day, so a holder who
  bought above the debut price sees a flat phantom loss across the range. The plan framed
  BOCF purely as a valuation change.
- **Fix**: Document it as an accepted consequence (same class as PUL-79's tranche
  approximation; the alternative reintroduces the step) and word the note to cover both
  chart modes.
- **Decision**: FIXED

### F4 — Progress titles drifted from the phase Success Criteria

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: `## Progress`
- **Detail**: Seven rows (1.5, 1.6, 2.4, 3.4, 4.3, 4.5, 4.6) were abbreviated relative to
  the phase blocks. Numbering aligns so `/10x-implement` parses the plan, but the
  `progress-format.md` contract requires matching titles.
- **Fix**: Restate the seven rows verbatim.
- **Decision**: FIXED

### F5 — Frontend cache call sites not enumerated

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3, change #1
- **Detail**: The plan names `_fetchPortfolioHistorySlot` but the cache is also read at
  `static/index.html:3849` (`_ppHistDataAll === null` as a not-yet-loaded sentinel),
  nulled at `:3524-3525` and `:3862-3863`, and redrawn from at `:3877-3878`.
- **Fix**: List all five sites in the phase contract.
- **Decision**: FIXED

### F6 — The brief states a risk that does not exist

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: `plan-brief.md` → Open Risks & Assumptions
- **Detail**: The brief claimed `ARRAY_AGG` over zero rows returns `NULL` and Python must
  coerce. Verified: the BigQuery Python client surfaces `[]`.
- **Fix**: Correct the brief; move the three verified assumptions out of the risk list.
- **Decision**: FIXED

### F7 — Phase 2 leaves e2e red without saying so

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 Overview
- **Detail**: Phase 2's criteria deliberately use `--ignore=tests/e2e`, but nothing warns
  that a red e2e suite between Phases 2 and 3 is expected — the PUL-91 gotcha.
- **Fix**: One sentence in the phase overview.
- **Decision**: FIXED

## Triage Summary

Fixed: F1, F2, F3, F4, F5, F6, F7 (7). Skipped: none. Accepted: none. Dismissed: none.

► Verdict after fixes: **SOUND**
