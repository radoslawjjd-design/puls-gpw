<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: FIFO Basis on the Read Paths

- **Plan**: `context/changes/fifo-basis-on-read-paths/plan.md`
- **Scope**: all 3 phases
- **Date**: 2026-08-05
- **Verdict**: APPROVED
- **Findings**: 1 critical, 1 warning, 1 observation — all fixed

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | CRITICAL → PASS after fix |
| Architecture | PASS |
| Pattern Consistency | WARNING → PASS after fix |
| Success Criteria | PASS (browser check delegated) |

## Evidence

**Scope**: the diff touches exactly the planned files — `src/portfolio_lots.py`,
`db/bigquery.py`, `src/api.py`, three test files, `tests/e2e/conftest.py`, and the
change folder. `src/portfolio_treemap.py` and `get_portfolio_calendar_data` are
untouched, as the plan requires.

**Behavioural verification, measured on production rather than asserted**:

- Right edge: both bases computed inside one query over one price snapshot — `+0.0000`
  on all five wallets, zero holdings dropped by the tightened predicate.
- Historical correction: reported P&L rises on 205 of 250 trading days, worst
  **+2 143,10 PLN** — the figure research predicted before phase 2 was written.
- `first_buy_date`: 20 live positions, key present on every one, re-bought tickers
  reporting the re-buy.

**Success criteria**: every automated criterion re-run and green (1106 tests, ruff
clean). Two criteria were **corrected rather than ticked as written** — 2.1 promised an
untouched suite, which a deliberate behaviour change cannot honour, and 2.3 grepped for
a string that survives in a useful comment. Both corrections are recorded in
`baseline-report.md`.

## Findings

### F1 — The prefix replay degrades to unusable on a heavy account

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: `src/portfolio_lots.py`, `basis_segments`
- **Detail**: The ledger "as of a date" was rebuilt over the **wallet's** whole calendar
  once per event date. A ticker's lots only move on its own trading days, so most of
  that work is discarded — and the cost is superlinear. Measured:

  | events | dates | seconds |
  | --: | --: | --: |
  | 100 | 50 | 0.004 |
  | 2 000 | 1 000 | 1.51 |
  | 5 000 | 2 500 | 8.82 |
  | 10 000 | 5 000 | **47.01** |

  Production is ~100 events per wallet, so nothing is slow today — which is exactly what
  makes it a trap. This sits on the request path, and an active five-year portfolio
  reaches the thousands. The failure mode is a user-visible timeout on the endpoint that
  already costs ~1.6 s.
- **Fix**: Replay per ticker over its own event dates instead of per wallet-day, then
  sort the segments once at the end so output order stays stable.
- **Result**: 10 000 events **47.01 s → 0.899 s** (52×). Production segments unchanged
  (210), right-edge invariant re-verified at `+0.0000`, all 21 ledger tests green
  without an assertion edited.
- **Decision**: FIXED

### F2 — A filtered test run was treated as evidence the file was clean

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: process, phase 3
- **Detail**: Phase 3's new contract tests were run with `-k "first_buy_date or
  acquisition"` and reported 4 passed, which was read as "`tests/test_api.py` is green".
  It was not: adding a keyword argument broke three `assert_called_once_with` assertions
  and the e2e conftest fake, 19 failures in total, caught only by the full suite. The
  project's own rule — a BQ-backed endpoint needs *every* dependency it touches mocked —
  names this exact class, and the fake's signature is where it bites.
- **Fix**: The three call assertions and the conftest fake now carry the new argument;
  the fake mirrors production's contract (field present only when asked for). Full suite
  is the phase gate, never a filtered subset.
- **Decision**: FIXED

### F3 — `_basis_segments_for` depends on a default it does not state

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: `db/bigquery.py`, `_basis_segments_for`
- **Detail**: It calls `list_user_portfolio_positions`, which itself calls
  `list_broker_trades` when `include_first_buy_date=True`. The default is `False`, so
  there is no double fetch — but the relationship is invisible at the call site, and
  flipping that default would silently double the history endpoint's cost.
- **Fix**: Note the dependency where the call is made.
- **Decision**: FIXED

## Not findings, recorded so they are not re-litigated

- **The `-45.21` right-edge alarm** was a measurement artefact, not a regression: the
  quotes scheduler refreshed today's closes between capturing the baseline and diffing
  it. `Δvalue == Δpnl` exactly is the signature — it can only happen when the basis is
  unchanged and the price moved. Recorded in `baseline-report.md`.
- **The XTB `first_buy_date` "mismatch"** was the verification script keying expectations
  by ticker while XTB sits in two wallets with different histories. The code was right;
  the check reproduced by accident the cross-wallet merge this design prevents.
