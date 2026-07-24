# PUL-91 — Kalendarz: second "Wszystkie" chart + dynamic titles — Plan Brief

> Full plan: `context/changes/pul-91-kalendarz-wszystkie-chart-dynamic-titles/plan.md`
> Research: `context/changes/pul-91-kalendarz-wszystkie-chart-dynamic-titles/research.md`

## What & Why

Under the Kalendarz view there is one value-over-time chart, scoped to the active portfolio
(PUL-89). PUL-91 adds a **second "Wszystkie" chart** (total value of all the user's
portfolios), **dynamic per-portfolio titles**, and a **single shared** range switcher + metric
toggle driving both — so a user comparing one wallet against their whole portfolio sees both
trends at once. When the active tab is already "Wszystkie", only one chart shows.

## Starting Point

`#pp-history-section` in `static/index.html` renders one inline-SVG chart via
`fetchPortfolioHistory` / `_renderPortfolioHistory` with single-slot state (`_ppHistData`,
one `_ppHistReqSeq`) and a static heading. The backend already supports the aggregate:
`GET /api/portfolio/history?portfolio_id=all` sums all wallets (PUL-90) — no backend work.

## Desired End State

With a specific portfolio active: two charts (active + "Wartość wszystkich portfeli w czasie"),
side-by-side on desktop, stacked on mobile, each with its own title. One range switch refetches
both; one metric toggle redraws both. With "Wszystkie" active: a single aggregate chart. Clean
empty/loading/error per chart, light+dark correct, no new deps, no console errors.

## Key Decisions Made

| Decision                 | Choice                                            | Why                                                         | Source   |
| ------------------------ | ------------------------------------------------- | ---------------------------------------------------------- | -------- |
| Aggregate data source    | Backend `portfolio_id=all` (existing)             | PUL-90 already sums wallets server-side with LOCF gate      | Research |
| all-mode duplicate       | Render ONE chart when active tab is "Wszystkie"   | Two identical aggregate charts would be redundant           | User     |
| Layout                   | Side-by-side desktop, stacked mobile (responsive) | Both trends visible on wide screens, readable on narrow     | Plan     |
| Metric toggle scope      | Drives BOTH charts                                 | Matches "one switcher controls both" model; predictable UX  | Plan     |
| Titles                   | Genitive map (distinct from nominative labels)    | "…portfela głównego w czasie" needs inflection              | Plan     |
| Out-of-order guard       | Per-slot seq counters                              | Two concurrent fetches per range switch (PUL-89 F1 lineage) | Plan     |

## Scope

**In scope:** two charts, dynamic genitive titles, shared range+metric controls, all-mode
single chart, responsive layout, e2e coverage, conftest all-mode fake fix.

**Out of scope:** backend/endpoint/BQ changes, "Wszystkie" positions table (separate ticket),
historical-price backfill (1Y stays partial), URL-schema changes.

## Architecture / Approach

Keep shared `_ppHistRange` + `_ppHistMetric`. Replace the single chart slot with two
(active, aggregate), each with its own cache, seq guard, container, and title element.
`_renderPortfolioHistory` gains a target-element param. A small orchestrator always fetches
the aggregate (`all`) and, unless in all-mode, also the active portfolio, rendering each slot
as it resolves and hiding the active block in all-mode. Wallets are stored in a new
`_ppPortfolios` so titles resolve from `portfolio_type`/`portfolio_name`.

## Phases at a Glance

| Phase                                   | What it delivers                                       | Key risk                                            |
| --------------------------------------- | ------------------------------------------------------ | --------------------------------------------------- |
| 1. Two charts + titles + all-mode       | Full feature in `static/index.html` (markup/CSS/JS)    | Two-fetch out-of-order desync; responsive squeeze   |
| 2. e2e coverage                         | conftest all-mode fake fix + Playwright tests          | Shared-conftest strict-mode collisions (two titles) |

**Prerequisites:** none — backend `portfolio_id=all` already live (PUL-90).
**Estimated effort:** ~1 session, 2 phases.

## Open Risks & Assumptions

- Two concurrent fetches per range switch must use per-slot seq guards, else a stale resolve
  desyncs a chart (this was PUL-89's F1 bug with a single series).
- Adding a second title heading may trip strict-mode locators in the shared e2e conftest —
  audit before adding fixtures (memory gotcha from PUL-90).
- 1Y range still returns a partial year (ingestion started ~mid-2026) — accepted, backfill
  is a separate ticket.

## Success Criteria (Summary)

- Specific portfolio → two correctly-titled charts; "Wszystkie" → one aggregate chart.
- One range switch and one metric toggle both act on both charts together.
- Existing pytest suite stays green; e2e covers two-charts / all-mode-single / shared switcher.
