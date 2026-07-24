---
change_id: pul-91-kalendarz-wszystkie-chart-dynamic-titles
title: Kalendarz — second "Wszystkie" value chart, dynamic per-portfolio titles, shared range switcher
status: impl_reviewed
created: 2026-07-24
updated: 2026-07-24
tracking:
  linear: PUL-91
  github: 181
---

## Notes

Extend the value-history chart under Kalendarz (built in PUL-89 / PUL-79) with:

1. A second **"Wszystkie"** chart = total value of all the user's portfolios over time,
   rendered next to the active portfolio's chart.
2. Dynamic chart titles (replace static "Wartość portfela w czasie"), using
   `_PORTFOLIO_TYPE_LABELS` + `portfolio_name` in correct Polish genitive:
   - główny → "Wartość portfela głównego w czasie"
   - IKZE → "Wartość portfela IKZE w czasie"
   - user-named → Wartość portfela "<nazwa>" w czasie
   - aggregate → "Wartość wszystkich portfeli w czasie"
3. A single shared range switcher (1T/1M/3M/1R → 1w|1m|3m|1y) driving BOTH charts.

**User constraint (2026-07-24):** if the active tab is already "Wszystkie" (the PUL-90
aggregate view), render only ONE chart (the aggregate) — no duplicate.

Backend: recommended `portfolio_id=all` mode already exists from PUL-90 (sentinel `all`
on `/api/portfolio/history` → server-side sum with LOCF forward-fill + full-coverage gate).
So this is likely front-end-only; confirm in /10x-research.

Refs: `#pp-history-section`, `fetchPortfolioHistory` / `_renderPortfolioHistory`,
`_ppHistRange`, `#pp-history-ranges`, `_PORTFOLIO_TYPE_LABELS` (static/index.html:3117);
endpoint `src/api.py:get_portfolio_value_history`, `db/bigquery.py:get_portfolio_history`.
