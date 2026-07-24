---
change_id: pul-90-wszystkie-aggregate
title: Add "Wszystkie" aggregate view in Mój portfel (all-portfolios positions + combined summary)
status: implementing
created: 2026-07-24
updated: 2026-07-24
archived_at: null
tracking:
  linear: PUL-90
  github: 180
---

## Notes

Add a default **"Wszystkie"** (All) option to the Mój portfel wallet selector that aggregates across every portfolio the user owns. Frontend-first; a small backend aggregate may be added per the design decision below.

- **Tabela**: positions from all the user's portfolios.
- **Summary** (`#pp-summary`): summed total value, combined P&L (zysk/strata), combined daily change.
- Read-only in "Wszystkie" (no add/edit/delete); editing stays on individual wallet tabs.
- "Wszystkie" shown first + selected by default on entry.

Design decisions to resolve in /10x-plan: (1) same ticker across portfolios → merge row (summed shares + weighted-avg buy price) vs per-portfolio rows; (2) client-side merge (N calls, no backend) vs backend `portfolio_id=all`; (3) read-only enforcement.

Out of scope: "Wszystkie" value-history chart in Kalendarz (companion), historical-price backfill.

References: `_renderPortfolioTabs` (static/index.html:3301), `_activePortfolioId`, `fetchUserPortfolios` (GET /api/portfolio/wallets), `fetchPortfolioPositions` (GET /api/portfolio/positions?portfolio_id=), `_updatePortfolioSummary` (static/index.html:3121), `#pp-summary` (~static/index.html:3517), `_PORTFOLIO_TYPE_LABELS` (static/index.html:3117). Builds on PUL-89 (#177) / PUL-79 (#138).
