# Treemap D/D:/Total: labels, since-purchase P&L, hover highlight + click-to-filter Implementation Plan

## Overview

Follow-up to PUL-45/PUL-50 (shipped, archived). Extends the existing portfolio
treemap with: (1) explicit `D/D:`/`Total:` labels on the two existing detail
lines plus a new third "since-purchase" P&L line (`Zakup:` — % and PLN) per
position, and (2) a bold hover-highlight border on cells plus click-to-filter
navigation to the announcements table, pre-filtered by the clicked cell's
ticker.

## Current State Analysis

- `compute_treemap_positions(today_positions_json, yesterday_positions_json,
  total_value)` (`src/portfolio_treemap.py`) parses each of today's positions
  from `positions_json` but only reads `ticker` and `value` — it never reads
  the `pct` field, even though `pct` (cumulative % return since purchase,
  parsed by Gemini from the XTB screenshot) is already present in every
  `positions_json` row written by `/portfolio-xpost` (confirmed:
  `tests/test_api.py:279,285,291`, `tests/e2e/conftest.py` fixtures use it for
  other purposes already).
- `profit_abs` (the PLN amount matching `pct`) is parsed by Gemini
  (`src/gemini_client.py:63-68`) but is **not** persisted in `positions_json`
  — only `ticker`, `value`, and `pct` are. It must be derived mathematically:
  `cost = value / (1 + pct/100)`, `profit_pln = value - cost` — the exact
  inverse of `_cumulative_pct()` (`src/portfolio_thread_composer.py:48-52`,
  `pct = profit_abs / (value - profit_abs) * 100`).
- `TreemapPosition` (`src/api.py:105-111`) has five fields: `ticker`,
  `position_value_pln`, `daily_change_pln`, `daily_change_pct`,
  `portfolio_share_pct`. No since-purchase fields exist yet.
- `renderTreemap()` (`static/index.html:1002-1036`) renders two unlabeled
  detail lines (`dailyText`, `shareText`) once a cell is at least `90×46`
  (`static/index.html:1026`), plus a `.tc-tooltip` with the same two lines
  (`static/index.html:1030`). Neither line has a text label today.
- `.treemap-cell:hover` (`static/index.html:246`) currently only sets
  `overflow: visible; z-index: 5;` to let the tooltip escape clipping — no
  border/outline highlight exists.
- Treemap cells render as plain `<div class="treemap-cell ...">` with no
  `data-*` attributes and no click handler — there is no click-to-navigate
  precedent on this view. The closest precedent in the codebase is
  `tr.clickable` in the x-posts history table (`static/index.html:936-949`),
  which is click-only with no keyboard/role support.
- The announcements filter form already has a `#f-ticker` input
  (`static/index.html:298`) wired to `fetchAnnouncements()`
  (`static/index.html:828-866`, reads `v('ticker')` at line 833), and
  `showAnnouncementsView()` (`static/index.html:794-801`) already handles
  switching away from the treemap view, including stopping the resize
  tracker. Click-to-filter can reuse all of this — no new fetch/filter
  machinery needed.
- `tests/e2e/conftest.py`'s treemap fixtures (`_FAKE_TREEMAP_LATEST`,
  `_FAKE_TREEMAP_PRIOR`, `_FAKE_TREEMAP_IKZE_LATEST`,
  `_FAKE_TREEMAP_IKZE_PRIOR`, lines 54-102) currently omit `pct` entirely from
  every position — they must gain `pct` values to exercise the new line in
  E2E.

## Desired End State

An admin opens "Treemapa portfela" and sees, on every cell large enough: a
`D/D:` line (today's %/PLN change, unchanged in meaning), a `Total:` line
(wallet share % and absolute PLN value, unchanged in meaning), and a new
`Zakup:` line showing the position's cumulative %/PLN gain or loss since
purchase — or `Zakup: brak danych` when that data isn't computable.
Hovering any cell additionally draws a bold white outline around it (on top
of the existing tooltip). Clicking any cell navigates to the announcements
table with that ticker pre-filled in the ticker filter and the results
fetched, leaving any other active filters untouched.

Verify by: opening the treemap as admin, confirming all three labeled lines
render correctly (including a `brak danych` case), hovering a cell to see
the outline, and clicking a cell to land on the announcements table filtered
by that ticker.

### Key Discoveries:

- `pct` is already in every `positions_json` row — no ingestion/schema change
  needed, this is a pure read-time addition (`tests/test_api.py:279`).
- `profit_abs` is not persisted — deriving it from `pct` + `value` reuses the
  exact inverse of an existing, tested formula
  (`src/portfolio_thread_composer.py:48-52`), so the math itself is already
  proven correct elsewhere in the codebase.
- `pct == -100` makes the inverse formula's denominator zero (full loss, cost
  computed as 0) — this is the one edge case the existing `_cumulative_pct()`
  doesn't have to handle (it only ever computes the percentage, never
  inverts it), so it's new to this change.
- The announcements view's ticker filter and view-switching machinery already
  exist and need zero changes to support click-to-filter — only the treemap
  side needs new code.

## What We're NOT Doing

- No schema or ingestion change to `/portfolio-xpost` or `positions_json` —
  `profit_abs` stays derived, not persisted.
- No keyboard/role accessibility on treemap cells (matches the existing
  `tr.clickable` precedent in the x-posts table).
- No filter-form reset on click — only the ticker field is overwritten,
  company/event-type/date filters are left as the admin set them.
- No new truncation tier between "ticker-only" and "all three lines" — a
  single raised threshold governs all three lines together, same binary
  behavior as today's two-line threshold.
- No changes to `computeTreemapLayout`/`treemap-layout.js` — the layout
  algorithm is unaware of cell content and needs no changes for either part
  of this change.

## Implementation Approach

Backend-to-frontend, three phases, splitting the two independent
user-facing concerns (data: labels + since-purchase line; interaction:
hover + click) into their own phases after the shared backend groundwork:

1. **Backend**: extend `compute_treemap_positions()` to derive
   `since_purchase_pct`/`since_purchase_pln` from the already-present `pct`
   field, guard the division-by-zero edge case, extend `TreemapPosition`.
2. **Frontend — labels & since-purchase line**: add the `D/D:`/`Total:`
   prefixes, render the new `Zakup:` line with its `brak danych` fallback,
   raise the truncation threshold to `90×60`, update E2E fixtures to include
   `pct`.
3. **Frontend — hover & click**: add the hover outline, wire cell clicks to
   the existing ticker-filter/view-switch machinery, add E2E coverage for
   both.

## Critical Implementation Details

**The division-by-zero edge case is deliberately collapsed to "no data," not
partially rendered.** When `pct == -100` (a total loss), `cost = value / (1 +
pct/100)` divides by zero. Rather than showing a `Zakup:` line with a known
% but an unknowable PLN amount, both `since_purchase_pct` and
`since_purchase_pln` are set to `None` together in this case — mirroring how
`daily_change_pln`/`daily_change_pct` are already nulled together as a pair
elsewhere in this same function, never independently. The frontend therefore
only needs one null-check (on `since_purchase_pln`) to decide whether to
render the line or fall back to `brak danych`, with no partial-data state to
handle.

**`since_purchase_pct` is a passthrough of the already-parsed `pct` field,
not a recomputation.** Only the PLN amount needs deriving — `pct` itself is
already the correct cumulative percentage straight from
`positions_json`. Naming follows the existing `daily_change_pct` /
`daily_change_pln` pair convention exactly (`since_purchase_pct` /
`since_purchase_pln`), not `profit_abs`/`profit_pln`, so the two pairs read
consistently in the API response and in `renderTreemap()`.

## Phase 1: Backend — since-purchase P&L computation

### Overview

Extend the pure delta-computation function and its Pydantic model with the
new since-purchase fields, derived from the `pct` field that's already
present in every position but currently dropped.

### Changes Required:

#### 1. `src/portfolio_treemap.py` — since-purchase computation

**Intent**: For each of today's positions, read the already-present `pct`
field and derive the PLN amount via the inverse of the existing
`_cumulative_pct` formula, nulling both fields together when `pct` is
missing or the denominator is zero.

**Contract**: In the existing per-position loop, after computing
`portfolio_share_pct`, add: `pct = position.get("pct")`; if `pct` is not a
number, `since_purchase_pct = since_purchase_pln = None`; else compute
`denom = 1 + pct / 100`; if `denom == 0`, both `None`; else `cost = value /
denom`, `since_purchase_pct = pct`, `since_purchase_pln = value - cost`. Add
both keys to the per-position result dict. The malformed-JSON path (returns
`[]`) and the malformed-item-skip path (existing `try`/`except`) are
unaffected — this only adds two new keys to an already-built dict.

#### 2. `src/api.py` — model field additions

**Intent**: Expose the two new fields through the API response.

**Contract**: `TreemapPosition` (`src/api.py:105-111`) gains
`since_purchase_pct: float | None = None` and `since_purchase_pln: float |
None = None`. No endpoint body changes — `TreemapPosition(**p).model_dump()`
already passes through whatever keys `compute_treemap_positions` returns.

#### 3. `tests/test_portfolio_treemap.py` — since-purchase test cases

**Intent**: Cover the new fields' happy path and both null-collapse edge
cases, and update existing exact-equality assertions that will otherwise
fail once the result dicts gain two new keys.

**Contract**: Update every existing test that asserts `result == [...]` or
`result[0] == {...}` as a full dict literal to include
`"since_purchase_pct"`/`"since_purchase_pln"` computed from each fixture's
existing `pct` value (all current fixtures already include a `pct` key, so
this is arithmetic, not new fixture data). Add new cases:
position missing the `pct` key entirely → both fields `None`; `pct == -100`
→ both fields `None` (division-by-zero collapse); a normal positive `pct`
(e.g. `pct: 20.0`, `value: 1200.0` → `cost = 1000.0`, `since_purchase_pln =
200.0`) computed correctly.

### Success Criteria:

#### Automated Verification:

- Updated pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q`
- Full test suite still passes: `uv run pytest --tb=short`

#### Manual Verification:

- `curl -H "X-API-Key: <admin-key>" localhost:8000/admin/portfolio/treemap` returns
  `since_purchase_pct`/`since_purchase_pln` per position with plausible values
  against real data.

---

## Phase 2: Frontend — labeled lines & since-purchase display

### Overview

Add the `D/D:`/`Total:` prefixes to the two existing lines, render the new
`Zakup:` line (with its `brak danych` fallback), raise the truncation
threshold so all three lines appear together or not at all, and give the E2E
suite real `pct` data to exercise this.

### Changes Required:

#### 1. `static/index.html` — `renderTreemap()` cell content

**Intent**: Label the two existing lines, build the third since-purchase
line with the same sign/fallback conventions as the daily line, and gate all
three together behind a single raised threshold.

**Contract**: In `renderTreemap()` (`static/index.html:1002-1036`):
`dailyText` gains a `D/D: ` prefix; `shareText` gains a `Total: ` prefix.
Build `sincePurchaseText` following the exact same sign/formatting pattern as
`dailyText` (`static/index.html:1014-1021`), keyed off
`item.since_purchase_pln === null` → `'Zakup: brak danych'`, else `Zakup:
${sign}${pct.toFixed(1)}% / ${sign}${pln.toFixed(0)} PLN`. The truncation
condition (`static/index.html:1026`) changes from `cell.width >= 90 &&
cell.height >= 46` to `cell.width >= 90 && cell.height >= 60`, and when true
renders all three `<span class="tc-detail">` lines (daily, total,
since-purchase) instead of two. The `.tc-tooltip` (`static/index.html:1030`)
gains the same third line so hovering a too-small cell still surfaces the
since-purchase figure.

#### 2. `static/index.html` — CSS

**Intent**: No structural CSS change needed for a third stacked line —
`.tc-detail` is already `display: block` per line, confirmed sufficient by
the existing two-line behavior; this item exists only to record the
threshold-driven container sizing check.

**Contract**: No CSS rule changes in this phase (verify in Manual
Verification that a third `.tc-detail` line doesn't visually overflow the
`90×60` cells at the raised threshold; if it does, the fix is the
`min-height` bump already established as precedent in the PUL-50 plan's
addendum, not a new pattern).

#### 3. `tests/e2e/conftest.py` — `pct` in treemap fixtures

**Intent**: Give the E2E suite real `pct` values so the live-server test can
assert the new line renders with real data, including one position with no
`pct` to exercise the `brak danych` fallback.

**Contract**: Add a `"pct": <float>` key to each position in
`_FAKE_TREEMAP_LATEST`, `_FAKE_TREEMAP_IKZE_LATEST` (lines 58-65, 84-90).
Leave one position (e.g. the existing `"NEW"` ticker, already used to test
the daily-change `no-data` case) without a `pct` key, so it doubles as the
since-purchase `brak danych` case too. `_FAKE_TREEMAP_PRIOR`/
`_FAKE_TREEMAP_IKZE_PRIOR` need no `pct` additions — since-purchase is
computed from today's positions only.

#### 4. `tests/e2e/test_portfolio_treemap.py` — labeled-line assertions

**Intent**: Assert the new labels and since-purchase line render against
real fixture data end to end.

**Contract**: Extend
`test_admin_can_open_treemap_and_see_positions_rendered_with_pl_deltas` to
assert `main_container` contains `"D/D:"`, `"Total:"`, and `"Zakup:"` text,
and that the `NEW` ticker's cell contains `"Zakup: brak danych"`.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual Verification:

- Open the treemap as admin: cells large enough show `D/D:`, `Total:`, and
  `Zakup:` lines, each with correct sign and values against known data.
- A position with no purchase-cost data available shows `Zakup: brak danych`.
- No visual overflow or clipping of the third line at the `90×60` threshold;
  cells below it still show ticker-only, same as today.

**Implementation Note**: Pause here for manual confirmation before
proceeding to Phase 3.

**Deviation from plan (discovered during manual verification)**: the
`.tc-tooltip` hover tooltip (pre-existing from PUL-45/50, originally planned
to gain the third line per item #1 above) was removed entirely instead of
extended. Manual testing surfaced a sequence of hover/touch issues with the
tooltip approach (CSS `:hover` unreliable on tap, viewport-overflow growing
the page, narrow-cell text truncation) that converged on a product decision
to replace hover-tooltip-on-cell with a click-triggered modal (see Phase 3's
rewritten design below) — the user explicitly requested this pivot mid-phase.
The inline `.tc-detail` lines (item #1's other half) were kept and their
width gate raised from `cell.width >= 90` to `cell.width >= 150` (height gate
unchanged at `>= 60`) since the new `Zakup:` line is longer than the other
two and was getting ellipsis-truncated in narrow cells at the original
width. `touch-action: manipulation` was added to `.treemap-cell` to keep tap
interactions (now driving Phase 3's click-to-open-modal) free of double-tap-
zoom interference.

---

## Phase 3: Frontend — click-to-open popup with summary + navigate

### Overview

**Rewritten mid-implementation per explicit user request** (original design
below this note, kept for history, superseded). Original design: bold hover
outline + direct click-to-filter navigation to the announcements table.
New design: hover outline is kept as a visual affordance, but clicking any
cell now opens a centered modal popup showing that position's full info
(ticker + the same `D/D:`/`Total:`/`Zakup:` lines), with a single button —
"Ostatnie podsumowania" — inside the popup. Only clicking that button
navigates to the announcements table pre-filtered by the popup's ticker,
reusing the existing filter/fetch/view-switch machinery untouched. The popup
closes via an explicit close (X) button, clicking outside the popup, or
Esc — without navigating anywhere.

### Changes Required:

#### 1. `static/index.html` — hover outline CSS

**Intent**: Add a bold border highlight on hover, signaling the cell is
clickable.

**Contract**: Add a `.treemap-cell:hover { outline: 3px solid #fff;
outline-offset: -3px; }` rule (drawn inward so it isn't clipped by
neighboring absolutely-positioned cells). No JS-driven active-state class
needed for this — it's a plain CSS `:hover`, since (unlike the removed
tooltip) it's a non-essential visual cue, not something touch devices need
to reach via tap.

#### 2. `static/index.html` — popup modal markup + CSS

**Intent**: A single reusable modal element, populated per-click, centered
on screen, dismissible three ways.

**Contract**: Add one hidden modal container to the DOM (created once,
analogous to how `treemap-view` itself is injected in
`injectAdminOnlyChrome()`), with a backdrop, a close (X) button, a ticker
heading, the three detail lines, and the "Ostatnie podsumowania" button.
CSS: backdrop fixed full-screen with semi-transparent fill, modal box
centered via flexbox or fixed+transform, above all other content
(`z-index` higher than the treemap's existing `30`).

#### 3. `static/index.html` — click-to-open wiring

**Intent**: Clicking any cell opens the popup populated with that cell's
data; the popup's button navigates, closing the popup first.

**Contract**: In `renderTreemap()`'s cell template, add
`data-ticker="${esc(item.ticker)}"` to the `<div class="treemap-cell ...">`
element (each cell already has the three text values in scope at render
time — store them via `data-*` attributes or look them up from the
in-memory `_treemapData` by ticker when the popup opens). Add one delegated
`click` listener per wallet container (`$('treemap-main')`,
`$('treemap-ikze')`), attached once in `injectAdminOnlyChrome()` so it
survives re-renders: `event.target.closest('.treemap-cell')` → read
`.dataset.ticker` → populate and show the popup. The popup's "Ostatnie
podsumowania" button click handler: hide the popup, set
`$('f-ticker').value = ticker`, `currentPage = 1`, call
`showAnnouncementsView()`, then `fetchAnnouncements()`. The popup's close
(X) button, a backdrop click, and an `Escape` keydown all just hide the
popup without touching any filter state.

#### 4. `tests/e2e/test_portfolio_treemap.py` — popup + hover coverage

**Intent**: Assert the new interactions end to end against real fixture
data.

**Contract**: Add `test_hovering_treemap_cell_shows_outline` — hover a cell,
assert `to_have_css("outline-style", "solid")`. Add
`test_clicking_treemap_cell_opens_popup_with_summary` — click a cell with a
known ticker, assert the popup is visible and contains that ticker's
`D/D:`/`Total:`/`Zakup:` text. Add
`test_clicking_popup_button_navigates_to_filtered_announcements` — open the
popup, click "Ostatnie podsumowania", assert the announcements view becomes
visible, `#f-ticker` has that ticker's value, and a request to
`/announcements` containing the ticker fires (mirroring the
`page.expect_response` pattern in
`test_user_role_never_triggers_treemap_network_request`,
`tests/e2e/test_portfolio_treemap.py:69-77`). Add
`test_closing_popup_does_not_navigate` — open the popup, close it via the X
button, assert the announcements view was never shown and no `/announcements`
request fired. Add
`test_clicking_treemap_cell_preserves_other_active_filters` — set the
company filter, open the treemap, click a cell, click "Ostatnie
podsumowania", assert the company filter's value is unchanged after
navigating.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual Verification:

- Hovering any cell shows a bold white outline.
- Clicking any cell opens a centered popup showing that position's ticker
  and `D/D:`/`Total:`/`Zakup:` lines.
- Clicking "Ostatnie podsumowania" in the popup navigates to the
  announcements table with that ticker filled into the ticker filter and
  matching results displayed.
- Closing the popup (X, backdrop click, or Esc) does not navigate anywhere.
- Clicking a cell while another filter (e.g. company) is already set, then
  using the popup's button, leaves that filter intact alongside the new
  ticker filter.

**Deviation from plan (discovered during manual verification)**: the
treemap's `item.ticker` field is actually a company display name sourced
from XTB screenshot OCR (e.g. "Toya"), not an exchange ticker symbol (e.g.
"TOA") — confirmed against real announcement data
(`{"company":"Toya SA","ticker":"TOA"}`). The announcements endpoint's
`ticker` filter is an exact match against the real symbol, so setting
`#f-ticker` from the popup (as originally written above) silently returned
zero results for every position. Fixed: the popup's "Ostatnie podsumowania"
button now sets `#f-company` (a case-insensitive partial match) instead of
`#f-ticker`. The "preserves other filters" test/manual-check was updated to
verify `#f-ticker` (now the untouched field) instead of `#f-company` (now
the field this feature sets).

**Out of scope, deferred**: the user separately noted that switching between
views (Treemapa/Historia/Ogłoszenia, including this popup's navigation)
never updates the browser URL — confirmed as a pre-existing, app-wide gap
(the app only `pushState`s for announcements pagination, not view-switching)
that predates this change and isn't specific to the treemap. Decided to skip
it here and track separately rather than expand this plan's scope.

---

<details>
<summary>Superseded original Phase 3 design (kept for history)</summary>

## Phase 3 (original): Frontend — hover highlight & click-to-filter

### Overview

Add a bold hover outline to cells, and wire a click on any cell to set the
announcements ticker filter to that cell's ticker and navigate there,
reusing the existing filter/fetch/view-switch machinery untouched.

### Changes Required:

#### 1. `static/index.html` — hover outline CSS

**Intent**: Add a bold border highlight on hover, on top of the existing
tooltip-reveal behavior.

**Contract**: Extend the existing `.treemap-cell:hover` rule
(`static/index.html:246`, currently `overflow: visible; z-index: 5;`) to add
`outline: 3px solid #fff; outline-offset: -3px;` — drawn inward so it doesn't
get clipped by neighboring absolutely-positioned cells.

#### 2. `static/index.html` — cell ticker attribute

**Intent**: Make each cell's ticker available to a click handler without
re-deriving it from rendered text.

**Contract**: In `renderTreemap()`'s cell template
(`static/index.html:1033-1034`), add `data-ticker="${esc(item.ticker)}"` to
the `<div class="treemap-cell ...">` element.

#### 3. `static/index.html` — click-to-filter wiring

**Intent**: Clicking any cell sets the ticker filter and navigates to the
announcements view, without touching any other active filter.

**Contract**: In `injectAdminOnlyChrome()`, after the two treemap containers
are created (`static/index.html:773-788`), add one delegated `click`
listener per container (`$('treemap-main')`, `$('treemap-ikze')`):
`event.target.closest('.treemap-cell')` → read `.dataset.ticker`; if present,
set `$('f-ticker').value = ticker`, `currentPage = 1`, call
`showAnnouncementsView()`, then `fetchAnnouncements()`. Attaching the
listener once on the persistent container (rather than per-cell on every
render) means it survives `renderTreemap()` being called again on resize
with no rebinding needed.

#### 4. `tests/e2e/test_portfolio_treemap.py` — hover & click coverage

**Intent**: Assert both new interactions end to end against real fixture
data.

**Contract**: Add `test_hovering_treemap_cell_shows_outline` — hover a cell,
assert `to_have_css("outline-style", "solid")`. Add
`test_clicking_treemap_cell_navigates_to_filtered_announcements` — click a
cell with a known ticker (e.g. `PKO`), assert the announcements view becomes
visible, `#f-ticker` has that ticker's value, and a request to
`/announcements` containing `ticker=PKO` fires (mirroring the
`page.expect_response` pattern already used in
`test_user_role_never_triggers_treemap_network_request`,
`tests/e2e/test_portfolio_treemap.py:69-77`). Add
`test_clicking_treemap_cell_preserves_other_active_filters` — set the
company filter, open the treemap, click a cell, assert the company filter's
value is unchanged after navigating.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual Verification:

- Hovering any cell shows a bold white outline in addition to the existing
  tooltip.
- Clicking any cell navigates to the announcements table with that ticker
  filled into the ticker filter and matching results displayed.
- Clicking a cell while another filter (e.g. company) is already set leaves
  that filter intact alongside the new ticker filter.

</details>

---

## Testing Strategy

### Unit Tests:

- `tests/test_portfolio_treemap.py`: since-purchase happy path, missing
  `pct`, and `pct == -100` division-by-zero collapse — all three exercised
  as pure-function cases with no BQ/network dependency.

### Integration Tests:

- None beyond the existing `tests/test_api.py` treemap suite, which is
  unaffected by this change (`TreemapPosition`'s new fields default to
  `None` and existing exact-equality assertions there don't need updating
  unless an implementer chooses to assert the new fields explicitly — not
  required by this plan since `tests/test_portfolio_treemap.py` already
  covers the computation).

### Manual Testing Steps:

1. With real production data, open the treemap as admin and confirm all
   three labeled lines render with plausible values against known purchase
   history.
2. Hover several cells of different sizes to confirm the outline appears
   consistently alongside the existing tooltip.
3. Click cells across both wallets to confirm each navigates to the
   announcements table correctly filtered by that cell's ticker.

## Performance Considerations

No change — same number of BQ reads, same per-position computation cost
class (now three derived fields instead of two, still O(1) per position).

## Migration Notes

None — no schema or data migration; `pct` already exists in every
`positions_json` row.

## References

- Parent changes (shipped, archived):
  `context/archive/2026-06-20-admin-ui-portfolio-treemap/`,
  `context/archive/2026-06-20-portfolio-treemap-multi-wallet/`
- Inverse formula precedent: `src/portfolio_thread_composer.py:48-52`
- Existing model/endpoint: `src/api.py:105-111,235-257`
- Existing render/fetch pattern: `static/index.html:952-1036`
- Existing filter/view-switch machinery reused as-is:
  `static/index.html:794-801,828-866`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backend — since-purchase P&L computation

#### Automated

- [x] 1.1 Updated pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q` — 5c5effc
- [x] 1.2 Full test suite still passes: `uv run pytest --tb=short` — 5c5effc

#### Manual

- [x] 1.3 curl with admin key returns `since_purchase_pct`/`since_purchase_pln` per position with plausible values — 5c5effc

### Phase 2: Frontend — labeled lines & since-purchase display

#### Automated

- [x] 2.1 Full test suite passes: `uv run pytest --tb=short`
- [x] 2.2 E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual

- [x] 2.3 D/D:, Total:, Zakup: lines render correctly on large-enough cells
- [x] 2.4 A position with no purchase-cost data shows "Zakup: brak danych"
- [x] 2.5 No overflow/clipping at the 150×60 threshold; ticker-only fallback still works below it

### Phase 3: Frontend — click-to-open popup with summary + navigate

#### Automated

- [x] 3.1 Full test suite passes: `uv run pytest --tb=short`
- [x] 3.2 E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual

- [x] 3.3 Hovering any cell shows the bold white outline
- [x] 3.4 Clicking any cell opens a centered popup with that position's D/D:/Total:/Zakup: lines
- [x] 3.5 Clicking "Ostatnie podsumowania" in the popup navigates to announcements filtered by that ticker
- [x] 3.6 Closing the popup (X, backdrop click, or Esc) does not navigate anywhere
- [x] 3.7 Clicking a cell with another filter already active, then using the popup's button, leaves that filter intact
