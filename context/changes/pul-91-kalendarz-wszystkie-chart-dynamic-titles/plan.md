# PUL-91 — Kalendarz: second "Wszystkie" value chart + dynamic titles + shared range switcher

## Overview

Extend the single value-history chart under the Kalendarz view into **two**
inline-SVG charts — the **active portfolio** and the **"Wszystkie"** (all-portfolios
aggregate) — driven by **one shared** range switcher and metric toggle, each with a
**dynamic Polish title** derived from portfolio type/name. When the active tab is
already "Wszystkie" (the PUL-90 aggregate view), render **only one** chart (the
aggregate) — no duplicate. Backend is unchanged: the aggregate series is already
available via `portfolio_id=all`.

## Current State Analysis

- One chart today: `#pp-history-section` → static `<h3>Wartość portfela w czasie</h3>`,
  range switcher `#pp-history-ranges` (1T/1M/3M/1R), metric toggle `#pp-history-metrics`
  (Wartość/Zysk-strata), single `#pp-history-chart` (`static/index.html:3611-3628`).
- Single-slot state: `_ppHistData` (one cache), `_ppHistRange='3m'`, `_ppHistMetric='value'`,
  `_ppHistReqSeq` (one out-of-order guard) (`static/index.html:3110-3113`).
- `fetchPortfolioHistory()` fetches `/api/portfolio/history?range=&portfolio_id=${_activePortfolioId}`
  (already sends `all` verbatim), guards out-of-order via `_ppHistReqSeq`, caches to
  `_ppHistData`, renders (`static/index.html:4016-4040`).
- `_renderPortfolioHistory(data)` is effectively `pure(data)→SVG`, but hard-targets
  `$('pp-history-chart')` and reads shared `_ppHistMetric` (`static/index.html:4042-4106`).
- Range switcher (`:3696-3706`) and metric toggle (`:3709-3717`) both act on the single chart.
- **Backend already all-mode capable** (PUL-90): `GET /api/portfolio/history` with
  `portfolio_id=all` skips ownership and sums all wallets via `get_portfolio_history(None, …)`
  (`src/api.py:1008-1018`). No backend change.
- **Wallets list is NOT stored globally** — `fetchUserPortfolios` uses local `data`
  (`static/index.html:3382-3427`); dynamic titles have no source today.
- `_PORTFOLIO_TYPE_LABELS` is **nominative** ("Główny"); titles need genitive
  ("…portfela głównego w czasie") (`static/index.html:3119-3121`).
- e2e fake `_fake_get_portfolio_history` returns `[]` for `portfolio_id != _FAKE_PORTFOLIO_ID`,
  i.e. `[]` for all-mode's `None` (`tests/e2e/conftest.py:359-362`).

## Desired End State

Under Kalendarz, with a specific portfolio active: **two** charts render — the active
portfolio (title per type/name) and "Wartość wszystkich portfeli w czasie" — arranged
side-by-side on desktop and stacked on mobile. One range switcher refetches+redraws both;
one metric toggle redraws both from cache. With "Wszystkie" active: **one** chart (the
aggregate). Per-chart empty/loading/error states; light+dark correct; no console errors;
no new deps; existing pytest suite green; e2e covers both-charts, all-mode-single, and
shared-switcher.

### Key Discoveries:

- Backend aggregate is one fetch away: `portfolio_id=all` (`src/api.py:1008`, verified by
  `tests/test_api.py:1052`).
- `_renderPortfolioHistory` needs only `(data, targetEl)` parameterization —
  `_ppHistMetric` stays shared (`static/index.html:4042-4106`).
- Out-of-order guard must scale to two concurrent fetches (PUL-89 F1 lineage) —
  per-slot seq counters (`static/index.html:4022-4033`).
- Store wallets globally (`_ppPortfolios`) to source titles (`static/index.html:3402`).

## What We're NOT Doing

- No backend / endpoint / BigQuery changes (`portfolio_id=all` already exists).
- No "Wszystkie" positions/summary table in Mój portfel (separate ticket).
- No historical-price backfill (1Y stays partial — separate ticket).
- No URL-schema change: `?range=` stays a single shared value.
- No change to Tabela/Treemapa views or the calendar grid itself.

## Implementation Approach

Keep one shared `_ppHistRange` and `_ppHistMetric`. Replace the single chart slot with
two slots (active, aggregate), each with its own data cache, seq guard, chart container,
and title element. A small orchestrator always fetches the aggregate (`all`) and, when not
in all-mode, also fetches the active portfolio; it renders each into its slot as it
resolves. All-mode renders the single aggregate slot and hides the active slot. Titles come
from a genitive map keyed on `portfolio_type` (+ quoted `portfolio_name` for `inny`),
sourced from a newly-stored `_ppPortfolios`.

## Critical Implementation Details

- **Two concurrent fetches per range switch** — each slot gets its own monotonic seq
  (`_ppHistReqSeqActive` / `_ppHistReqSeqAll`), captured before its `await` and re-checked
  after each, so a stale resolve of either series cannot render (PUL-89 F1 was exactly this
  desync with one series).
- **all-mode is the aggregate** — do not issue two identical `all` fetches. When
  `_ppIsAllMode()`, fetch only the aggregate and render only its slot; hide the active slot.
- **Titles are genitive**, distinct from nominative `_PORTFOLIO_TYPE_LABELS`: głowny→"głównego",
  ikze→"IKZE", ike→"IKE", ppk→"PPK", ppe→"PPE"; `inny`→`Wartość portfela "<esc(name)>" w czasie`;
  aggregate→"Wartość wszystkich portfeli w czasie". Escape `portfolio_name` via existing `esc()`.

## Phase 1: Two charts + dynamic titles + all-mode single

### Overview

Turn the one-chart section into two responsive chart slots with dynamic titles, a
two-slot fetch orchestrator with per-slot out-of-order guards, and shared range/metric
controls acting on both. Handle all-mode (single chart) and per-slot empty/loading/error.

### Changes Required:

#### 1. Markup — two chart slots + title elements

**File**: `static/index.html` (`#pp-history-section`, ~3611-3628)

**Intent**: Replace the single static `<h3>` + single `#pp-history-chart` with a
responsive container holding two chart blocks, each = a dynamic title element + a chart
body element. Keep the shared `#pp-history-ranges` and `#pp-history-metrics` in the header
row (they now drive both). The active block is hidden in all-mode.

**Contract**: New structure inside `#pp-history-section`, e.g. `#pp-history-charts` wrapper
containing `#pp-history-block-active` (`<h3 id="pp-history-title-active">` + `<div id="pp-history-chart-active">`)
and `#pp-history-block-all` (`<h3 id="pp-history-title-all">` + `<div id="pp-history-chart-all">`).
Remove the old single `#pp-history-chart` id and the static heading text. Aggregate title
element gets the constant "Wartość wszystkich portfeli w czasie".

#### 2. CSS — responsive two-column layout

**File**: `static/index.html` (`#pp-history-*` rules, ~850-859)

**Intent**: Lay the two chart blocks side-by-side on desktop and stack them on mobile;
let each SVG scale within its column (SVG already uses `viewBox`+`preserveAspectRatio`).

**Contract**: `#pp-history-charts` as a responsive grid/flex — two columns on wide viewports,
one column when narrow (single breakpoint, ~≤720px, or `auto-fit minmax`). Drop/relax the
fixed `max-width:640px` on the chart body so a column can shrink; add a per-block title style.

#### 3. State — two slots + stored wallets

**File**: `static/index.html` (state vars ~3110-3124)

**Intent**: Replace the single history cache/seq with per-slot pairs and add a module-level
wallets store for title lookup.

**Contract**: Introduce `_ppHistDataActive` / `_ppHistDataAll` (caches),
`_ppHistReqSeqActive` / `_ppHistReqSeqAll` (guards); keep shared `_ppHistRange` / `_ppHistMetric`.
Add `_ppPortfolios = []`. Add a genitive title map + a `_ppHistTitleFor(portfolioId)` helper
returning the correct string (uses `_ppPortfolios` + `_ppIsAllMode`).

#### 4. Store wallets on fetch

**File**: `static/index.html` (`fetchUserPortfolios`, ~3400-3402)

**Intent**: Persist the wallets list so titles can be resolved after initial load and on tab
switch.

**Contract**: Set `_ppPortfolios = data` before `_renderPortfolioTabs(data)`.

#### 5. Parameterize the renderer

**File**: `static/index.html` (`_renderPortfolioHistory`, ~4042-4106)

**Intent**: Make the SVG renderer target an arbitrary chart element so both slots reuse one
code path; metric stays shared via `_ppHistMetric`.

**Contract**: Signature `_renderPortfolioHistory(data, chartEl)` — resolve `chartEl` param
instead of hard `$('pp-history-chart')`; all empty/no-data/SVG writes go to `chartEl`. No
change to SVG math or the header line — **except** the gradient id must be namespaced per
target: today `gid = 'pp-hist-grad-' + (pnl?'p':'v')` (`static/index.html:4064`) is a fixed
string, so two charts rendering the same metric would emit two `<linearGradient>` with the
**same** DOM id (invalid HTML; `url(#…)` resolves to the first match). Incorporate `chartEl.id`
into `gid` and into the `url(#…)` reference. Likewise vary the SVG `aria-label` per chart
(active vs aggregate) so the two aren't identically labelled.

#### 6. Two-slot fetch orchestrator

**File**: `static/index.html` (`fetchPortfolioHistory`, ~4016-4040)

**Intent**: Fetch the aggregate always, and the active portfolio too when not in all-mode;
render each slot independently with its own out-of-order guard; set titles; toggle active
block visibility.

**Contract**: `fetchPortfolioHistory()` becomes an orchestrator that (a) sets titles via
`_ppHistTitleFor`, (b) shows/hides `#pp-history-block-active` based on `_ppIsAllMode()`,
(c) calls two internal fetchers — one for `all` (guard `_ppHistReqSeqAll`, cache
`_ppHistDataAll`, render into `#pp-history-chart-all`) and, unless all-mode, one for
`_activePortfolioId` (guard `_ppHistReqSeqActive`, cache `_ppHistDataActive`, render into
`#pp-history-chart-active`). For the `_activePortfolioId === null` case (only reachable with
zero portfolios) **hide both chart blocks** (`#pp-history-block-active` + `#pp-history-block-all`)
— the old code wrote "Wybierz portfel powyżej." to the now-removed `#pp-history-chart`, so there
is no single target anymore; hiding both is cleaner than resurrecting a placeholder.
Preserve 401→`doLogout()` and per-slot loading/error text.

#### 7. Shared range switcher + metric toggle drive both

**File**: `static/index.html` (`:3696-3717`, and `_selectPortfolioTab` `:3368-3380`, calendar-mode branch `:3688-3689`)

**Intent**: One range change refetches both series; one metric change redraws both from
cache; wallet switch and calendar-entry refetch both.

**Contract**: Range handler clears both caches and calls the orchestrator. Metric handler
redraws each non-null cache into its slot (`_ppHistDataActive`→active, `_ppHistDataAll`→all).
`_selectPortfolioTab` clears both caches; the `!_ppHistData` calendar-entry guard becomes a
both-slot check (or unconditional orchestrator call — de-dup via seq).

### Success Criteria:

#### Automated Verification:

- [ ] Full suite green (no backend change): `uv run pytest`
- [ ] No stray reference to the removed single `#pp-history-chart` id / old `_ppHistData` /
      single `_ppHistReqSeq`: `grep -n "pp-history-chart\b\|_ppHistData\b\|_ppHistReqSeq\b" static/index.html` returns nothing unexpected

#### Manual Verification:

- [ ] With a specific portfolio active: two charts render side-by-side (desktop), correct
      dynamic title on the active one + "Wartość wszystkich portfeli w czasie" on the aggregate
- [ ] Titles correct per type: główny→"…głównego…", IKZE→"…IKZE…", named→`…"<nazwa>"…`
- [ ] "Wszystkie" tab active: exactly one chart (aggregate), active block hidden
- [ ] One range switch (e.g. 3M→1R) refetches+redraws BOTH charts to the new range
- [ ] One metric toggle (Wartość↔Zysk/strata) redraws BOTH charts, no refetch
- [ ] Mobile width: charts stack vertically, readable; light + dark both correct; no console errors

**Implementation Note**: After Phase 1 automated checks pass, pause for manual browser
confirmation before Phase 2.

---

## Phase 2: e2e coverage

### Overview

Fix the history fake so all-mode returns data, then add Playwright coverage for the two-chart
layout, all-mode single chart, and the shared switcher — following the PUL-89 test as the
reference and respecting the shared-conftest strict-mode caveat.

### Changes Required:

#### 1. Fake returns aggregate rows

**File**: `tests/e2e/conftest.py` (`_fake_get_portfolio_history`, ~359-362)

**Intent**: In all-mode the endpoint passes `portfolio_id=None`; the fake must return a
non-empty series for it so the aggregate chart renders in e2e.

**Contract**: Return `_FAKE_HISTORY_ROWS` when `portfolio_id in (_FAKE_PORTFOLIO_ID, None)`
(optionally a distinct aggregate row set), `[]` otherwise. No signature change.

#### 2. e2e tests for two charts / all-mode / shared switcher

**File**: `tests/e2e/test_portfolio_value_history.py` (extend)

**Intent**: Cover the new behavior at the browser level, independent + self-cleaning, using
role/text locators (no CSS/XPath), waiting on state not timeouts.

**Contract**: Add tests: (a) specific-portfolio active → both chart blocks visible with the
correct titles (getByText/getByRole heading for the genitive title + the aggregate title);
(b) "Wszystkie" active → only the aggregate chart visible, active block hidden; (c) one range
switch updates both charts (wait on `waitForResponse`/re-render, assert both redraw). Reuse
existing `_open_portfolio*`/calendar helpers; audit strict-mode (two title headings now
present) before adding shared fixtures. Because "Wartość wszystkich portfeli w czasie" appears
as the single chart in all-mode AND as chart #2 in non-all-mode, **scope assertions within the
block containers** (`#pp-history-block-active` / `#pp-history-block-all`) rather than bare
page-level `getByText`, so element counts stay unambiguous.

### Success Criteria:

#### Automated Verification:

- [ ] New + existing history e2e pass: `uv run pytest tests/e2e/test_portfolio_value_history.py`
- [ ] Full suite still green: `uv run pytest`

#### Manual Verification:

- [ ] e2e run shows both charts and the all-mode single chart as intended (spot-check headed run if needed)

**Implementation Note**: Pause for manual confirmation after Phase 2.

---

## Testing Strategy

### Unit Tests:

- None new: `static/index.html` SPA JS is covered at the e2e layer; backend unchanged
  (existing `tests/test_api.py` all-mode tests already assert the `portfolio_id=all` contract).

### Integration / e2e Tests:

- Two charts + correct titles for a specific portfolio.
- Single aggregate chart in all-mode.
- Shared range switcher redraws both charts.

### Manual Testing Steps:

1. Log in, open Mój portfel → Kalendarz with a specific portfolio active → verify two charts + titles.
2. Switch range 3M→1R → both charts refetch/redraw.
3. Toggle Wartość↔Zysk/strata → both charts redraw from cache.
4. Switch to "Wszystkie" tab → one chart only, aggregate title.
5. Narrow the window / mobile → charts stack; check dark mode; console clean.

## Performance Considerations

Two concurrent fetches per range switch instead of one; each is cached server-side
(`history:{user}:{id}:{range}`, 300s) and client-side per slot. Negligible; no new hotspot.

## Migration Notes

None — front-end only, no data or schema changes.

## References

- Research: `context/changes/pul-91-kalendarz-wszystkie-chart-dynamic-titles/research.md`
- Backend all-mode: `src/api.py:1008-1018`; test `tests/test_api.py:1052`
- Chart today: `static/index.html:3611-3628`, `:4016-4106`
- PUL-89 e2e reference: `tests/e2e/test_portfolio_value_history.py`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles.

### Phase 1: Two charts + dynamic titles + all-mode single

#### Automated

- [x] 1.1 Full suite green (no backend change): `uv run pytest` — non-e2e suite 594 passed; the 3 history e2e tests intentionally red until Phase 2 rewrites them (they target the removed `#pp-history-chart`) — 96fab6c
- [x] 1.2 No stray reference to removed single-chart id / single-slot state in `static/index.html` — 96fab6c

#### Manual

- [x] 1.3 Two charts render side-by-side (desktop) with correct active + aggregate titles — 96fab6c
- [x] 1.4 Titles correct per type (główny/IKZE/named) — 96fab6c
- [x] 1.5 "Wszystkie" tab → exactly one chart, active block hidden — 96fab6c
- [x] 1.6 One range switch refetches+redraws both charts — 96fab6c
- [x] 1.7 One metric toggle redraws both charts from cache — 96fab6c
- [x] 1.8 Mobile stacks; light+dark correct; no console errors — 96fab6c

### Phase 2: e2e coverage

#### Automated

- [x] 2.1 History e2e pass: `uv run pytest tests/e2e/test_portfolio_value_history.py` — 4 passed — 911918b
- [x] 2.2 Full suite still green: `uv run pytest` — 707 passed — 911918b

#### Manual

- [x] 2.3 e2e run shows both charts + all-mode single chart as intended — 911918b
