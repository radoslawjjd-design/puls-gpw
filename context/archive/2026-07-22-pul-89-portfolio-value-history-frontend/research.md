---
date: 2026-07-22T00:00:00+02:00
researcher: Radek
git_commit: 7b39961631ce5e8af477b8ad0ad6ba9571b74bd4
branch: pul-89-portfolio-value-history-frontend
repository: puls-gpw
topic: "FARO-5 frontend — value-history line chart + range switcher (PUL-89)"
tags: [research, codebase, portfolio, frontend, svg-chart]
status: complete
last_updated: 2026-07-22
last_updated_by: Radek
---

# Research: FARO-5 frontend — portfolio value-history line chart + range switcher

**Date**: 2026-07-22
**Researcher**: Radek
**Branch**: pul-89-portfolio-value-history-frontend
**Git Commit**: 7b39961

## Research Question

Ground PUL-89 (GH #177) in the actual current `static/index.html`: verify the named
precedents (view-tabs, calendar fetch/render, sparkline SVG, active-wallet id, URL
tab persistence), confirm current line numbers, and surface anything the ticket got
wrong so the plan is accurate. Frontend-only — endpoint is live (PUL-79 / PR #176).

## Summary

The ticket's map of the code is accurate; all named precedents exist at (or within a
few lines of) the cited locations. The build is a **clone-the-calendar-view** job with
a hand-rolled inline SVG line chart modeled on `_sparklineSvg`. Six integration touch
points, all in `static/index.html`, no backend and no new deps.

**One correction to the ticket's guidance:** the ticket says "do NOT add
`X-API-Key`/`X-Client-Id`". In reality **every** portfolio fetch in this file — including
the JWT-only `positions` and `wallets` endpoints (PUL-74) — still sends
`headers: { 'X-API-Key': apiKey }` alongside the same-origin session cookie. The actual
house style is to clone `fetchPortfolioCalendar` **verbatim**, header included. The
cookie is what authenticates (sent automatically on same-origin fetch); the legacy
header is harmless. Deviating from the pattern is the riskier choice. → resolve in plan.

## Detailed Findings

### 1. View-tabs markup — add 4th tab (`static/index.html:3434-3438`)
```html
<div id="pp-view-tabs">
  <button type="button" class="pp-view-tab active" data-mode="table">Tabela</button>
  <button type="button" class="pp-view-tab" data-mode="treemap">Treemapa</button>
  <button type="button" class="pp-view-tab" data-mode="calendar">Kalendarz</button>
</div>
```
Add `<button ... data-mode="history">Wartość</button>`. Buttons are styled by `.pp-view-tab`
(`:842-848`), dark theme at `:955-957` — new button inherits both, zero CSS needed for the tab itself.

### 2. View-toggle handler — add `history` branch (`static/index.html:3583-3603`)
- `:3588` — wallet-tabs wrap visibility: `(mode === 'table' || mode === 'calendar')`. **Add `|| mode === 'history'`** so the wallet selector stays visible (ticket point).
- Add `$('pp-history-wrap').style.display = mode === 'history' ? '' : 'none';` and set the other three wraps to hidden when history is active.
- Add an `else if (mode === 'history')` arm: `stopPortfolioTreemapResize(); if (!_ppHistData) fetchPortfolioHistory();` (lazy first-fetch, mirrors calendar `:3599`).

### 3. Calendar fetch/render to clone (`static/index.html:3802-3900`)
- `fetchPortfolioCalendar()` (`:3802`) — guards `_activePortfolioId === null`, builds URL with `encodeURIComponent(_activePortfolioId)`, `fetch(url, { headers: { 'X-API-Key': apiKey } })`, `if (r.status === 401) doLogout()`, `if (!r.ok) throw`, caches into module var, calls render, `catch` writes an inline error string. **Clone as `fetchPortfolioHistory()`** GETting `/api/portfolio/history?range=${_ppHistRange}&portfolio_id=${encodeURIComponent(_activePortfolioId)}`.
- `_renderPortfolioCalendar(data)` (`:3825`) — pure DOM builder into a container. **Clone as `_renderPortfolioHistory(data)`** to draw the SVG + axis labels + empty state.

### 4. SVG drawing precedent — `_sparklineSvg` (`static/index.html:3145-3152`)
```js
const w = 96, h = 28, min = Math.min(...hist), max = Math.max(...hist);
const span = max - min || 1;
const pts = hist.map((v,i)=>(i*w/(hist.length-1)).toFixed(1)+','+(h-3-(v-min)/span*(h-6)).toFixed(1)).join(' ');
const col = hist[hist.length-1] >= hist[0] ? 'var(--positive)' : 'var(--negative)';
// <svg ...><polyline points=... stroke=col .../></svg>
```
Direct template for the big chart: same normalize-to-viewBox math, same green/red-by-direction
colour, scaled up to a full-width `viewBox` with padding for Y-axis min/max labels.
`var(--positive)`/`var(--negative)` are already theme-aware.

### 5. Auth — house style vs ticket (`static/index.html:1263, 3029-3030, 3328-3329, 3811`)
- `let apiKey = sessionStorage.getItem('apiKey')` (`:1263`).
- `positions` (`:3029`), `wallets` (`:3328`), `calendar` (`:3811`) **all** send `headers: { 'X-API-Key': apiKey }`. Same-origin fetch sends the session cookie automatically → that's the real auth (JWT, PUL-74). **Clone the header; don't drop it.**
- 401 handling is uniform: `if (r.status === 401) { doLogout(); return; }`.

### 6. Active wallet + wallet-switch refetch (`static/index.html:3086, 3310-3319`)
- `_activePortfolioId` module var (`:3086`), set on wallet-tab click (`:3313`).
- On wallet switch (`:3314-3316`): `_ppCalData = null; if (curMode === 'calendar') fetchPortfolioCalendar();`. **Add** `_ppHistData = null;` and `if (curMode === 'history') fetchPortfolioHistory();` so switching wallet on the history tab refetches.

### 7. URL tab persistence — automatic (`static/index.html:2639-2650, 3336-3352`)
- Mode is read from `?tab=` and applied by clicking `.pp-view-tab[data-mode="${urlTab}"]` (`:3348-3350`); written by `_ppWriteUrl` from the active tab's `dataset.mode` (`:2639`). **Generic** — adding `data-mode="history"` makes deep-linking `?tab=history` work with no extra code.

### 8. New module state to add (near `_ppCalData`, `_activePortfolioId`)
- `let _ppHistData = null;` (cache / lazy-fetch guard, mirrors `_ppCalData`).
- `let _ppHistRange = '3m';` (default range per ticket = 3M).

### 9. Markup wrap to add (sibling of `pp-calendar-wrap` `:3528-3542`)
`<div id="pp-history-wrap" style="display:none">` containing: a range switcher
(`1T|1M|3M|1R` buttons, styled like `.pp-view-tab`), the SVG chart container, and an
inline empty/error slot — mirroring the calendar wrap's nav+grid+legend structure.

## Architecture Insights

- **Single-file frontend**: all UI in `static/index.html`; only external JS is `static/js/treemap-layout.js`. No chart lib — inline SVG is the established pattern.
- **Per-view module vars + lazy fetch**: each view (`_ppCalData`, `_ppTreemapData`) caches its payload and fetches on first activation; wallet switch nulls the cache. Follow this exactly.
- **Theme-awareness is mostly free**: reusing existing classes (`.pp-view-tab`, `.empty`, `var(--positive/negative/brand/text/border)`) inherits the dark-theme block (`:908-962`). Only genuinely new elements need new dark rules.
- **Error/empty style**: inline strings like `<div class="empty">…</div>` (calendar `:3818`) / `Brak pozycji…` (table `:3158`). Empty state string per ticket: "Brak danych dla tego zakresu".

## Code References

- `static/index.html:3434-3438` — view-tabs (add 4th)
- `static/index.html:3583-3603` — view-toggle handler (add history branch; `:3588` wallet-wrap visibility)
- `static/index.html:3802-3823` — `fetchPortfolioCalendar` (clone)
- `static/index.html:3825-3900` — `_renderPortfolioCalendar` (clone)
- `static/index.html:3145-3152` — `_sparklineSvg` (SVG math precedent)
- `static/index.html:3310-3319` — wallet-switch refetch hook
- `static/index.html:3336-3352` — URL tab read/apply
- `static/index.html:3528-3542` — `pp-calendar-wrap` markup (structure precedent)
- `static/index.html:842-848, 955-957` — `.pp-view-tab` styling (light + dark)
- `static/index.html:908-962` — dark-theme block

## Historical Context (from prior changes)

- Backend delivered in PUL-79 (PR #176, `c95a883`): `GET /api/portfolio/history?range=1w|1m|3m|1y` → `[{date, value_pln, pnl_pln}]`, ascending, one point per trading day; LOCF forward-fill + full-coverage-gate (empty `[]` when no fully-covered day). Endpoint `src/api.py:get_portfolio_value_history`, BQ `db/bigquery.py:get_portfolio_history`.
- Calendar/treemap/positions views scoped per-user via JWT (PUL-74) — anonymous X-API-Key path retired server-side, but the header still rides along on every client fetch.

## Open Questions

1. **Value ↔ P&L toggle** — ticket lists it as optional/nice-to-have. Include in v1 or defer? (`pnl_pln` is already in the payload, so it's cheap.) → decide at plan time.
2. **Hover tooltip + Y-axis min/max labels** — also optional. Recommend Y-axis min/max labels (near-free, big readability win) in v1; hover tooltip optional.
