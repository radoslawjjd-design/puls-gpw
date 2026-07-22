---
change_id: pul-89-portfolio-value-history-frontend
title: FARO-5 frontend — portfolio value-history line chart + range switcher
status: impl_reviewed
created: 2026-07-22
updated: 2026-07-23
archived_at: null
tracking:
  linear: PUL-89
  github: 177
---

## Notes

4th "Wartość" tab in Mój portfel: inline-SVG line chart of value_pln over time + range switcher (1T/1M/3M/1R → 1w/1m/3m/1y) consuming GET /api/portfolio/history. Frontend-only, static/index.html.

Endpoint already live (backend PUL-79 / PR #176): `GET /api/portfolio/history?portfolio_id=<id>&range=1w|1m|3m|1y` → JSON array ascending by date `[{date, value_pln, pnl_pln}]`. JWT session cookie auth (no X-API-Key). Handle empty `[]` → empty state; 1y may return < full year. Clone calendar pattern (`fetchPortfolioCalendar`/`_renderPortfolioCalendar`), follow `_sparklineSvg` for SVG drawing. Polish strings, theme-aware, no new deps.
