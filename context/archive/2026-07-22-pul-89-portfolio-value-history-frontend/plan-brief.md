# FARO-5 Frontend — Value-History Chart — Plan Brief

> Full plan: `context/changes/pul-89-portfolio-value-history-frontend/plan.md`
> Research: `context/changes/pul-89-portfolio-value-history-frontend/research.md`

## What & Why

Add a 4th "Wartość" tab to the Mój portfel view showing an inline-SVG line chart of the
portfolio's total value over time. The FARO-5 backend endpoint (`GET /api/portfolio/history`)
shipped in PUL-79 / PR #176; PUL-79's spec deferred the chart to the Designer — this is that
work. Frontend-only, single file, no new deps.

## Starting Point

`static/index.html` has three portfolio view tabs (Tabela | Treemapa | Kalendarz) that share
one pattern: a `pp-*-wrap` container, a module cache var, lazy fetch on first activation, and
a DOM-building render fn. The calendar view is the closest sibling to clone; `_sparklineSvg`
already draws an inline SVG polyline (green/red by direction) we reuse for the chart math.

## Desired End State

A "Wartość" tab renders a value-over-time line for the active wallet (default 3M). A range
switcher (1T/1M/3M/1R) refetches+redraws; a Wartość↔Zysk/strata toggle redraws from cache.
Empty range → "Brak danych dla tego zakresu". `?tab=history&range=1m` deep-links restore.
Light + dark correct, no console errors, no new dependencies.

## Key Decisions Made

| Decision                   | Choice                                            | Why (1 sentence)                                                              | Source   |
| -------------------------- | ------------------------------------------------- | ---------------------------------------------------------------------------- | -------- |
| v1 scope                   | Chart + range switcher + Y min/max + P&L toggle   | `pnl_pln` is already in the payload, so the toggle is cheap; tooltip deferred | Plan     |
| Auth on the fetch          | Clone calendar verbatim — keep `X-API-Key` header | Every portfolio fetch sends it; cookie is the real auth; matching is safest   | Research |
| URL state                  | Persist `?range=` only; metric resets to Wartość  | Consistent with `?tab=`/`?portfolio=`; useful share-link without bloat        | Plan     |
| Chart tech                 | Hand-rolled inline SVG (`_sparklineSvg` math)     | App has no chart library; inline SVG is the established pattern               | Research |
| Phasing                    | 2 phases (scaffold → render)                      | Clean manual-verify checkpoint between plumbing and drawing                   | Plan     |

## Scope

**In scope:** 4th tab; range switcher (1T/1M/3M/1R, default 3M); value/P&L toggle; SVG line
chart; Y min/max + X date labels; loading/empty/error states; `?range=` persistence; wallet-
switch refetch; light+dark theming.

**Out of scope:** any backend change; `1d`/intraday; hover tooltip; new chart library;
persisting the metric toggle in the URL.

## Architecture / Approach

Clone the calendar view end-to-end in `static/index.html`, swapping the calendar-grid
renderer for an SVG line-chart renderer. `fetchPortfolioHistory` (clone of
`fetchPortfolioCalendar`) fetches + caches into `_ppHistData`; `_renderPortfolioHistory` draws
from cache using `_ppHistMetric`. Range clicks refetch; metric toggle redraws from cache. Tab
+ range persist through the existing `_ppPortfolioParams`/URL-reader plumbing.

## Phases at a Glance

| Phase             | What it delivers                                          | Key risk                                             |
| ----------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| 1. Scaffold       | Tab + wrap + toggle branch + module state + `?range=`     | URL range must be set before the restore-click fires |
| 2. Chart render   | fetch + SVG render + range/metric wiring + states         | Toggle must redraw from cache (not refetch)          |

**Prerequisites:** endpoint live (done); a logged-in session with at least one wallet holding covered days.
**Estimated effort:** ~1 session across 2 phases.

## Open Risks & Assumptions

- Assumes the endpoint's empty-`[]` contract (no fully-covered day) is the only empty path — handled by the empty state.
- `1y` may return < 1 year of points by design (short price history) — chart plots what's returned, no fixed point-count assumption.
- Assumes same-origin session cookie authenticates the fetch (as it does for calendar/positions).

## Success Criteria (Summary)

- Wartość tab renders a value line for both wallets; range switcher refetches; metric toggle redraws instantly.
- Empty range → empty state, not a broken chart; light + dark correct; no console errors; no new deps.
- `?tab=history&range=<r>` deep-link restores tab + range.
