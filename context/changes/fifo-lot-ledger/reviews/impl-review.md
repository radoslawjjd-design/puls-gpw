<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Dated FIFO Lot Ledger

- **Plan**: `context/changes/fifo-lot-ledger/plan.md`
- **Scope**: all 3 phases
- **Date**: 2026-08-04
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 1 observation — both fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | WARNING → PASS after fix |
| Pattern Consistency | OBSERVATION → PASS after fix |
| Success Criteria | PASS (2.7 pending, delegated to local verification) |

## Evidence

**Scope**: the diff touches exactly the planned files — `src/portfolio_lots.py`,
`src/portfolio_realized.py`, `src/brokers/xtb.py`, their three test files, and the
change folder. No unplanned source file.

**Plan adherence**: three phases as described, with two deviations, both recorded in
the plan rather than silent — `first_acquired_at` dropped (no consumer in part 1,
same YAGNI line as the deferred `basis_as_of`), and the baseline captured over the
real production history because `tests/test_brokers_xtb.py:307` records that no export
file is committed to the repo.

**Behavioural equivalence, measured rather than assumed**:

- `compute_realized_pnl` over the 508-row production history, against the phase 1
  baseline: 3 scopes, 41 tickers, every pre-existing key identical; only
  `days_held_weighted` / `days_held_max` added.
- `reconstruct_positions` over the same history: 4 portfolios, 40 positions, 40 closed
  tickers — ticker, shares, `avg_buy_price`, `company_name` and the closed list all
  identical.
- The independent production FIFO audit still reports `delta=+0.00` on both wallets.

**Safety & quality**: the ledger is pure — no I/O, no SQL, no secrets, no external
boundary. Complexity is O(sells × open lots), the same shape as the code it replaced.

**Success criteria**: every automated criterion re-run this session and green (1091
tests, ruff clean). Manual 2.7 — the Zrealizowane tab in a browser — is the one row
left open; the figures behind it are verified against production by 2.6.

## Findings

### F1 — The ledger's purity is guarded by a one-off plan checkbox, not a test

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: `src/portfolio_lots.py`, `tests/test_brokers_xtb.py:427`
- **Detail**: `test_parser_package_imports_no_data_or_web_layer` defends the claim that
  the parser package is pure — which is what lets the golden dataset run with no
  infrastructure — but it inspects only the **direct** imports of `src/brokers/*.py`.
  Phase 3 added `xtb.py → src.portfolio_lots`, so that purity now also depends on the
  ledger, one hop away from anything the test opens. Criterion 1.4 checked it once at
  implementation time and leaves nothing behind in the repo.
- **Fix**: Promote the check to a real test in `tests/test_portfolio_lots.py`, mirroring
  the parser package's own AST-based guard.
- **Decision**: FIXED

### F2 — A comment claims a stronger ordering guarantee than the code gives

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `src/portfolio_realized.py:106`
- **Detail**: The comment said sales are walked chronologically "so that two tickers
  finishing on the same result keep the order the old single-pass loop gave them". The
  old loop broke same-instant ties by the input list's order; the rebuilt version breaks
  them by first appearance in the history. The difference needs two tickers selling in
  the same microsecond *and* finishing on an identical result, so it is unreachable in
  practice — but the comment should describe what the code does.
- **Fix**: Restate the comment as what is actually guaranteed — chronological across
  tickers, ties resolved deterministically by first appearance.
- **Decision**: FIXED
