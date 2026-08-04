# Dated FIFO Lot Ledger — Plan Brief

> Full plan: `context/changes/fifo-lot-ledger/plan.md`
> Research: `context/changes/fifo-lot-ledger/research.md`

## What & Why

The codebase opens and consumes FIFO lots in **three** places, none of which record
*when* a lot was acquired. PUL-114 needs a cost basis evaluated as of a date, and two
new date-bearing outputs (`first_buy_date`, `days_held`), which is impossible without
dated lots. This change builds one dated ledger and moves the two Python consumptions
onto it, so part 2 has a single engine to hang the read paths on instead of a fourth
implementation.

## Starting Point

`src/brokers/xtb.py:reconstruct_positions` runs FIFO at import and writes
`avg_buy_price`; `src/portfolio_realized.py:compute_realized_pnl` runs a second,
subtly different FIFO over the stored operations; `db/bigquery.py`'s `ops_basis` CTE
computes a plain SQL average. Measured against production, the first is **FIFO-exact
today** — replaying all 508 operation rows reproduces all 43 stored positions at
`+0.00` on both wallets. The ticket's claim that `avg_buy_price` is a naive average is
false; only `ops_basis` is, and that one is part 2's problem.

## Desired End State

`src/portfolio_lots.py` is the only place a lot is opened or consumed. Its lots carry
`occurred_at`, and it reports what a sale could not cover rather than flooring
silently. Both Python consumers delegate to it and return identical numbers — proven by
51 existing tests passing with no assertion edited. The realized endpoint additionally
reports `days_held_weighted` and `days_held_max` per ticker, `null` where no matched lot
exists.

## Key Decisions Made

| Decision | Choice | Why | Source |
| -- | -- | -- | -- |
| Split of PUL-114 | Two changes, two PRs | Part A cannot move a production number; part B carries all behavioural risk with a small diff | Plan (pre-planning) |
| Residual shares with no lot | Stored `avg_buy_price` as declared fallback | Never zero — a zero basis reads as 100% profit; ledger *reports* the gap, caller prices it | Plan (pre-planning) |
| How the ledger feeds SQL | Step-function segments via `UNNEST` | Basis only changes on operation days; SQL valuation, LOCF/BOCF and coverage gate stay untouched | Plan (pre-planning) |
| `days_held` across several lots | Two scalars: weighted mean + oldest lot | Oldest-lot is what PUL-114's criterion asks for; weighted is what the question actually means | Plan (pre-planning) |
| Sort tiebreak | Adopt buy-before-sell | Strictly safer, and provably inert — 0 tied instants across 508 production rows | Research |
| Ticker normalization | Stays in the XTB adapter | Stored rows are already normalized (`src/api.py:486`); the ledger must not learn one broker's quirk | Research |
| Instrument name | First non-`None` wins | `xtb.py:221` locks in `None` today; a small, deliberate, recorded import fix | Plan |
| Days-held exposure | Ships in part A | The endpoint serialises the result raw, and holding it back means reopening the same file in part B | Plan |
| As-of-date query | Deferred to part 2 | No consumer and no honest test here; the ledger already walks events in order, so part 2 adds an accumulator | Plan |

## Scope

**In scope:** `src/portfolio_lots.py` and its tests; `compute_realized_pnl` rebuilt on
it plus two days-held fields; `reconstruct_positions` rebuilt on it.

**Out of scope:** `db/bigquery.py` entirely (`ops_basis`, `get_portfolio_history`); the
positions read path and `PortfolioPositionOut`; `first_buy_date`; the as-of-date query;
the treemap and calendar; float→Decimal; the −4 482 PLN divergence.

## Architecture / Approach

Strangler, three phases, each independently green. The ledger is pure logic with no
callers in phase 1, so it is fully test-drivable before anything depends on it. Phases
2 and 3 then swap one consumer each, ordered by blast radius — the read-only realized
path before the import path that writes to BigQuery. Each consumer keeps a thin adapter
mapping its own input shape (BigQuery `dict`s; parser `Operation`s) onto one normalized
ledger event, which is what lets them share an engine without sharing a contract.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| -- | -- | -- |
| 1. Dated lot ledger | `src/portfolio_lots.py` + unit tests, no callers | Designing a shape part 2 then has to fight |
| 2. Realized on the ledger | Same numbers, plus two days-held fields | Period filtering and `unmatched_tickers` ordering are load-bearing and easy to hoist wrongly |
| 3. XTB import on the ledger | `_Lot` / `_consume_oldest` deleted | This path writes `avg_buy_price` to BigQuery — arithmetic must be byte-identical |

**Prerequisites:** none — no schema change, no backfill, no deploy ordering.
**Estimated effort:** ~1 session across 3 phases.

## Open Risks & Assumptions

- The 51-test baseline is assumed to be a real behavioural contract. If any assertion
  *has* to change during phases 2–3, that is a finding to surface, not a test to fix.
- The name-resolution fix is a deliberate behaviour change in the import. Low impact
  (it can only fill a `company_name` that is empty today), but it is a change.
- Part 2 inherits an unresolved question: the **−4 482 PLN** divergence against the XTB
  statement cannot come from either basis source — today's gap is exactly `+0.00`. Its
  verification criterion needs restating before part 2 is planned.

## Success Criteria (Summary)

- `uv run pytest --tb=short` green, with no existing assertion edited.
- `git grep -n "_consume_oldest\|_Lot"` matches only the ledger and its tests.
- Re-running the production FIFO audit still reports `delta=+0.00` on both wallets.
