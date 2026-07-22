---
date: 2026-07-22T21:45:00+0200
researcher: Radek
git_commit: bebad43fcb9bd7d86dd00ab2dfb72e173c54cb0e
branch: master
repository: radoslawjjd-design/puls-gpw
topic: "PUL-79 / FARO-5 — GET /api/portfolio/history value-history endpoint (source selection + contract)"
tags: [research, codebase, portfolio, bigquery, faro, history, value-curve]
status: complete
last_updated: 2026-07-22
last_updated_by: Radek
---

# Research: Portfolio value-history endpoint (PUL-79 / FARO-5)

**Date**: 2026-07-22T21:45:00+0200
**Researcher**: Radek
**Git Commit**: bebad43
**Branch**: master
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

Build `GET /api/portfolio/history?portfolio_id=…&range=1w|1m|3m|1y` →
`[{date: 'YYYY-MM-DD', value_pln: number, pnl_pln: number}]`, trading days only.
The ticket names **two candidate data sources** — the compute-on-the-fly
`get_portfolio_calendar_data`, and the precomputed `portfolio_snapshots` table —
and asks research to pick one after verifying the snapshot table's real coverage.

## Summary

**Verdict: compute-on-the-fly is the only viable source. The `portfolio_snapshots`
table is a dead end for this endpoint** — verified against live BigQuery, not just
the schema.

`portfolio_snapshots` is the **admin/owner treemap's** data, populated ad-hoc by the
`portfolio-xpost` skill from XTB broker screenshots. Live coverage:

| wallet | rows | first_day | last_day | distinct_days |
|--------|------|-----------|----------|---------------|
| main   | 7    | 2026-06-17 | 2026-06-26 | 7 |
| ikze   | 7    | 2026-06-17 | 2026-06-26 | 7 |

It is keyed by **`wallet` (`"main"`/`"ikze"`), not `user_id`/`portfolio_id`**; it has
**no automated writer** in the app tree (only tests + the manual xpost skill write it);
it holds **7 days total** and is **frozen ~a month stale** (last row 2026-06-26, today
2026-07-22). It cannot serve a per-user `portfolio_id`-scoped series. Do not use it.

The correct source is a **generalization of `get_portfolio_calendar_data`**
(`db/bigquery.py:362`), which already computes per-trading-day portfolio value from the
real per-user table (`user_portfolio_positions`) × historical close, unioning ETFs via
`COALESCE(company_daily_stats, etf_quotes)`. Live per-user data: 1 user, 2 portfolios,
18 position rows — exactly what the endpoint targets. Generalize its fixed month+lookback
window to an arbitrary `[start_date, CURRENT_DATE]` range.

The endpoint itself is a near-verbatim clone of `GET /api/portfolio/calendar`
(`src/api.py:880`): same deps, same wallet-ownership guard, same cache pattern.

## Detailed Findings

### Source A (CHOSEN) — generalize `get_portfolio_calendar_data`

`db/bigquery.py:362-457`. Returns one dict per trading day with `snapshot_date`,
`portfolio_value`, `daily_change_pln`, `prices_found`, `total_positions`.

Query shape (the pattern to generalize):
- `trading_days` = `SELECT DISTINCT snapshot_date FROM company_daily_stats WHERE snapshot_date BETWEEN @start AND @end` → **trading-days-only for free** (only days the market emitted stats appear). ✅ satisfies the ticket's "trading days only".
- `positions` = current `shares` per ticker for `(user_id, portfolio_id)`.
- `daily_prices` = `trading_days CROSS JOIN positions LEFT JOIN company_daily_stats LEFT JOIN etf_quotes`, with `COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia) AS close_price`. ✅ **ETF-safe already** — the etf_quotes union is built in.
- `daily_portfolio` = `SUM(shares * close_price)` grouped by day.

**Generalization needed**: replace the `year, month` signature + `month_start`/`monthrange`
window (`:384-387`) with `start_date`/`end_date` params. `end_date = CURRENT_DATE()`;
`start_date = DATE_SUB(CURRENT_DATE(), INTERVAL …)` per range. Everything else transfers.

⚠️ **Docstring/impl mismatch to know**: docstring says "35-day lookback" but
`lookback_start = month_start` (`:385`) — there is **no** actual lookback; `compute_calendar_pnl`
uses `zmiana_kwotowa` deltas, not cross-day value diffs. For history we don't need the
lookback at all; just scan `[start, today]`.

### The value approximation (product decision to record)

`user_portfolio_positions` stores only `shares` + `avg_buy_price` — **no transaction
dates** (`_USER_PORTFOLIO_POSITIONS_SCHEMA`, `db/bigquery.py:530-535`). So "value on day X"
= **today's** share count × that day's close. Over `1y` this pretends the current holdings
were always held (tranche buys are invisible). Same caveat the ticket flags. Options for the
plan: (a) accept + document in the response/UI, or (b) descope `1y`. The snapshots table would
have avoided this but is unusable (above).

### `pnl_pln` — OPEN semantic decision

Ticket wants `pnl_pln` per point but doesn't define it. Two readings:
- **Cumulative unrealized P&L** = `value_pln − SUM(shares × avg_buy_price)` (cost basis is a
  constant baseline; curve = value curve shifted down). Most natural for a "P&L over time" line.
- **Daily change** = `SUM(shares × zmiana_kwotowa)` — this is what `get_portfolio_calendar_data`
  already returns as `daily_change_pln`.

Recommend **cumulative unrealized P&L** (matches a value-history chart's intent and reuses
`avg_buy_price` already in the positions CTE). Plan should confirm with the Designer's spec.

### Source B (REJECTED) — `portfolio_snapshots`

Schema `db/bigquery.py:194-206`; writer `save_portfolio_snapshot:231`; readers
`get_latest_snapshot_before:278` and `get_latest_snapshot_for_wallet` (used by
`/admin/portfolio/treemap`, `src/api.py:626-661`). Keyed by `wallet` + `snapshot_date`.
No non-test caller of `save_portfolio_snapshot` in app code — only the manual
`portfolio-xpost` skill (`.claude/skills/portfolio-xpost/SKILL.md`) writes it from broker
screenshots. Live coverage table above proves it's owner-only, sparse, and stale. **Reject.**

### The endpoint to clone — `GET /api/portfolio/calendar`

`src/api.py:880-912`. Copy this structure for `/api/portfolio/history`:
- Deps: `role: Role = Depends(_get_role)`, `user_id: str = Depends(_get_user_id)`.
- Validate the range param (422 on bad value — mirror the calendar's month/year 422s).
  **`1d` must be rejected 422** (ticket: intraday not stored).
- Wallet-ownership guard: `list_user_portfolios(user_id)` then
  `if not any(w["portfolio_id"] == portfolio_id …): raise HTTPException(403, …)`.
  ⚠️ Inconsistency to pick a side on: **calendar uses 403**, positions
  (`src/api.py:695`) uses **404** for the same "wallet not yours" case. Match calendar (403)
  since it's the closest sibling, unless the Designer's client expects 404.
- Cache: `f"history:{user_id}:{portfolio_id}:{range}"` via `_perf_get(..., ttl=300)` /
  `_perf_set` (calendar uses 300 s; positions uses 30 s — 300 s is fine, history moves once/day).
- Wrap BQ call in try/except `BigQueryError` → 500.

### Auth — ticket text is STALE, follow the code

Ticket says "Same auth headers (`X-API-Key` + `X-Client-Id`)". **Not true for per-user
endpoints anymore.** Per PUL-74 (`src/api.py:153-159`), `_get_user_id` is **JWT-only** —
identity comes from the signed session cookie; the anonymous `X-Client-Id` path is retired.
`_get_role` (`:127-144`) accepts a JWT cookie OR `X-API-Key`. Just reuse the two calendar
deps; do not add any `X-Client-Id` handling.

### Response model

No `PortfolioHistory*` model exists yet — add one next to `PortfolioCalendarResponse`
(the calendar response model) and `PortfolioPositionOut` (`src/api.py:270`). Shape:
`list[PortfolioHistoryPoint]` where `PortfolioHistoryPoint = {date: str (ISO), value_pln:
float, pnl_pln: float}`. Serialize `snapshot_date.isoformat()` → `date`.

### E2E mock (required — new BQ fn must be patched)

`tests/e2e/conftest.py`. Mirror `_fake_get_portfolio_calendar_data` (`:346-349`) and its
patch registration (`:552-555`). Add a `_fake_get_portfolio_history` returning a small
ascending series for `_FAKE_PORTFOLIO_ID` and `[]` otherwise, then
`patch("src.api.get_portfolio_history", side_effect=_fake_get_portfolio_history)` in the
`live_server_url` patch stack. **Skipping this makes the endpoint raise against a real BQ
client in E2E** — see [[feedback-e2e-conftest-bq-mocking]].

## Code References

- `db/bigquery.py:362-457` — `get_portfolio_calendar_data` (generalize to range)
- `db/bigquery.py:384-387` — the fixed month window to replace with start/end params
- `db/bigquery.py:410-411` — `COALESCE(cds, etq)` ETF-safe close + change (reuse)
- `db/bigquery.py:530-535` — positions schema (shares + avg_buy_price, no tx dates)
- `db/bigquery.py:787` — `list_user_portfolios` (wallet-ownership guard)
- `db/bigquery.py:194-206, 231, 278` — `portfolio_snapshots` (rejected source)
- `src/api.py:880-912` — `/api/portfolio/calendar` endpoint (clone this)
- `src/api.py:695` vs `:903` — 404 vs 403 wallet-not-found inconsistency
- `src/api.py:127-159` — `_get_role` / `_get_user_id` (JWT-only per-user auth)
- `tests/e2e/conftest.py:346-349, 552-555` — calendar mock + patch to mirror

## Architecture Insights

- **Two "portfolio value over time" systems that must not be confused**: the owner's
  `portfolio_snapshots` (wallet-keyed, broker-screenshot, admin treemap) vs the per-user
  `user_portfolio_positions` compute-on-the-fly (calendar, positions, and now history).
  FARO-5 lives entirely in the second.
- **Trading-days-only is a property of the `company_daily_stats` calendar**, obtained by
  driving the series off `SELECT DISTINCT snapshot_date`, not by generating a date range.
- **ETF trap is already handled** in the calendar query — do not drop the `etf_quotes`
  union when generalizing ([[project-etf-quotes-scheduler]]).
- **`company_daily_stats` has ~31% per-day gaps** ([[project-company-daily-stats-query-pattern]]);
  the `LEFT JOIN` + `COUNTIF(close_price IS NOT NULL)` already tolerates this — a day with
  partial prices still yields a (best-effort) point.
- **BQ facts** (verified live this session): project `puls-gpw`, dataset `espi_ebi`
  (default `oswiadczenia_gpw` is gone — every `bq` call needs `--project_id=puls-gpw`).
  See [[db-bigquery]] rule: `load_dotenv()` before `db.*`, `with_quota_project` guard on new
  clients (no new client here — reuse `_get_client()`).

## Historical Context (from prior changes)

- `context/archive/2026-07-21-portfolio-sparklines-price-history/research.md` — PUL-78
  established the exact ETF-safe COALESCE pattern and the `include_history` gating idea; the
  history query here is a whole-portfolio sibling of that per-position array.
- `context/archive/2026-06-29-pul-59-portfolio-calendar/` — origin of
  `get_portfolio_calendar_data` and `compute_calendar_pnl`; the function we generalize.
- `context/archive/2026-06-17-portfolio-xpost-skill/plan.md` — the sole writer path for
  `portfolio_snapshots` (broker screenshots), confirming it's owner-only.

## Related Research

- `context/archive/2026-07-21-portfolio-sparklines-price-history/research.md`

## Open Questions

1. **`pnl_pln` semantics** — cumulative unrealized (recommended) vs daily change? Confirm
   against the Designer's line-chart spec.
2. **`1y` tranche approximation** — accept + document, or descope? (Recommend accept +
   document; the snapshot alternative that would have fixed it is unusable.)
3. **Wallet-not-found status** — 403 (match calendar) vs 404 (match positions)? Pick one;
   confirm what the Designer's client branches on.
4. **`1w` = ~5 points** is intended per ticket — confirm the frontend renders a 5-point line
   acceptably (a Designer-side concern, out of this backend's scope).
