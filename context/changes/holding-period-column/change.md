---
change_id: holding-period-column
title: Show how long each position has been held, in the table and in Zrealizowane
status: impl_reviewed
created: 2026-08-05
updated: 2026-08-05
archived_at: null
tracking:
  linear: PUL-123
  github: null
---

## Notes

Part 2 of PUL-123. Part 1 (colouring the daily change) shipped 2026-08-04 in PR #246 and
is archived at `context/archive/portfolio-daily-change-colour/`.

**PUL-114 owns the data; this change owns the rendering.** Both numbers already exist on
the endpoints — `first_buy_date` on `/api/portfolio/positions` (the oldest *open* FIFO
lot) and `days_held_weighted` / `days_held_max` on `/api/portfolio/realized`. There is no
lot arithmetic to do here, and the ticket says so explicitly: do not build a second
ledger.

### Decisions taken with the owner before any code (2026-08-05)

1. **Format: days below 90, years and months above.** `47 dni` / `3 mies.` /
   `1 rok 2 mies.`. The ticket's own suggestion, and it fits the real data — live
   positions run from 15 days to 424. Same format in both views.
2. **Zrealizowane shows the volume-weighted figure**, with the oldest consumed lot in the
   cell's `title`. Weighted is the answer to "how long did I hold this"; the oldest-lot
   number is what the ticket's acceptance criterion asks for, and it stays checkable.
3. **The CSV export gains the column.** An export that omits a column the table shows is
   the bigger surprise, and holding period is exactly the sort of thing a spreadsheet is
   for.

### Absence is not zero

A hand-entered position has no operations, so `first_buy_date` is `null` and there is no
holding period. It renders as the existing neutral `—`, never `0 dni` and never today.
The ticket names this as the thing not to paper over; PUL-114 already reports absence
honestly, so this change only has to not invent something.

### Found while reading the code

`_sortRows` (`static/index.html:2496`) needs **no change**. ISO dates compare
lexicographically in chronological order, and it already pushes `null` to the end
regardless of sort direction — so the new column sorts correctly by raw date, which is
what a two-format cell requires.
