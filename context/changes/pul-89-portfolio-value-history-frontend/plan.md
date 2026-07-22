# FARO-5 Frontend — Portfolio Value-History Chart Implementation Plan

## Overview

Add an inline-SVG line chart of the portfolio's total value over time to the "Mój portfel"
view in `static/index.html`, consuming the already-live `GET /api/portfolio/history`
endpoint (PUL-79 / PR #176). Includes a range switcher (1T/1M/3M/1R → 1w/1m/3m/1y,
default 3M), a value header (current value + Δ over range), gradient area fill, gridlines,
Y-axis min/max labels, and a Wartość↔Zysk/strata toggle (plots `value_pln` or `pnl_pln`).
Frontend-only, single file, no new dependencies.

> **Design revision (post-implementation, user feedback):** the chart was originally built
> as a 4th "Wartość" view tab (Phase 1, commit 20ee300). Per user request it was **moved
> under the Kalendarz view** — the value-over-time chart now sits below the calendar grid
> (daily results) inside `pp-calendar-wrap` as a `#pp-history-section`, and the separate
> tab was removed. The calendar view now fetches both the month grid and the value chart;
> `?range=` persists alongside `?tab=calendar`. The chart component itself is unchanged.

## Current State Analysis

The portfolio view has three view tabs (Tabela | Treemapa | Kalendarz) at
`static/index.html:3434-3438`, toggled by a handler at `:3583-3603`. Each view follows a
consistent pattern: a `pp-*-wrap` container, a module-level cache var (`_ppCalData`,
`_ppTreemapData`), a lazy fetch on first activation, and a render function that builds DOM
into the container. The calendar view (`fetchPortfolioCalendar` `:3802`,
`_renderPortfolioCalendar` `:3825`) is the closest sibling to clone. Inline SVG line
drawing already exists in `_sparklineSvg` (`:3145-3152`): normalize values to a viewBox,
`<polyline>`, green/red by direction using `var(--positive)`/`var(--negative)`. The active
wallet id is `_activePortfolioId` (`:3086`), set on wallet-tab click (`:3313`) which nulls
caches and refetches the active view. View mode persists in the URL via `?tab=` — read at
`:3336-3352` (clicks the matching `.pp-view-tab[data-mode]`) and written by
`_ppPortfolioParams` (`:2637`) / `_ppWriteUrl` (`:2650`).

**Auth reality (corrects the ticket):** every portfolio fetch in this file — including the
JWT-only `positions`/`wallets` endpoints — sends `headers: { 'X-API-Key': apiKey }`
alongside the same-origin session cookie (the cookie is the real auth). We clone
`fetchPortfolioCalendar` verbatim, header included; the header is harmless and matching the
pattern is the safe choice. See `research.md` §5.

## Desired End State

The Kalendarz view in Mój portfel renders a value-over-time line chart below the calendar
grid (`#pp-history-section`) for the active wallet at the default 3M range, with a value
header (current value + Δ over range), gradient area fill, gridlines, and Y/X labels. A
range switcher (1T/1M/3M/1R) refetches and redraws. A Wartość↔Zysk/strata toggle redraws
from the cached payload (no refetch). Empty range shows "Brak danych dla tego zakresu",
not a broken chart. Non-200 shows an inline error. Switching wallet refetches both the
calendar and the chart; month nav refetches only the calendar. Deep-link
`?tab=calendar&range=1m` restores the view + range. Light and dark themes both correct;
no console errors; no new deps. (See the Design revision note above — originally a 4th tab.)

### Key Discoveries:

- View-tabs markup: `static/index.html:3434-3438` (new button inherits `.pp-view-tab` styling incl. dark `:955-957`).
- Toggle handler: `:3583-3603`; wallet-wrap visibility line `:3588` needs `|| mode === 'history'`.
- Clone targets: `fetchPortfolioCalendar` `:3802-3823`, `_renderPortfolioCalendar` `:3825-3900`.
- SVG math precedent: `_sparklineSvg` `:3145-3152` (`var(--positive/negative)` already theme-aware).
- Wallet-switch refetch: `:3310-3319` (add `_ppHistData = null` + `if (curMode === 'history') fetchPortfolioHistory()`).
- URL persistence: `_ppPortfolioParams` `:2637-2648` (add `range` for history), reader `:3348-3350` (set `_ppHistRange` from URL before clicking the history tab).
- Calendar wrap structure precedent: `:3528-3542`.

## What We're NOT Doing

- No backend changes — endpoint is live (PUL-79 / PR #176).
- No `1d`/intraday range (needs a timestamped snapshot table — separate future ticket).
- No hover tooltip (deferred; optional per ticket, not in chosen v1 scope).
- No new chart library or external `<script>` — inline SVG only.
- No persistence of the Wartość/P&L toggle in the URL — only `?range=` persists; the toggle resets to Wartość on entry (per decision).
- No debouncing infra — server caches 300s per (user, portfolio, range); range clicks refetch directly.

## Implementation Approach

Clone the calendar view's shape end-to-end, swapping the calendar-grid renderer for an SVG
line-chart renderer. Phase 1 wires all the plumbing (markup, toggle branch, wallet-switch
hook, module state, URL range param) so the tab activates and shows an empty state with no
chart logic yet. Phase 2 fills in the fetch + SVG render, the range/toggle interactions, and
the loading/empty/error states.

## Critical Implementation Details

- **URL range read ordering** — in `fetchUserPortfolios` (`:3348-3350`), the history tab is
  restored by `tabBtn.click()`, and the toggle handler's lazy-fetch reads `_ppHistRange`. So
  `_ppHistRange` must be set from the `?range=` param *before* that click fires, or the
  restored deep-link fetches the default 3M instead of the URL's range.
- **Toggle redraws from cache, range switches refetch** — the Wartość↔P&L toggle must redraw
  from the already-fetched `_ppHistData` (both `value_pln` and `pnl_pln` are in every point),
  never refetch. Only range changes hit the network.

## Phase 1: Scaffold the Wartość view (plumbing + empty state)

### Overview

Add the tab, its wrap container with range switcher + value/P&L toggle + chart/empty/error
slots, the toggle-handler branch, the wallet-switch refetch hook, module state, and URL
`?range=` persistence. After this phase the tab switches correctly and shows an empty state;
no chart is drawn yet.

### Changes Required:

#### 1. View-tabs — 4th tab

**File**: `static/index.html` (`:3434-3438`)

**Intent**: Add a `Wartość` tab so users can reach the value-history view.

**Contract**: `<button type="button" class="pp-view-tab" data-mode="history">Wartość</button>` appended after the Kalendarz button. No new CSS (inherits `.pp-view-tab`).

#### 2. History wrap markup

**File**: `static/index.html` (sibling of `pp-calendar-wrap` `:3528-3542`)

**Intent**: Container for the range switcher, the value/P&L toggle, the SVG chart, and an inline empty/error slot.

**Contract**: `<div id="pp-history-wrap" style="display:none">` containing: (a) a range switcher — 4 buttons styled like `.pp-view-tab` with `data-range="1w|1m|3m|1y"` and labels `1T|1M|3M|1R`, `3m` marked active; (b) a value/P&L toggle — 2 buttons `data-metric="value|pnl"` labels `Wartość|Zysk/strata`, `value` active; (c) `<div id="pp-history-chart">` for the SVG; (d) reuse an inline `<div class="empty">` slot for empty/error. Static labels in Polish.

#### 3. Module state

**File**: `static/index.html` (near `_ppCalData` / `let _activePortfolioId` `:3086`)

**Intent**: Cache var + current range + current metric, mirroring `_ppCalData`.

**Contract**: `let _ppHistData = null;`, `let _ppHistRange = '3m';`, `let _ppHistMetric = 'value';`.

#### 4. Toggle-handler branch

**File**: `static/index.html` (`:3583-3603`)

**Intent**: Show/hide the history wrap and lazily fetch on first activation, keeping the wallet selector visible.

**Contract**: line `:3588` → `(mode === 'table' || mode === 'calendar' || mode === 'history')`; add `$('pp-history-wrap').style.display = mode === 'history' ? '' : 'none';` and set it to `'none'` for the other modes; add `else if (mode === 'history') { stopPortfolioTreemapResize(); if (!_ppHistData) fetchPortfolioHistory(); }`.

#### 5. Wallet-switch refetch hook

**File**: `static/index.html` (`:3310-3319`)

**Intent**: Switching wallet while on the history tab refetches for the new wallet.

**Contract**: add `_ppHistData = null;` next to `_ppCalData = null;` and `if (curMode === 'history') fetchPortfolioHistory();` next to the existing calendar refetch.

#### 6. URL range persistence

**File**: `static/index.html` — writer `_ppPortfolioParams` (`:2637-2648`), reader `fetchUserPortfolios` (`:3348-3350`)

**Intent**: Persist/restore the selected range via `?range=` (tab already persists via `?tab=`).

**Contract**: writer — `if (activeTab === 'history') out.set('range', _ppHistRange);`. Reader — before the `tabBtn.click()` for a restored tab, if `urlTab === 'history'` read `urlParams.get('range')` and, when it is one of `1w|1m|3m|1y`, assign `_ppHistRange` and sync the switcher's active button. (Set state before click — see Critical Implementation Details.)

### Success Criteria:

#### Automated Verification:

- App serves without error: `curl -sf http://localhost:8000/health` (or the app's health route) returns 200.
- `static/index.html` parses (no unbalanced tags): open the page, no console errors on load.

#### Manual Verification:

- A 4th "Wartość" tab appears after Kalendarz and is styled consistently (light + dark).
- Clicking Wartość shows the wrap with range switcher (3M active) + value/P&L toggle + an empty state; the wallet selector stays visible.
- Switching between Tabela/Treemapa/Kalendarz/Wartość shows/hides the correct wraps with no leftover content.
- `?tab=history&range=1m` deep-link opens the Wartość tab with 1M active.
- No console errors.

**Implementation Note**: After automated checks pass, pause for manual confirmation before Phase 2.

---

## Phase 2: Chart rendering + interactions

### Overview

Implement `fetchPortfolioHistory` (clone calendar fetch) and `_renderPortfolioHistory` (SVG
line chart + Y min/max labels), wire the range switcher (refetch) and value/P&L toggle
(redraw from cache), and handle loading/empty/error states.

### Changes Required:

#### 1. `fetchPortfolioHistory()`

**File**: `static/index.html` (clone of `fetchPortfolioCalendar` `:3802-3823`)

**Intent**: Fetch the value-history series for the active wallet + current range, cache it, render, with the same auth/401/error handling as calendar.

**Contract**: guard `_activePortfolioId === null` → show "Wybierz portfel powyżej." in `pp-history-chart`; show a loading state; `GET /api/portfolio/history?range=${_ppHistRange}&portfolio_id=${encodeURIComponent(_activePortfolioId)}` with `headers: { 'X-API-Key': apiKey }`; `if (r.status === 401) { doLogout(); return; }`; `if (!r.ok) throw`; `_ppHistData = data; _renderPortfolioHistory(data);`; `catch` → inline "Błąd ładowania danych." in `pp-history-chart`.

#### 2. `_renderPortfolioHistory(data)`

**File**: `static/index.html` (clone-shape of `_renderPortfolioCalendar` `:3825`)

**Intent**: Draw the line chart from the cached payload using the current metric, with Y min/max labels and X date-range labels; empty payload → empty state.

**Contract**: if `!data.length` → `pp-history-chart` shows `<div class="empty">Brak danych dla tego zakresu</div>` and return. Otherwise pick series = `data.map(d => _ppHistMetric === 'pnl' ? d.pnl_pln : d.value_pln)`; compute min/max/span (`span || 1`); build a full-width responsive `<svg viewBox="0 0 W H">` with padding; `<polyline>` over the series using the `_sparklineSvg` normalize math scaled to the viewBox; stroke `data[last] >= data[0] ? var(--positive) : var(--negative)`; render Y min/max value labels (formatted PLN, e.g. `Math.round`) and X first/last date labels (`data[0].date` / `data[last].date`). SVG uses `width:100%` / `max-width:100%` so no horizontal page scroll on mobile.

#### 3. Range switcher wiring

**File**: `static/index.html` (in the portfolio view wiring block near `:3583`)

**Intent**: Clicking a range button updates state, refetches, and persists to URL.

**Contract**: delegate clicks on `[data-range]` buttons → set active class, `_ppHistRange = btn.dataset.range`, `_ppHistData = null`, `fetchPortfolioHistory()`, `_ppWriteUrl(true)`.

#### 4. Value/P&L toggle wiring

**File**: `static/index.html` (same wiring block)

**Intent**: Clicking a metric button redraws from cache without refetching.

**Contract**: delegate clicks on `[data-metric]` buttons → set active class, `_ppHistMetric = btn.dataset.metric`, `if (_ppHistData) _renderPortfolioHistory(_ppHistData)`. No network, no URL change.

#### 5. Dark-theme rules (only if needed)

**File**: `static/index.html` (dark block `:908-962`)

**Intent**: Any genuinely new element (e.g. axis labels) that doesn't inherit an existing themed class gets a dark rule.

**Contract**: add `html[data-theme="dark"]` rules only for new class names introduced in the chart markup; reuse `var(--text)`/`var(--positive)`/`var(--negative)` so most theming is free.

### Success Criteria:

#### Automated Verification:

- Page loads with no console errors: exercised via the browser in manual verification.

#### Manual Verification:

- Kalendarz view renders the value-over-time chart below the calendar grid for the active wallet at 3M, with the value header (current + Δ).
- Range switcher (1T/1M/3M/1R) refetches and redraws; line reshapes per range.
- Value↔Zysk/strata toggle redraws instantly (no network call — verify in Network tab) and P&L can go negative without breaking the chart.
- Empty range (wallet with no covered day) shows "Brak danych dla tego zakresu", not a broken chart.
- Switching wallet on the Kalendarz view refetches both calendar and chart for the new wallet.
- Month nav (prev/next) refetches only the calendar, not the chart.
- Both wallets work; light + dark themes both correct; no horizontal page scroll on mobile width.
- `?tab=calendar&range=3m` restores view + range on reload; no console errors.

**Implementation Note**: After automated checks pass, pause for manual confirmation. This is the last phase → hand off to /10x-e2e for browser coverage of the key risks.

---

## Testing Strategy

### Manual Testing Steps:

1. Log in, open Mój portfel, click Kalendarz → line renders below the calendar at 3M for the active wallet.
2. Click each range (1T/1M/3M/1R) → chart refetches and reshapes; watch Network for one request per click.
3. Toggle Zysk/strata → chart redraws with no new network request; toggle back to Wartość.
4. Switch wallet tab → both calendar and chart refetch for the new wallet.
5. Pick a wallet/range with no data → empty state string shows under the calendar.
6. Toggle dark theme → colours and labels remain correct.
7. Reload with `?tab=calendar&range=1m` → Kalendarz + 1M restored.
8. Narrow viewport to mobile width → no horizontal page scroll.

### E2E (hand off to /10x-e2e):

- Risk: tab activation renders a chart (getByRole tab "Wartość" → SVG/line visible).
- Risk: range switch refetches (waitForResponse on `/api/portfolio/history`).
- Risk: empty range → empty-state text visible.

## Performance Considerations

Server caches 300s per (user, portfolio, range) — range clicks refetch directly, no client
debounce needed. The value/P&L toggle never refetches (redraws from cache). Series are small
(≤ ~250 points for 1y of trading days); SVG polyline is trivial to render.

## References

- Related research: `context/changes/pul-89-portfolio-value-history-frontend/research.md`
- Backend ticket: PUL-79 / GH #138 / PR #176. Frontend ticket: PUL-89 / GH #177.
- Precedents: `static/index.html` `_sparklineSvg` `:3145`, `fetchPortfolioCalendar` `:3802`, `_renderPortfolioCalendar` `:3825`, view-tabs `:3434-3438`, toggle `:3583-3603`, `_ppPortfolioParams` `:2637`.

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Scaffold the Wartość view

#### Automated

- [x] 1.1 App serves without error (health route 200) — 20ee300
- [x] 1.2 static/index.html loads with no console errors — 20ee300

#### Manual

- [x] 1.3 4th "Wartość" tab appears after Kalendarz, styled consistently (light + dark) — 20ee300
- [x] 1.4 Clicking Wartość shows range switcher (3M active) + value/P&L toggle + empty state; wallet selector visible — 20ee300
- [x] 1.5 Switching between the four tabs shows/hides the correct wraps with no leftover content — 20ee300
- [x] 1.6 `?tab=calendar&range=1m` deep-link opens Kalendarz with chart at 1M active (design revised: chart moved under calendar) — 4782b75
- [x] 1.7 No console errors — 20ee300

### Phase 2: Chart rendering + interactions

#### Automated

- [x] 2.1 Page loads with no console errors (via browser in manual verification) — 4782b75

#### Manual

- [x] 2.2 Kalendarz view renders the chart below the calendar grid for the active wallet at 3M (with value header) — 4782b75
- [x] 2.3 Range switcher refetches and redraws; line reshapes per range — 4782b75
- [x] 2.4 Value↔Zysk/strata toggle redraws instantly with no network call; negative P&L OK — 4782b75
- [x] 2.5 Empty range shows "Brak danych dla tego zakresu", not a broken chart — 4782b75
- [x] 2.6 Switching wallet on Kalendarz refetches both calendar and chart; month nav refetches only calendar — 4782b75
- [x] 2.7 Both wallets work; light + dark correct; no horizontal page scroll on mobile — 4782b75
- [x] 2.8 `?tab=calendar&range=3m` restores view + range on reload; no console errors — 4782b75
