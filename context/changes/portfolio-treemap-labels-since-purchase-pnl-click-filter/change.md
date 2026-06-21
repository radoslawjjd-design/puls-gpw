---
change_id: portfolio-treemap-labels-since-purchase-pnl-click-filter
title: Treemap D/D:/Total: labels, since-purchase P&L, hover highlight + click-to-filter
status: impl_reviewed
created: 2026-06-21
updated: 2026-06-21
archived_at: null
tracking:
  linear: PUL-51
  github: 77
---

## Notes

Follow-up to PUL-45/PUL-50 (shipped, archived). Two additions to the portfolio
treemap requested by the user during manual verification of the multi-wallet
treemap:

1. Label the existing figures (`D/D:` before daily change, `Total:` before the
   aggregate/share line) and add a new since-purchase profit/loss line (% and
   PLN) per position.
2. On hover, draw a bold border around the hovered cell (in addition to the
   existing tooltip). On click, navigate to the announcements table
   pre-filtered by that cell's ticker.

Key finding from scoping discussion: `pct` (cumulative % return since
purchase, parsed by Gemini from the XTB screenshot) is already persisted in
`positions_json` by the `portfolio-xpost` skill, but `compute_treemap_positions()`
(`src/portfolio_treemap.py`) currently drops it — it isn't read from input
positions or included in the output. `profit_abs` (PLN) is parsed by Gemini
but not persisted; decided to derive it mathematically from `pct` + `value`
(`cost = value / (1 + pct/100)`; `profit_abs = value - cost`, same inverse
formula as `portfolio_thread_composer.py::_cumulative_pct`) rather than touch
the ingestion skill — avoids a schema/ingestion change and backfill.

Decided as one ticket / one PR (not split into data vs. interaction) since
both touch the same view and are small individually.
