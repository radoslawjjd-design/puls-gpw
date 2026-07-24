---
date: 2026-07-24T13:35:36+02:00
researcher: Radek
git_commit: f05bdf04a31c626d0eb98d1a84a296990f99fd27
branch: master
repository: puls-gpw
topic: "PUL-91 — second \"Wszystkie\" value chart, dynamic per-portfolio titles, shared range switcher under Kalendarz"
tags: [research, codebase, portfolio-history, chart, static-index-html, faro-5]
status: complete
last_updated: 2026-07-24
last_updated_by: Radek
---

# Research: PUL-91 — second "Wszystkie" value chart + dynamic titles + shared range switcher

**Date**: 2026-07-24T13:35:36+02:00
**Researcher**: Radek
**Git Commit**: f05bdf04a31c626d0eb98d1a84a296990f99fd27
**Branch**: master
**Repository**: puls-gpw

## Research Question

For PUL-91 (extend the value-history chart under Kalendarz with a second
"Wszystkie" chart, dynamic per-portfolio titles, and a single range switcher
driving both charts — and, per user, render only ONE chart when the active tab
is already "Wszystkie"): what exists today, what does the backend already give
us, and what has to change on the frontend?

## Summary

- **Backend is already done.** PUL-90 added the `portfolio_id=all` sentinel to
  `GET /api/portfolio/history`. In all-mode the endpoint skips the ownership
  check and calls `get_portfolio_history(None, user_id, start_date)`, which sums
  across all the user's wallets with the same LOCF forward-fill + full-coverage
  gate as the per-portfolio path. **No backend change is required** — the
  aggregate series is one fetch away (`portfolio_id=all`). (`src/api.py:1008-1018`)
- **This is a front-end-only change** in `static/index.html`, localized to the
  `#pp-history-section` block (chart markup, `fetchPortfolioHistory`,
  `_renderPortfolioHistory`, the range switcher, and the module-level history
  state vars).
- **Three concrete gaps to close in the plan:**
  1. The wallets list (`portfolio_type` / `portfolio_name`, needed for dynamic
     titles) is **not stored globally** — `fetchUserPortfolios` uses it locally.
     A module-level `_ppPortfolios` (or similar) must hold it.
  2. Single `#pp-history-chart` + single-slot state (`_ppHistData`, one
     `_ppHistReqSeq`) must become **two chart slots** (active + aggregate) with
     independent data caches and out-of-order guards.
  3. The e2e conftest fake (`_fake_get_portfolio_history`) returns `[]` for any
     `portfolio_id != _FAKE_PORTFOLIO_ID`, i.e. `[]` for all-mode's `None` — so
     the aggregate chart would render empty in e2e. The fake must return rows for
     `None` too.
- **User constraint:** when `_ppIsAllMode()` (active tab == `_ALL_PORTFOLIOS`),
  render a **single** chart (the aggregate) titled "Wartość wszystkich portfeli
  w czasie" — no duplicate. Otherwise render two: active-portfolio chart (dynamic
  title) + aggregate chart.

## Detailed Findings

### Backend — `GET /api/portfolio/history` (already all-mode capable)

`src/api.py:994-1031` — `get_portfolio_value_history(portfolio_id, range, ...)`:

- `all_mode = portfolio_id == _ALL_PORTFOLIOS` (`src/api.py:1008`); `_ALL_PORTFOLIOS`
  sentinel defined `src/api.py:322`.
- All-mode **skips** the wallet-ownership `403` check (`src/api.py:1009-1016`).
- Calls `get_portfolio_history(None if all_mode else portfolio_id, user_id, start_date)`
  (`src/api.py:1018`) — `portfolio_id=None` = sum across all wallets.
- Response shape per point: `{date, value_pln, pnl_pln}` (`PortfolioHistoryPoint`,
  `src/api.py:1022-1028`). Cache key already namespaces the id:
  `history:{user_id}:{portfolio_id}:{range}` (`src/api.py:1004`), so `all` and a
  specific id cache independently.
- `db/bigquery.py:465-572` `get_portfolio_history(portfolio_id, user_id, start_date)`:
  "When portfolio_id is provided, results are scoped to that wallet. When it is
  None, [all wallets]" — LOCF forward-fill + full-coverage gate applied in-query.

**Existing tests confirm the contract:**
- `tests/test_api.py:1052` `test_get_portfolio_history_all_mode_skips_ownership_and_passes_none`
  — asserts `portfolio_id=all` → `get_portfolio_history` called with `None`,
  no 404/403.
- `tests/test_api.py:1623` returns 200 with series; `:1645` 403 wrong portfolio;
  `:1654` 422 intraday; `:1663` 422 unknown range; `:1672` 500 on BQ error.

### Frontend — current single-chart implementation (`static/index.html`)

**State (module-level, `static/index.html:3105-3125`):**
- `_activePortfolioId` (3109), `_ppHistData` (3110, single cache),
  `_ppHistRange='3m'` (3111), `_ppHistMetric='value'` (3112),
  `_ppHistReqSeq=0` (3113, single out-of-order guard).
- `_PORTFOLIO_TYPE_LABELS` (3119-3121): `glowny→Główny, ikze→IKZE, ike→IKE,
  ppk→PPK, ppe→PPE, inny→Inny` (nominative; titles need genitive — see below).
- `_ALL_PORTFOLIOS='all'` (3124), `_ppIsAllMode()` (3125).

**Markup (`static/index.html:3611-3628`):** `#pp-history-section` contains a
header row (`<h3 class="pp-hist-title">Wartość portfela w czasie</h3>` — static,
3613), `#pp-history-ranges` (1T/1M/3M/1R, 3615-3620), `#pp-history-metrics`
(Wartość / Zysk-strata, 3621-3624), and a single `#pp-history-chart` (3627).

**Fetch (`static/index.html:4016-4040`) `fetchPortfolioHistory()`:**
- Guard `_activePortfolioId === null` → "Wybierz portfel powyżej." (`all` is
  truthy so it passes).
- URL: `/api/portfolio/history?range=${_ppHistRange}&portfolio_id=${encodeURIComponent(_activePortfolioId)}`
  (4027) — already sends `all` verbatim in all-mode.
- Out-of-order guard via `seq = ++_ppHistReqSeq` checked after each await
  (4025, 4029, 4033) — lessons.md SPA out-of-order pattern (this was PUL-89 F1).
- Caches into `_ppHistData` (4034), then `_renderPortfolioHistory(data)`.

**Render (`static/index.html:4042-4106`) `_renderPortfolioHistory(data)`:**
- Empty/no-data → "Brak danych dla tego zakresu" (4044-4047).
- Picks series by `_ppHistMetric` (value|pnl, 4048-4049), builds an inline SVG
  (gradient area + polyline + gridlines + min/max/first/last axis labels), and a
  header line with current value + delta + pct. Writes into `#pp-history-chart`
  via `innerHTML` (4105). **Fully data-driven and side-effect-free** except for
  the fixed target `$('pp-history-chart')` and its dependence on the shared
  `_ppHistMetric` — trivial to parameterize to (data, targetEl).

### Event wiring (`static/index.html:3672-3717`)

- View-mode tabs (Tabela|Treemapa|Kalendarz|…) scoped to `#pp-view-tabs`
  (3672-3693). On `calendar`: `if (!_ppCalData) fetchPortfolioCalendar();
  if (!_ppHistData) fetchPortfolioHistory();` (3688-3689).
- **Range switcher (3696-3706):** on click sets `_ppHistRange`, clears
  `_ppHistData`, calls `fetchPortfolioHistory()`, writes URL. **This is the hook
  the shared switcher extends** — it must refetch BOTH series.
- **Metric toggle (3709-3717):** on click sets `_ppHistMetric`, redraws from
  cache `if (_ppHistData) _renderPortfolioHistory(_ppHistData)` — no refetch.
  For two charts this must redraw both from their caches.

### Tab selection / lifecycle

- `_selectPortfolioTab(portfolioId, btn)` (`static/index.html:3368-3380`): sets
  `_activePortfolioId`, clears `_ppCalData` + `_ppHistData`, and when in calendar
  mode refetches calendar + history. This is where switching wallet must also
  reset/refetch both charts and re-title.
- `fetchUserPortfolios()` (`static/index.html:3382-3427`): fetches
  `/api/portfolio/wallets` into local `data`, defaults `_activePortfolioId` to
  `_ALL_PORTFOLIOS` (PUL-90), restores `?portfolio=`, `?range=`, `?tab=` from URL.
  **`data` (the wallets) is never stored globally** → titles have no source
  today. Restored `_ppHistRange` from `?range=` (3410-3416) must drive both
  charts.
- `_ppWriteUrl` / `_ppPortfolioParams` (`static/index.html:2660-2675`): writes
  `tab`, `portfolio`, and (calendar) `year/month/range`. Range persistence stays
  a single value (shared switcher) — no URL schema change needed.

### Titles — genitive requirement

`_PORTFOLIO_TYPE_LABELS` is **nominative** ("Główny"), but the ticket wants the
genitive form inside "Wartość portfela … w czasie":
- głowny → "Wartość portfela **głównego** w czasie"
- ikze → "Wartość portfela **IKZE** w czasie"
- user-named (`inny`) → Wartość portfela **"<portfolio_name>"** w czasie
- aggregate → "Wartość wszystkich portfeli w czasie"

So the plan needs a small genitive map (or per-type title strings) distinct from
`_PORTFOLIO_TYPE_LABELS`. Other types present in the add-portfolio modal
(`static/index.html:3634-3639`: ike, ppk, ppe) should get genitive forms too
(ike→IKE, ppk→PPK, ppe→PPE read fine as-is; only "głowny"→"głównego" inflects).

### e2e test scaffolding

- `tests/e2e/conftest.py:352-362`: `_FAKE_HISTORY_ROWS` (3 weekday points) and
  `_fake_get_portfolio_history(portfolio_id, ...)` → rows **only** when
  `portfolio_id == _FAKE_PORTFOLIO_ID`, else `[]`. In all-mode the endpoint
  passes `None`, so **the fake returns `[]` for the aggregate** → the "Wszystkie"
  chart would be empty in e2e. Fix: return rows for `None` too (aggregate).
  Patched at `tests/e2e/conftest.py:572-573`.
- Existing e2e: `tests/e2e/test_portfolio_value_history.py` (3 tests, PUL-89) —
  the reference for extending browser coverage (two charts + shared switcher +
  all-mode single chart). Memory note: conftest already mocks history; adding
  entities to the shared conftest risks strict-mode locator collisions — audit
  before adding.

## Code References

- `src/api.py:994-1031` — `GET /api/portfolio/history`, all-mode branch (`:1008`, `:1018`).
- `src/api.py:322` — `_ALL_PORTFOLIOS` sentinel.
- `db/bigquery.py:465-572` — `get_portfolio_history`, `None` = sum all wallets.
- `static/index.html:3109-3125` — history state vars + `_ALL_PORTFOLIOS` / `_ppIsAllMode`.
- `static/index.html:3119-3121` — `_PORTFOLIO_TYPE_LABELS` (nominative).
- `static/index.html:3368-3380` — `_selectPortfolioTab`.
- `static/index.html:3382-3427` — `fetchUserPortfolios` (wallets NOT stored globally).
- `static/index.html:3611-3628` — `#pp-history-section` markup (static `<h3>`, single `#pp-history-chart`).
- `static/index.html:3696-3717` — range switcher + metric toggle wiring.
- `static/index.html:4016-4040` — `fetchPortfolioHistory`.
- `static/index.html:4042-4106` — `_renderPortfolioHistory` (parameterizable to target element).
- `tests/e2e/conftest.py:352-362, 572-573` — history fake + patch.
- `tests/e2e/test_portfolio_value_history.py` — PUL-89 e2e reference.

## Architecture Insights

- **Backend already supports the feature**; PUL-91 is a pure front-end
  composition change. Lowest-risk path: keep one shared `_ppHistRange` +
  `_ppHistMetric`, add a second data slot + second chart container, and a small
  fetch orchestrator that (a) always fetches the aggregate (`all`), and (b) also
  fetches `_activePortfolioId` when not in all-mode.
- **Out-of-order guard must scale to two fetches.** PUL-89's F1 bug was an
  out-of-order resolve desyncing the chart from the active range. With two
  concurrent fetches per range switch, use per-slot seq counters (or one seq
  that both fetches capture and re-check) so a stale resolve of either series
  cannot render.
- `_renderPortfolioHistory` is essentially pure(data)→SVG; parameterizing it to
  `(data, targetEl, title)` is the natural refactor and keeps the SVG code
  single-sourced for both charts.
- Metric toggle (Wartość/Zysk-strata) currently redraws from a single cache;
  keep it shared across both charts for consistency (redraw both caches).

## Historical Context (from prior changes)

- **PUL-90** (`f05bdf0`, memory `session-2026-07-24`): added `portfolio_id=all`
  sentinel to positions/calendar/history; calendar + chart already aggregate in
  all-mode. Gotcha: changing the default tab to read-only "Wszystkie" broke e2e
  (`_open_portfolio*` helpers click "Główny"; `to_be_visible` strict-mode fails
  with 2 tabs). Don't add entities to shared conftest without a strict-mode audit.
- **PUL-89** (`60fb5d7`, memory `session-2026-07-23`): built the single chart
  under the calendar; F1 fix was the out-of-order guard `_ppHistReqSeq`.
  conftest already mocks `get_portfolio_history`.
- **PUL-79** (`c95a883`, memory `session-2026-07-22b`): the endpoint itself;
  LOCF forward-fill + full-coverage gate chosen over 0-fill (0-fill = false
  ~25% jump). 1Y range currently returns a partial year (ingestion started
  ~mid-2026) — backfill is a separate ticket, out of scope here.

## Related Research

- `context/archive/**` PUL-89 / PUL-79 / PUL-90 artifacts (value-history lineage).

## Open Questions

1. **Chart order & layout:** active first then aggregate (side-by-side vs
   stacked)? Ticket says "next to". `#pp-history-chart` is `max-width:640px`
   (`static/index.html:859`) — side-by-side needs a responsive 2-col that stacks
   on narrow screens. → resolve in /10x-plan.
2. **Metric toggle scope:** confirm the Wartość/Zysk-strata toggle applies to
   both charts (recommended) — ticket only names the range switcher explicitly.
3. **Titles for user-named portfolios:** truncation/escaping of long
   `portfolio_name` in the `<h3>` (use existing `esc()`); confirm max display.
