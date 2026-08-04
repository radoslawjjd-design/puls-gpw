<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Dated FIFO Lot Ledger

- **Plan**: `context/changes/fifo-lot-ledger/plan.md`
- **Mode**: Deep
- **Date**: 2026-08-04
- **Verdict**: REVISE → **SOUND** after fixes
- **Findings**: 1 critical, 2 warnings, 2 observations — all fixed

## Verdicts

| Dimension | Verdict | After fixes |
|-----------|---------|-------------|
| End-State Alignment | PASS | PASS |
| Lean Execution | PASS | PASS |
| Architectural Fitness | PASS | PASS |
| Blind Spots | FAIL | PASS |
| Plan Completeness | WARNING | PASS |

## Grounding

7/7 paths ✓ (`src/portfolio_realized.py`, `src/brokers/xtb.py`, `src/api.py`,
`db/bigquery.py`, and the three test files), `src/portfolio_lots.py` correctly absent ✓,
5/5 symbols ✓ (`_consume_oldest` `xtb.py:298`, `_Lot` `xtb.py:67`, `_sort_key`
`portfolio_realized.py:42`, `normalize_ticker` `xtb.py:184`, `_merge_positions_by_ticker`
`api.py:654`), brief↔plan ✓, Progress↔Phase mechanically consistent ✓ (4+1 / 5+1 / 4+2).

## Findings

### F1 — Aggregating from matches instead of sells silently understates the money

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: Blind Spots
- **Location**: Phase 2 — Rebuild the realized computation
- **Detail**: The plan promises "identical values", but `shares_sold`, `proceeds` and
  `sales` are counted per *sale* today (`src/portfolio_realized.py:132-135`), and
  `all_years` per *sale* too (`:119`). Only `cost` comes from matched lots. The ledger's
  natural output is a match list, which carries only the *covered* volume — so folding
  it into the per-ticker totals drops the uncovered part of every partially-matched
  sale, and a sale spanning three lots counts as three sales.
  The existing suite does not catch it: `tests/test_portfolio_realized.py:89-97` asserts
  `cost` and `pnl_pct` for a fully unmatched sale but never `shares_sold` or `proceeds`,
  and no test covers a *partially* covered sale at all.
- **Fix**: Add a "Critical Implementation Details" entry naming the sells-not-matches
  rule, and add a test that fails on the wrong source **before** the refactor —
  4 bought / 10 sold must report `shares_sold` 10 and proceeds over all 10, and a sale
  consuming three lots must report `sales == 1`.
- **Decision**: FIXED

### F2 — "The same numbers as before" has nothing to compare against

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 2 criterion 2.6, Phase 3 criterion 3.6
- **Detail**: Both manual criteria diff against "before the change", but by the time
  they run the old implementation no longer exists. As written they are assertions of
  faith, not verifications.
- **Fix**: Capture the baseline in Phase 1, while `master`'s behaviour still runs —
  `compute_realized_pnl` over the production trade history and `reconstruct_positions`
  over the XTB test fixture, committed as JSON under `baseline/`, deleted at archive.
- **Decision**: FIXED

### F3 — The purity check is unsound

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1 — Automated Verification
- **Detail**: The one-liner imports `ast` and `sys` without using either, and does a
  substring search — a comment mentioning "bigquery" fails it, and `import db.bigquery`
  written as `from db import bigquery` inside a string would pass it.
- **Fix**: Parse the module and assert no import resolves to `db`, `src`, `google`,
  `fastapi` or `pydantic`.
- **Decision**: FIXED

### F4 — `git grep` exits 1 when it finds nothing

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 and Phase 3 — Automated Verification
- **Detail**: Two criteria are phrased "returns nothing" / "matches only". Verified by
  exit code — which is this project's own rule — a clean result reads as a failure.
- **Fix**: State the expected exit code explicitly in both criteria.
- **Decision**: FIXED

### F5 — `first_acquired_at` has no consumer, but `basis_as_of` was deferred for exactly that reason

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Lean Execution
- **Location**: Phase 1 — The ledger module
- **Detail**: The plan defers the as-of-date query to part 2 on YAGNI grounds, then
  includes `first_acquired_at`, which is equally unused in part 1 — the positions read
  path it serves is part 2.
- **Fix**: Drop it; it is one `min()` over the open lots when part 2 needs it. Record
  the reasoning so part 2 does not read the absence as an oversight.
- **Decision**: FIXED
