# Full-coverage gate — backward-fill debut prices (PUL-100) — Plan Brief

> Full plan: `context/changes/history-coverage-gate/plan.md`
> Ticket (acts as frame + research): Linear PUL-100 / GitHub #195

## What & Why

The portfolio value chart emits a day only when **every** holding has a price that day,
so the series starts at the *latest* first-price date across all holdings. A 0.66%
position in a company listed three months ago truncates a full year of history down to
71 points. We backward-fill each holding's debut close across the days before it was
listed, so one recent IPO stops deciding how much history everyone else gets to see.

## Starting Point

`get_portfolio_history` (`db/bigquery.py:465`) already forward-fills prices (LOCF, the F1
decision from PUL-79) and then applies a `WHERE missing = 0` gate. The endpoint returns a
bare JSON array; the SPA consumes it directly into an inline-SVG chart. Measured on real
BigQuery today: "Główny" returns 71 points at `1y` (2026-04-16 → 07-24), clamped by
`S2B`; a second portfolio without a recent IPO correctly returns 249.

## Desired End State

`1y` returns a full year regardless of when the newest holding debuted. Days before a
debut value that holding at its first close, so the curve is continuous — no phantom step
on debut day. An `(i)` next to the chart title names every holding valued at a debut price
and from when, plus any holding dropped for having no price data at all.

## Key Decisions Made

| Decision | Choice | Why | Source |
|---|---|---|---|
| Pre-debut valuation | Backward-fill the debut close (BOCF) | Continuous by construction — the day before and the day of a debut hold the same value | Ticket |
| Rejected: `avg_buy_price` | No | A holder who bought above the debut price would see a cliff on debut day — worse than the bug | Ticket |
| Rejected: skip missing positions | No | Introduces a step that reads as a gain that never happened | Ticket |
| Coverage gate | Demoted to safety net: fires only when a ticker has no price anywhere | Keeps the guard that matters, drops the one that clamps | Ticket |
| API response shape | Object `{series, notes, excluded}` | Metadata needs a home; the only consumer is our own SPA, so breakage risk is zero | Plan |
| Metadata delivery | Same query, cross-joined `STRUCT` array | A second round trip would double a 1.6 s user-facing latency | Plan |
| UI affordance | `(i)` icon with tooltip, click + keyboard focus (not hover-only) | User's call; tap/focus support neutralises the touch-device gap | Plan |
| Note suppression | Only when the debut falls after `start_date` | Otherwise every chart carries a permanent footnote | Plan |
| Performance work | None | 2.5 MB scanned for the 1y+400d window — clustering absorbed PUL-92's 16k → 1.9M row growth | Plan |

## Scope

**In scope:** BOCF fill in the history query; conditional aggregation replacing the gate;
per-ticker coverage metadata; dict return from `get_portfolio_history`; object response
from `GET /api/portfolio/history`; `(i)` affordance in the chart; unit, API and e2e tests;
before/after benchmark on real BigQuery.

**Out of scope:** query optimisation or caching; storing purchase dates (the tranche
approximation PUL-79 accepted stays); calendar, treemap and every other
`company_daily_stats` consumer.

## Architecture / Approach

One SQL statement continues to do all the work. `filled` gains
`COALESCE(LOCF, BOCF)`; `daily` aggregates conditionally on `px_ff IS NOT NULL` and
counts covered positions, so the outer filter becomes `covered > 0` instead of
`missing = 0`. A `coverage` CTE derives each holding's first real price and date; a
`meta` CTE folds that into two arrays cross-joined onto the daily rows and read once
from row 0 in Python. The endpoint mirrors the dict; the SPA reads `.series` for the
chart and `.notes` / `.excluded` for the affordance.

The load-bearing invariant: after LOCF **and** BOCF, `px_ff IS NULL` can only mean "this
ticker has no price anywhere in the window" — it is all-or-nothing per ticker. That is
what makes per-day conditional aggregation safe; an excluded holding is excluded on every
day, so excluding it cannot introduce a step. Its cost basis must be dropped alongside
its value, or P&L shows a permanent phantom loss.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. DB layer | BOCF + safety-net gate + coverage metadata; dict return | Window-frame subtlety: `FIRST_VALUE … CURRENT ROW AND UNBOUNDED FOLLOWING` must be verified against real data, not just mocks |
| 2. API layer | `{series, notes, excluded}` envelope | Five existing tests and the e2e mock pin the old array shape |
| 3. Frontend | `(i)` affordance, tap- and keyboard-accessible | Phase 2 breaks the chart until Phase 3 lands — verify with `--ignore=tests/e2e` in between |
| 4. Verification | Before/after benchmark on real BigQuery | Manual, easy to skip; it is what proves the acceptance criteria |

**Prerequisites:** real BigQuery credentials in `.env` (Phases 1 and 4 verify against
live data); branch `pul-100-history-coverage-gate` already created.
**Estimated effort:** one session across four phases.

## Open Risks & Assumptions

Three assumptions were verified against real BigQuery during plan review rather than
carried as risks:

- `COALESCE(LAST_VALUE …, FIRST_VALUE … CURRENT ROW AND UNBOUNDED FOLLOWING)` fills
  pre-debut days with the debut close and leaves post-debut days untouched — **confirmed**.
- `ARRAY_AGG` over zero qualifying rows reaches Python as `[]`, not `None` — **the
  earlier claim in this brief was wrong**; no coercion needed (a defensive `or []` is
  still harmless).
- `FROM meta LEFT JOIN daily ON TRUE` returns one row when `daily` is empty —
  **confirmed**, and it is why the join is written meta-first (plan-review F1).

Remaining risks:

- Changing the response shape breaks the chart between Phase 2 and Phase 3. Known
  project gotcha: verify the intermediate state with `--ignore=tests/e2e`.
- The tooltip choice trades discoverability for a cleaner chart. Mitigated by click +
  keyboard focus + `aria-label`, but a user who never activates it still doesn't see the
  assumption.
- Backward-filling shifts the P&L curve as well as the value curve: a holder who bought
  above the debut price sees a flat phantom loss across pre-debut days (plan-review F3).
  Accepted — the alternative reintroduces the step this change removes.
- `first_px_date` is the first price in *our data*, not a listing date. Note wording must
  stay a statement about coverage, or it will assert a false debut for the 12 tickers
  whose history starts when the scraper did (plan-review F2).

## Success Criteria (Summary)

- `1y` on a portfolio holding a recent IPO returns a full year of points (~249, not 71).
- The chart states which holdings were valued at their debut price and from when.
- No phantom step in the value series on a holding's first quoted day.
