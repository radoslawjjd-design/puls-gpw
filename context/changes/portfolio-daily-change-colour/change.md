---
change_id: portfolio-daily-change-colour
title: Colour the daily-change column in the portfolio table and mobile cards
status: new
created: 2026-08-04
updated: 2026-08-04
archived_at: null
tracking:
  linear: PUL-123
  github: null
---

## Notes

PUL-123 part 1 — colour the daily change column in the portfolio table (green
positive / red negative / neutral for zero and null), desktop table **and**
mobile cards.

Part 2 (holding period) is explicitly **out of scope** — it is blocked on
PUL-114, which owns the FIFO lot ledger and decides how `first_buy_date` /
`days_held` are exposed and how absence is represented.

From the ticket:

- `static/index.html:3839` renders **Zmiana dzienna** as plain text with no
  class; the neighbouring **Zysk/strata** column five lines below already does
  the wanted thing (`pnlClass = pos.pnl_pln > 0 ? 'positive' : ...`).
- The treemap already tints by daily change (`static/index.html:5812`) and
  prefixes positive values with `+` (`:5826`).
- `daily_change_pct` is `float | None` — `null` must stay the existing `—`,
  styled neutral. Zero and unknown are different states and must not collapse.
- Colour must not be the only signal — keep the sign on the number.

No GitHub issue exists for PUL-123 yet; `tracking.github` stays `null` rather
than opening one unasked.
