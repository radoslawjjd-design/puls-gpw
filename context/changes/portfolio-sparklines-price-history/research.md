---
date: 2026-07-21T11:09:14+0200
researcher: Radek
git_commit: 3012e4d1375af6ceac01b9e04ab73b070b018a1a
branch: pul-78-portfolio-sparklines-price-history
repository: radoslawjjd-design/puls-gpw
topic: "PUL-78 / FARO-1 — price_history[] on /api/portfolio/positions for sparklines"
tags: [research, codebase, portfolio, bigquery, etf, sparkline]
status: complete
last_updated: 2026-07-21
last_updated_by: Radek
---

# Research: price_history[] for portfolio sparklines (PUL-78 / FARO-1)

**Date**: 2026-07-21T11:09:14+0200
**Researcher**: Radek
**Git Commit**: 3012e4d
**Branch**: pul-78-portfolio-sparklines-price-history
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

Extend `GET /api/portfolio/positions` so each position carries `price_history: number[]` — close prices from the last ~30 trading sessions, PLN, ascending by date (oldest first), no dates — so the already-built frontend sparkline renders. Do it without breaking ETFs, without N queries, and without bloating the treemap path that shares the same BQ function.

## Summary

Everything needed already exists as prior art. The exact ETF-safe price lookup pattern is `list_user_portfolio_positions` (`db/bigquery.py:641-703`), which COALESCEs the latest close from `company_daily_stats` **and** `etf_quotes` via `ROW_NUMBER() … rn=1`. For history we generalize `rn=1` → `rn<=30` and `ARRAY_AGG(… ORDER BY snapshot_date ASC)`. The close-price column in both tables is **`kurs_zamkniecia`**; the date column is **`snapshot_date`** (DATE).

The one real design decision: the same BQ function is also called by the **treemap** path (`src/api.py:801`), which must NOT pay for the history aggregation. Gate it behind an `include_history: bool = False` parameter (conditional SQL, exactly like the existing `portfolio_filter` branch) — only the positions endpoint passes `True`.

The frontend contract is forgiving: `_sparklineSvg` (`static/index.html:2969`) returns `"—"` when the value is not an array **or** has `length < 2`. So `None`, `[]`, and single-element arrays are all safe — the sparkline only draws at ≥2 points.

## Detailed Findings

### The endpoint — `GET /api/portfolio/positions`

`src/api.py:637-672`.
- Deps: `_get_role` + `_get_user_id` (per-user; **not** admin-gated — this is the user's own portfolio, no score/sentiment involved, so no role stripping needed).
- 404s if `portfolio_id` isn't one of the user's wallets.
- Per-user **30 s cache**: `positions:{user_id}:{portfolio_id}` (`_perf_get(..., ttl=30)`).
- Builds each row via `PortfolioPositionOut(**row, pnl_pln=…, pnl_pct=…).model_dump()` (line 670).

### The response model — `PortfolioPositionOut`

`src/api.py:270-280`. `model_config = ConfigDict(extra="ignore")`.
- Fields: ticker, company_name, shares, avg_buy_price, current_price?, daily_change_pct?, pnl_pln?, pnl_pct?, price_as_of?.
- **`extra="ignore"`** means a `price_history` key in `row` is dropped **unless** we add the field to the model. Add: `price_history: list[float] | None = None`. Because the endpoint does `PortfolioPositionOut(**row, …)`, once the BQ row dict carries `price_history`, it maps automatically — no call-site change beyond the model field.

### The BQ function to extend — `list_user_portfolio_positions`

`db/bigquery.py:641-703`. Current shape (the pattern to mirror):
- Two CTEs `latest_stats` (from `company_daily_stats`) and `latest_etf` (from `etf_quotes`), each `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC)`, filtered to `snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY)`.
- Main SELECT `LEFT JOIN`s both on `rn = 1` and COALESCEs: `COALESCE(ls.kurs_zamkniecia, etf.kurs_zamkniecia) AS current_price` (`:681`), same for `daily_change_pct` (`zmiana_procentowa`) and `price_as_of`.
- Returns `[dict(row) for row in rows]` (`:703`).

**Close price column = `kurs_zamkniecia`; change % = `zmiana_procentowa`; date = `snapshot_date` (DATE).** Confirmed identical in both tables (`:658-660`, `:668-670`).

Table-name constants: `_COMPANY_DAILY_STATS_TABLE_NAME` = "company_daily_stats" (`:2168`), `_ETF_QUOTES_TABLE_NAME` = "etf_quotes" (`:2368`), `_USER_PORTFOLIO_POSITIONS_TABLE_NAME` = "user_portfolio_positions" (`:528`).

### Prior art for a multi-day UNION — `get_portfolio_calendar_data`

`db/bigquery.py:~372-450`. Builds a per-`snapshot_date` series across a window by joining positions to `COALESCE(cds.kurs_zamkniecia, etq.kurs_zamkniecia)` per day (`:410-417`). Confirms the multi-day COALESCE/UNION shape works against the real schema; our per-ticker `ARRAY_AGG` is a sibling of it.

### The shared consumer to protect — treemap

`src/api.py:801`: `all_rows = list_user_portfolio_positions(user_id)` (no `portfolio_id`). Feeds `compute_treemap_positions` / `compute_user_portfolio_treemap_positions`. These read specific keys, so an extra `price_history` key is functionally harmless — but the **ARRAY_AGG cost + payload** would be paid for nothing. Hence the `include_history=False` default; the treemap call stays lean, the positions endpoint opts in.

### Frontend contract — `_sparklineSvg`

`static/index.html:2969-2977`, rendered at `:3006` (`<td data-label="30 dni">${_sparklineSvg(pos.price_history)}</td>`).
- `if (!Array.isArray(hist) || hist.length < 2) return '—';`
- Draws a polyline over `hist`; colour green if `hist[last] >= hist[0]`, else red. **No dates consumed** — a bare number array is the whole contract. Oldest→newest ordering matters for the colour + left-to-right slope.

### E2E mock to update — `_fake_list_user_portfolio_positions`

`tests/e2e/conftest.py:357` (patched at `:505`). Signature `(user_id, portfolio_id=None)`.
- **Must accept the new kwarg** — add `include_history=False` — or the endpoint's `list_user_portfolio_positions(user_id, portfolio_id, include_history=True)` will raise `TypeError` against the mock.
- `_FAKE_PORTFOLIO_POSITIONS` (`:~283`) should gain a `price_history` array on at least one position so an E2E can assert the sparkline `<svg>` renders, and one without it to assert the `"—"` fallback.

## Code References

- `src/api.py:637-672` — positions endpoint (cache, 404, row→model loop)
- `src/api.py:270-280` — `PortfolioPositionOut` (add `price_history: list[float] | None = None`)
- `src/api.py:801` — treemap reuse of the same BQ fn (protect with `include_history`)
- `db/bigquery.py:641-703` — `list_user_portfolio_positions` (extend here)
- `db/bigquery.py:681-683` — the COALESCE(company, etf) current_price pattern to generalize
- `db/bigquery.py:372-450` — `get_portfolio_calendar_data`, multi-day UNION prior art
- `static/index.html:2969-2977, 3006` — `_sparklineSvg` contract + render site
- `tests/e2e/conftest.py:357, 505` — fake positions + patch site

## Architecture Insights

- **ETF trap is real and known**: without unioning `etf_quotes`, ETF/ETC/ETN sparklines silently render "—" (same class of bug as the pre-scheduler ETF price outage — see [[project-etf-quotes-scheduler]]).
- **`company_daily_stats` has gaps** (~31% of tickers miss a given day — [[project-company-daily-stats-query-pattern]]). Do NOT assume 30 contiguous rows: rank by `snapshot_date DESC`, keep `rn<=30`, and take whatever exists. Use a generous date floor (≈ `INTERVAL 90 DAY`) to guarantee ≥30 sessions are reachable while still bounding the scan (30 sessions ≈ 6 calendar weeks; 90 days is a safe margin even with gaps).
- **Dedup for source overlap**: `current_price` COALESCEs company-first, implying a ticker could in principle appear in both tables. For the array, UNION ALL both sources then keep company-wins per `(ticker, snapshot_date)` via `QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src)=1` (src: company=0, etf=1). In practice a ticker lives in exactly one table, but this keeps parity with `current_price` semantics at ~zero cost.
- **Filter NULL closes** (`kurs_zamkniecia IS NOT NULL`) before aggregating so the array is clean numbers (frontend does no null-skipping inside the array).
- **Payload/cache**: 30 floats × N positions (~10-30 positions → a few KB). Comfortably inside the 30 s positions cache; no pagination needed. Still, `include_history` keeps the treemap path free of it.

### Recommended query shape (for the plan)

```sql
WITH hist_raw AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia, 0 AS src
  FROM `company_daily_stats`
  WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND kurs_zamkniecia IS NOT NULL
  UNION ALL
  SELECT ticker, snapshot_date, kurs_zamkniecia, 1 AS src
  FROM `etf_quotes`
  WHERE snapshot_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 90 DAY) AND kurs_zamkniecia IS NOT NULL
),
hist_dedup AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia
  FROM hist_raw
  QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker, snapshot_date ORDER BY src) = 1
),
hist_ranked AS (
  SELECT ticker, snapshot_date, kurs_zamkniecia,
         ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) AS rn
  FROM hist_dedup
),
price_hist AS (
  SELECT ticker, ARRAY_AGG(kurs_zamkniecia ORDER BY snapshot_date ASC) AS price_history
  FROM hist_ranked WHERE rn <= 30 GROUP BY ticker
)
-- … main SELECT … LEFT JOIN price_hist ph ON p.ticker = ph.ticker  → ph.price_history
```

Only emitted when `include_history=True`; otherwise the current query is unchanged.

## Historical Context (from prior changes)

- `context/archive/2026-06-22-my-wallet-watchlist/` — introduced the my-wallet/positions plumbing.
- ETF portfolio support (PUL-67) established the `etf_quotes` union for prices; the scheduler ([[project-etf-quotes-scheduler]]) keeps ETF quotes fresh — the sparkline depends on that freshness for ETFs.
- [[project-company-daily-stats-query-pattern]] — the ROW_NUMBER/gaps rule this change re-applies.

## Related Research

- None prior for sparklines specifically; this is the first FARO-v2 backend slice touched.

## Open Questions

1. **Window size**: ticket says "~30 trading sessions". `rn<=30` with a 90-day floor. Confirm 30 (not 20/60) is what the Designer's sparkline width (96px) is tuned for — low stakes, easy to change.
2. **Empty history representation**: return `None` vs `[]` when a ticker has no rows. Both render "—". Lean toward `None` (LEFT JOIN NULL flows through naturally); decide in the plan.
3. **Dedup CTE**: keep it for `current_price` parity, or drop it as YAGNI (a ticker is only ever in one table)? Recommend keep — negligible cost, avoids a latent correctness cliff.
