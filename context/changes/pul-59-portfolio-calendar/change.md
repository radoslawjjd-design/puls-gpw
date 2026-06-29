---
change_id: pul-59-portfolio-calendar
title: Monthly P&L calendar view in Mój portfel (green/red days)
status: impl_reviewed
created: 2026-06-29
updated: 2026-06-29
archived_at: null
tracking:
  linear: PUL-59
  github: null
---

## Notes

Monthly calendar grid in the "Mój portfel" section (alongside Tabela and Treemapa tabs). Each cell shows the day's portfolio P&L in PLN, coloured green (gain) / red (loss) / neutral-grey (no snapshot). Month navigation prev/next. Backend: new endpoint returning per-day P&L for a given (year, month) derived from `portfolio_snapshots`. User-facing (non-admin), combined wallet view or per-wallet TBD at plan stage.
