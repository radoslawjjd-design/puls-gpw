<!-- PLAN-REVIEW-REPORT -->
# Plan Review: FIFO Basis on the Read Paths

- **Plan**: `context/changes/fifo-basis-on-read-paths/plan.md`
- **Mode**: Deep
- **Date**: 2026-08-05
- **Verdict**: REVISE → SOUND after fixes
- **Findings**: 1 critical, 3 warnings, 2 observations — all 6 fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING → PASS after fix |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING → PASS after fix |
| Plan Completeness | WARNING → PASS after fix |

## Grounding

6/6 paths ✓ (`db/bigquery.py`, `src/api.py`, `src/portfolio_lots.py`,
`src/portfolio_treemap.py`, `tests/test_portfolio_lots.py`, `tests/e2e/conftest.py`),
2/2 symbols ✓ (`ops_basis` ×2 in `db/bigquery.py`, `include_history` ×4), brief↔plan ✓.
`basis_segments` / `first_open_lot_dates` correctly absent — Phase 1 builds them.

## Findings

### F1 — The e2e criterion targets a suite that cannot see an unrendered field

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Phase 3, change #4
- **Detail**: `tests/e2e/` is Playwright — `tests/e2e/test_portfolio_positions.py:1`
  imports `playwright.sync_api` and drives the DOM. This change renders nothing, so a
  browser test could only assert on a field the UI does not display. The owner's choice
  ("e2e on the positions contract") is right; the file was wrong. `tests/test_api.py`
  already tests HTTP contracts with FastAPI's `TestClient`.
- **Fix**: Move the contract test to `tests/test_api.py`; leave the browser test to
  PUL-123 part 2, where the column appears.
- **Decision**: FIXED

### F2 — Phase 1 commits the owner's real portfolio to a public repository

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1, change #1
- **Detail**: `gh repo view` reports `PUBLIC`. Part 1 already pushed
  `baseline/positions.json` and `baseline/realized.json` — tickers, share counts, average
  buy prices and Firebase user ids. Phase 1 planned a *history* baseline: a full year of
  portfolio value, strictly more revealing. The plan treated committing it as a given
  rather than as a decision.
- **Fix A ⭐ Recommended**: Keep the artefact in the scratchpad; commit only the derived
  comparison (`baseline-report.md`).
  - Strength: The evidence a future reader needs stays in the repo; the data does not.
  - Tradeoff: The baseline cannot be regenerated from the repo after this session.
  - Confidence: HIGH — the equivalence claim is checkable from the comparison alone.
  - Blind spot: Part 1's already-pushed baselines are untouched by this.
- **Fix B**: Commit as before, as an explicit accepted decision.
  - Strength: Full reproducibility.
  - Tradeoff: Permanent in public git history; removal later needs a rewrite.
  - Confidence: HIGH — it is the owner's own data and repo.
  - Blind spot: None significant.
- **Decision**: FIXED via Fix A (owner's choice). Part 1's baselines deliberately left
  in place — the owner declined the retro-removal option.

### F3 — `basis_segments` undefined for a key absent from `positions` with a non-zero residual

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1, change #2 (Contract)
- **Detail**: `residual = today_shares − total_signed` presumes a `positions` entry
  supplying both `today_shares` and `avg_buy_price`. A key with no position row — exactly
  the sold-to-zero case replacing `ops_basis` — has `today_shares = 0` and no stored
  price. If its operations do not net to zero (a hand-deleted position leaves buys on
  record; `db/bigquery.py:987-991` guards the same shape in SQL), the residual is
  non-zero with nothing to price it. The `denominator ≤ DUST` rule covers zero and
  negative denominators, not "positive denominator, unpriced residual".
- **Fix**: Specify that such a residual is folded in at the ledger's own open-lot basis
  (`ledger_cost / ledger_open`), never at zero — a zero basis renders as 100% profit.
- **Decision**: FIXED

### F4 — Criterion 2.7 cannot close inside Phase 2

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, Manual Verification
- **Detail**: "Endpoint latency measured before/after on the real service" — the branch
  does not reach production until the release branch merges, so the "after" half is only
  observable post-deploy. As written the phase gate cannot close honestly.
- **Fix**: Record the pre-change latency in Phase 2; make the post-deploy half a release
  check rather than a phase gate.
- **Decision**: FIXED

### F5 — Progress phase titles do not match the phase headings verbatim

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: `## Progress`
- **Detail**: Backticks around `ops_basis` and `first_buy_date` appear in the phase
  headings but not in Progress, breaking the mechanical title match `/10x-implement`
  relies on.
- **Fix**: Drop the backticks from the phase headings.
- **Decision**: FIXED

### F6 — The pre-first-operation fallback is load-bearing but reads as dead code

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, change #3
- **Detail**: `COALESCE(dated_basis.basis, h.avg_price)` looks like defensive residue
  once segments exist, but it is the only thing pricing a position entered by hand: such
  a position has no operations, therefore no segment, and `shares_on_day` equals its
  residual on every day of the range.
- **Fix**: State in the Contract why the fallback arm survives, so it is not tidied away.
- **Decision**: FIXED

## Triage Summary

```
Fixed:     F1, F2 (Fix A), F3, F4, F5, F6   (6)

► Verdict after fixes: REVISE → SOUND
```
