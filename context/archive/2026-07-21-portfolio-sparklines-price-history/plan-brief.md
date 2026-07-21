# Portfolio sparklines — price_history — Plan Brief

> Full plan: `context/changes/portfolio-sparklines-price-history/plan.md`
> Research: `context/changes/portfolio-sparklines-price-history/research.md`

## What & Why

Extend `GET /api/portfolio/positions` so each position carries `price_history: number[]` — the last 30 trading-session close prices (PLN, oldest first, no dates). The frontend sparkline is already built and rendered; it just has no data to draw. This is the backend slice (FARO-1 / PUL-78) that feeds it.

## Starting Point

`list_user_portfolio_positions` (`db/bigquery.py:641-703`) already looks up the *latest* close price ETF-safely — COALESCE across `company_daily_stats` + `etf_quotes` via `ROW_NUMBER()… rn=1`. The same function also backs the treemap. `PortfolioPositionOut` uses `extra="ignore"`, so a new field must be declared to survive.

## Desired End State

Every position from `/api/portfolio/positions` includes a `price_history` array (up to 30 ascending floats), correct for companies **and** ETFs, `null` when a ticker has no history. The treemap path is untouched and never pays for the aggregation. The already-built `_sparklineSvg` renders a trend line (or `"—"` for <2 points).

## Key Decisions Made

| Decision | Choice | Why | Source |
| --- | --- | --- | --- |
| Where to build history | Extend `list_user_portfolio_positions` | Its `current_price` COALESCE is the exact ETF-safe pattern to generalize | Research |
| Protect the shared treemap path | `include_history: bool = False` param | Only the positions endpoint opts in; treemap stays lean | Research |
| Window size | 30 sessions (`rn<=30`, 90-day floor) | Matches ticket + the 96px sparkline width | Plan |
| Empty history representation | `None` (null) | LEFT JOIN NULL flows naturally; frontend renders "—" either way | Plan |
| Dedup CTE | Keep (company-wins per ticker+date) | Parity with `current_price`; no latent double-count cliff | Plan |
| Gap handling | Rank DESC, take what exists | `company_daily_stats` has ~31% per-day gaps | Research |

## Scope

**In scope:** history CTEs in the BQ function; `price_history` field on `PortfolioPositionOut`; endpoint opts in; e2e fake + render assertion.

**Out of scope:** new endpoint, auth change, schema/migration, frontend change, treemap change, pagination/cache-TTL change.

## Architecture / Approach

One BQ function gains an opt-in 4-CTE history block (`hist_raw` union → `hist_dedup` company-wins → `hist_ranked` rn<=30 → `price_hist` ARRAY_AGG ASC), LEFT-JOINed on ticker. The model declares the field; the positions endpoint passes `include_history=True`; `PortfolioPositionOut(**row, …)` maps it automatically. Treemap call site stays on the default.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| --- | --- | --- |
| 1. Backend — history query + model field | `price_history` on the positions API, ETF-safe, opt-in | ETF union / ordering / dedup correctness in SQL |
| 2. E2E fake + render verification | fake accepts kwarg; e2e proves render vs "—" | e2e fake TypeError if kwarg missed |

**Prerequisites:** none — read-only over existing tables; frontend already shipped.
**Estimated effort:** ~1 session, 2 phases (~3-5h incl. tests).

## Open Risks & Assumptions

- Assumes ETF quotes stay fresh via the `etf_quotes` scheduler — stale quotes → ETF sparklines thin out (not an error).
- Assumes ≥30 sessions are reachable within the 90-day floor even with `company_daily_stats` gaps.

## Success Criteria (Summary)

- Positions API returns ascending `price_history` for companies and ETFs; `null` when no history.
- Treemap path unchanged (no history payload, no regression).
- Sparkline `<svg>` renders in-browser for tickers with history, `"—"` otherwise.
