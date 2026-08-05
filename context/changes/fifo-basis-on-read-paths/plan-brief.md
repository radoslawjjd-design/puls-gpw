# FIFO Basis on the Read Paths — Plan Brief

> Full plan: `context/changes/fifo-basis-on-read-paths/plan.md`
> Research: `context/changes/fifo-basis-on-read-paths/research.md`

## What & Why

The portfolio history chart values every past day against **today's** cost basis — one
constant multiplied across a year. Measured on production, that overstates the basis on
**69 of 76** operation dates, worst **+2 143,10 PLN (+7,12%)**, so the curve
systematically reports less profit than was real. This change feeds it the cost of the
lots that were actually open on each day, using the dated ledger part 1 built.

## Starting Point

Part 1 (`fifo-lot-ledger`, PR #248) built `src/portfolio_lots.py` and moved both Python
lot consumptions onto it **without moving a single production number**. Every basis in
the system is already correct for today — `avg_buy_price` is FIFO-exact to +0,00 across
all 43 positions — and all of them are frozen. Part 2 is where the numbers move.

## Desired End State

The history curve values each day against that day's lots. `ops_basis` — a weighted
average wrong for 8 of 20 sold-to-zero tickers, worst +11,33% — no longer exists.
`/api/portfolio/positions` carries `first_buy_date`, the oldest **open** lot's date,
which unblocks PUL-123 part 2. Today's reported P&L does not move at all.

## Key Decisions Made

| Decision | Choice | Why | Source |
| -- | -- | -- | -- |
| Segment payload | Basis **price** per share | Measured: `ledger_open + residual` equals SQL's `shares_on_day` exactly, so the P&L formula stays untouched | Research |
| Feed mechanism | `UNNEST` array parameter, per request | 93 structs on the largest wallet — no materialised table earns its keep | Plan (pre-part-1) |
| Residual shares | Priced at stored `avg_buy_price` | A zero basis renders as 100% profit | Plan (pre-part-1) |
| Basis-less holding | Excluded from value **and** basis | Matches what the coverage gate already does for unpriced tickers; makes phantom profit unreachable | Plan |
| `first_buy_date` source | Computed per request from the ledger | No schema change, no backfill, and it can never drift out of step with the basis | Plan |
| "Wszystkie" merge rule | Earliest open lot | Corresponds to a purchase that actually happened; a weighted date does not | Plan |
| UI notice | None | Existing `notes`/`data_from` describe data coverage; a methodology note conflates two caveats | Plan |
| Adjacent realized bug | Separate ticket | Two corrections in one PR make neither attributable | Plan |

## Scope

**In scope:** the history query's basis; deleting `ops_basis`; `portfolio_id` on
`list_broker_trades`; `first_buy_date` on the positions endpoint and its "Wszystkie"
merge; an e2e contract test for the new field.

**Out of scope:** the treemap (its "since purchase" figure is about today, and today's
basis is already exact); the calendar (carries no basis at all); the −4 482 PLN
divergence (PUL-124); `compute_realized_pnl`'s cross-wallet lot merging; any rendering.

## Architecture / Approach

Strangler, same as part 1. The pure part — events + stored positions → step-function
segments — lives in `src/portfolio_lots.py` under the same stdlib-only guard the ledger
has. `db/bigquery.py` fetches, builds, and binds an array parameter; it does no
arithmetic. Keeping the orchestration in the data layer means `tests/e2e/conftest.py`'s
wholesale patch of `get_portfolio_history` keeps working untouched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| -- | -- | -- |
| 1. Baseline + segment builder | Pre-change series frozen on disk; `basis_segments()` and `first_open_lot_dates()`, test-first, no callers | None — nothing in production can move |
| 2. History on segments | Time-varying basis; `ops_basis` deleted | The only phase that moves production numbers; three documented invariants in play at once |
| 3. `first_buy_date` | Opt-in field on the positions endpoint | Two other callers must not start paying for a query they don't need |

**Prerequisites:** part 1 merged or present on the branch — this depends on
`src/portfolio_lots.py`. Branch is cut from `feat/pul-114-fifo-lot-ledger`.
**Estimated effort:** one session; phase 2 carries most of it.

## Open Risks & Assumptions

- The share reconciliation was measured on **today's** production data. A future import
  that oversells would break the identity that makes a per-segment price safe — hence
  the ledger reports `uncovered` rather than absorbing it, and the basis-less path is
  guarded rather than assumed impossible.
- Phase 2's verification is a production diff, not a test. The SQL cannot be exercised
  without BigQuery, which is exactly why the Phase 1 baseline must land first.
- Added latency is asserted to be small but not yet measured; Phase 2 measures the real
  endpoint rather than trusting the estimate.

## Success Criteria (Summary)

- The chart's right edge still equals today's reported P&L, to the grosz, on every wallet
- Historical days sit higher, by up to ≈ +2 143 PLN around 2025-10-14
- A re-bought ticker's basis steps at the re-buy instead of running flat, and its
  `first_buy_date` reports the re-buy — not a purchase sold off 424 days earlier
