---
change_id: newconnect-raw-closes
title: Repair NewConnect historical closes from stooq's unadjusted series
status: implemented
created: 2026-08-05
updated: 2026-08-05
archived_at: null
tracking:
  linear: PUL-96
  github: 191
---

## Notes

PUL-98 closed GH #191 claiming the dividend-adjustment defect was fixed "across the
whole visible surface". Measurably untrue: the GPW archive it corrected from covers
only the main market, so every NewConnect name kept the adjusted series PUL-92
backfilled from `d_pl_txt`.

The ticket listed three options and judged raw NewConnect history unobtainable. A
fourth exists and was verified live: stooq's `o=` bitmask disables the adjustments
(`o=1111111` = skip splits, dividends, rights, denominations). See `research.md`.
