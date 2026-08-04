# Colour the daily-change column — Plan Brief

> Full plan: `context/changes/portfolio-daily-change-colour/plan.md`

## What & Why

The **Zmiana dzienna** column in the portfolio table is the only signed number in
that table rendered without colour. The **Zysk/strata** cell five lines below it in
the same `.map()` callback already tints green/red, and the treemap already tints by
the very same field. This is an inconsistency inside one table, not a missing
capability.

## Starting Point

`_renderPortfolioTable` (`static/index.html:3818`) computes `daily` as plain text
(`:3839`) and `pnlText`/`pnlClass` as a text/class pair (`:3840-3845`). The cells
land at `:3860` and `:3861` — only the second carries a class.

## Desired End State

A position up on the day shows `+1.50%` in green, one down shows `-0.80%` in red,
a flat one shows a neutral `0.00%`, and one with no price data still shows a
neutral `—`. True on desktop and phone, in light and dark themes.

## Key Decisions Made

| Decision | Choice | Why |
| -------- | ------ | --- |
| Zero vs unknown | `0.00%` neutral vs `—` neutral — different text, same styling | They are different states; the ticket forbids collapsing them |
| Second signal beyond colour | `+` prefix on positives | Negatives already carry `-`; matches the treemap at `:5826` |
| Mobile card handling | No separate work | The card layout restyles the same `<td>`; `td.positive` applies at every viewport |
| Dark mode | No new CSS | `html[data-theme="dark"] td.positive/negative` already exist at `:1187-1190` |
| Test style | Luminance comparison, not hex assertions | Mirrors `test_portfolio_mobile_layout.py`; survives a palette change |

## Scope

**In scope:** the daily-change cell in the portfolio table (desktop + mobile cards),
two e2e fixture rows covering negative and zero, e2e coverage of all four states.

**Out of scope:** PUL-123 part 2 (holding period — blocked on PUL-114), the treemap,
calendar, dividends, realized views, CSV export, sorting, the API payload, new CSS.

## Architecture / Approach

Mirror the `pnlText`/`pnlClass` pair that already sits five lines below, and apply
the resulting class to the `<td>` exactly as the neighbouring cell does. No CSS, no
backend, no new component.

## Phases at a Glance

| Phase | What it delivers | Key risk |
| ----- | ---------------- | -------- |
| 1. Colour and sign the cell | Coloured, signed daily change + e2e coverage of all four states | Extending the shared e2e fixture could disturb other portfolio tests — checked: none assert row counts or totals |

**Prerequisites:** none.
**Estimated effort:** one session, single phase.

## Open Risks & Assumptions

- Assumes no e2e test depends on the exact contents of `_FAKE_PORTFOLIO_POSITIONS`
  beyond the `PKO` and `CDR` rows. Verified by grep across the portfolio e2e files,
  but the full suite run in 1.2 is the real check.

## Success Criteria (Summary)

- All four daily-change states are visually distinct and correctly signed
- Nothing else in the portfolio views changes
- Full suite and lint green
