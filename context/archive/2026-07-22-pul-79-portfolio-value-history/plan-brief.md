# Portfolio Value-History Endpoint (PUL-79 / FARO-5) — Plan Brief

> Full plan: `context/changes/pul-79-portfolio-value-history/plan.md`
> Research: `context/changes/pul-79-portfolio-value-history/research.md`

## What & Why

FARO-5 backend for the faro-v2 UI: a value-over-time chart needs a data source. Add
`GET /api/portfolio/history?portfolio_id=…&range=1w|1m|3m|1y` → a per-trading-day series
`[{date, value_pln, pnl_pln}]` for the caller's own portfolio. The Designer builds the line
chart + range switcher after deploy.

## Starting Point

`get_portfolio_calendar_data` (`db/bigquery.py:362`) already computes daily portfolio value
from the per-user positions table × historical close, ETF-safe, trading-days-only — but scoped
to one month. `GET /api/portfolio/calendar` (`src/api.py:880`) wraps it with JWT auth, an
ownership guard, and a cache. This plan adds range-scoped siblings of both.

## Desired End State

A logged-in user requests `?range=3m` and gets an ascending JSON array of
`{date, value_pln, pnl_pln}`, one point per trading day, ETFs included. `1d` and unknown
ranges return 422; a portfolio the caller doesn't own returns 403.

## Key Decisions Made

| Decision                | Choice                                             | Why (1 sentence)                                                              | Source   |
| ----------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------- | -------- |
| Data source             | Compute-on-the-fly (generalize calendar query)     | Live BQ check proved `portfolio_snapshots` is owner-only, sparse, stale.     | Research |
| `pnl_pln` semantics      | Cumulative unrealized = value − Σ(shares×avg_buy)   | Matches a P&L-over-time line; reuses avg_buy_price already in the CTE.        | Plan     |
| `1y` range              | Keep it; document the tranche approximation         | Full range set the Designer asked for; the fix (snapshots) is unusable.      | Plan     |
| Wallet-not-found status | 403 (match calendar)                                | Consistency with the endpoint being cloned.                                  | Plan     |
| Auth                    | JWT-only (reuse calendar deps), ignore ticket text  | Per-user endpoints are JWT-only since PUL-74; ticket's X-Client-Id is stale. | Research |

## Scope

**In scope:** new `get_portfolio_history` BQ fn; `/api/portfolio/history` endpoint; range→date
resolver; `PortfolioHistoryPoint` model; conftest mock; unit + endpoint + E2E tests.

**Out of scope:** `portfolio_snapshots` (rejected); `1d`/intraday; the frontend chart;
transaction-date tracking; touching the existing calendar path.

## Architecture / Approach

Bottom-up, two layers. Phase 1: a new BQ function generalizes the calendar CTE to
`[start_date, CURRENT_DATE()]`, returning `value_pln = Σ(shares×close)` and
`pnl_pln = value_pln − Σ(shares×avg_buy_price)` per trading day. Phase 2: clone the calendar
endpoint (JWT deps, 403 ownership guard, 300 s cache) with a range resolver and typed response,
plus the E2E mock. Reuses `_get_client()` — no new GCP client, no schema change.

## Phases at a Glance

| Phase        | What it delivers                                            | Key risk                                             |
| ------------ | ---------------------------------------------------------- | ---------------------------------------------------- |
| 1. BQ layer  | `get_portfolio_history(portfolio_id, user_id, start_date)` | SQL generalization correctness (P&L formula, ranges) |
| 2. API layer | endpoint + model + resolver + conftest mock + tests        | forgetting the conftest mock → E2E hits real client  |

**Prerequisites:** none — all dependencies (positions table, price tables, auth) exist.
**Estimated effort:** ~1 session across 2 phases (ticket est. ~4–6 h).

## Open Risks & Assumptions

- `1y` curve uses current share counts against historical closes — misleading for tranche
  buyers; accepted and documented (no tx dates stored to fix it).
- `pnl_pln` interpretation assumed to match the Designer's chart intent — confirm if the
  frontend expects a per-day delta instead.

## Success Criteria (Summary)

- Valid ranges return an ascending `{date, value_pln, pnl_pln}` trading-day series incl. ETFs.
- `1d`/garbage → 422; unowned portfolio → 403; no session → 401.
- Full suite + E2E green; prod curl confirms the contract after deploy.
