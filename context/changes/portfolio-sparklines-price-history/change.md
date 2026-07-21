---
change_id: portfolio-sparklines-price-history
title: Portfolio sparklines — price_history on /api/portfolio/positions
status: implementing
created: 2026-07-21
updated: 2026-07-21
archived_at: null
tracking:
  linear: PUL-78
  github: 137
---

## Notes

PUL-78 / GitHub #137 (FARO-1): extend `GET /api/portfolio/positions` with `price_history: number[]` per position — last ~30 trading sessions, close prices in PLN, ascending by date (oldest first), no dates. Frontend already renders it (`_sparklineSvg(pos.price_history)`; `null`/missing → "—").

Known gotchas (from the ticket + prior art):
- **ETFs**: history MUST come from a COALESCE/UNION of `company_daily_stats` AND `etf_quotes`, exactly like `current_price` in `list_user_portfolio_positions` (db/bigquery.py:604). Without it ETF/ETC/ETN sparklines silently render "—".
- One **batch** query for all portfolio tickers using `ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY snapshot_date DESC) <= 30` — not N queries.
- `company_daily_stats` has gaps (~31% of companies miss a given day) — sort by date and take what exists; don't assume 30 contiguous rows.
- Update e2e mocks in `tests/e2e/conftest.py` (`_fake_list_user_portfolio_positions`).
- Watch response payload size + the 30s positions cache.

Estimate: ~3–5h incl. tests. Needs codebase grounding (BQ query shapes, ETF union) → start with /10x-research.
