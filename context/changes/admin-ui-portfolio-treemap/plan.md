# Admin UI portfolio treemap with daily P&L colouring — Implementation Plan

## Overview

Add a treemap visualisation of the admin's portfolio: rectangles proportional to
each position's value, coloured green/red/gray by daily change, reachable from the
profile menu. Backed by a new `GET /admin/portfolio/treemap` endpoint that computes
per-position daily deltas at read time from two existing `portfolio_snapshots` rows
— no schema change.

## Current State Analysis

- `portfolio_snapshots` (BigQuery) already stores the full position list per
  wallet per day in `positions_json`, written exclusively by the manual
  `/portfolio-xpost` skill (`.claude/skills/portfolio-xpost/SKILL.md`, Step 5.1).
  **Correction to `research.md`**: `positions_json` is a JSON **object**, not a
  bare list — `{"positions": [{"ticker", "value", "pct"}, ...], "media_attached": bool}`
  (confirmed at `SKILL.md:287-300`).
- `db/bigquery.py:273-310` has `get_latest_snapshot_before(wallet, before_date)` —
  one wallet at a time, strict `<` on date. There is no function to find the
  most-recently-uploaded wallet across all wallets — needed for v1's "whichever
  wallet was last uploaded" auto-detect (multi-wallet toggle is explicitly out of
  scope per the ticket).
- `src/api.py` has an established `Role`/`_require_admin` pattern (lines 41-56) and
  admin-endpoint convention (`GET /admin/x-posts`, lines 197-217): plain
  `ConfigDict(extra="ignore")` Pydantic response model, `BigQueryError` → 500.
- `static/index.html` has a profile-menu dropdown (shipped in PUL-47, merged) and
  one precedent for "menu item + dedicated view" (PUL-44's `x-history-btn` /
  `x-history-view`, lines 645-735): menu `<li>` inserted before the logout `<li>`,
  view inserted via `insertAdjacentElement('afterend', ...)`, toggled via plain
  `style.display`, fetch-on-open.
- **No static file serving exists.** `create_app()` (`src/api.py:110-121`) reads
  `static/index.html` once into a string and serves it from a single `GET /` route
  — there is no `StaticFiles` mount and no separate JS files are ever served. No JS
  test runner (vitest/jest) exists anywhere outside an unrelated tool's
  `node_modules`. Node 24 is available on the dev machine and in CI (per project
  history, PUL-36 added `ci-node24`).

## Desired End State

An admin clicks "Treemapa portfela" in the profile menu and sees a treemap of the
most-recently-uploaded wallet's positions: rectangle area proportional to position
value, colour reflecting daily direction (green/red/gray), ticker + daily %  +
daily PLN shown inside (ticker-only if the rectangle is too small). The view works
today against real data already written by the existing `/portfolio-xpost` skill —
it does not need to wait for PUL-43.

Verify by: logging in as admin, opening the menu, clicking "Treemapa portfela", and
seeing rectangles sized/coloured correctly for the latest uploaded wallet snapshot.

### Key Discoveries:

- `db/bigquery.py:191-201` — `portfolio_snapshots` schema; no per-position delta
  field exists or is needed (computed read-time).
- `SKILL.md:287-300` — `positions_json` is `{"positions": [...], "media_attached": bool}`.
- `src/api.py:68-99,197-227` — response-model and admin-endpoint conventions to follow.
- `static/index.html:645-735` — exact menu-item/view/fetch pattern to mirror.
- `src/api.py:110-121` — no existing static-file mount; one new `StaticFiles` mount
  is required to serve a standalone, unit-testable JS module.

## What We're NOT Doing

- No new BigQuery table or schema migration.
- No changes to the `/portfolio-xpost` skill or `positions_json`'s write-time shape.
- No multi-wallet toggle or `?wallet=` override — strictly auto-detect the
  most-recently-uploaded wallet (out of scope per ticket).
- No historical treemap / time-series view.
- No auto-refresh wiring tied to PUL-43's upload flow (PUL-43 doesn't exist yet;
  this view fetches on open only — PUL-43's implementation will call into this
  fetch function once it exists).
- No d3 or other charting library — hand-rolled squarified layout only.
- No tooltip/hover detail for truncated rectangles — static ticker-only fallback.

## Implementation Approach

Three phases, backend-to-frontend:

1. **Backend**: a pure delta-computation function + one new BQ query + the
   `GET /admin/portfolio/treemap` endpoint, fully unit-testable without a browser.
2. **Frontend layout module**: the squarified-treemap algorithm extracted into its
   own file (`static/js/treemap-layout.js`) so it can be unit-tested with Node's
   built-in test runner — this requires adding a `StaticFiles` mount, the one piece
   of new infrastructure in this plan.
3. **Frontend UI integration**: menu item, dedicated view (mirroring `x-history`),
   fetch wiring, rendering, and colour/truncation CSS.

## Critical Implementation Details

**Nullable deltas signal "no prior data."** The ticket's response shape is
`{ticker, position_value_pln, daily_change_pln, daily_change_pct}` with no extra
field for "no comparison available" (first-ever snapshot for a wallet, or a ticker
absent from yesterday's `positions_json`). Per the confirmed decision, this plan
makes `daily_change_pln`/`daily_change_pct` `float | None` — `None` means "no prior
data" (render gray + a distinct visual marker), `0.0` means "genuinely flat"
(render gray, no marker). The frontend must treat `null` and `0` as different CSS
states even though both are gray.

**`StaticFiles` mount is additive, not a rewrite.** `create_app()` currently has no
mount at all. Add exactly one `app.mount("/static", StaticFiles(directory="static"), name="static")`
call — this does not change the existing `GET /` route (which still reads and
returns `index.html` as a full HTML string) or any other route. Only
`static/js/treemap-layout.js` needs to be reachable this way; nothing else in
`static/` currently needs serving, so no other behavior changes.

## Phase 1: Backend — treemap data & endpoint

### Overview

Add the "latest snapshot across all wallets" query, a pure per-ticker delta
computation function, and the `GET /admin/portfolio/treemap` endpoint that wires
them together.

### Changes Required:

#### 1. `db/bigquery.py` — most-recent-snapshot query

**Intent**: Find the wallet+date of the most recently uploaded snapshot, across all
wallets, so the endpoint can auto-detect "whichever wallet was last uploaded"
without a `?wallet=` parameter.

**Contract**: New function `get_latest_snapshot() -> dict | None`, same return
shape as `get_latest_snapshot_before` (`snapshot_id`, `wallet`, `snapshot_date`,
`total_value`, `currency`, `day_change_abs`, `day_change_pct`, `positions_json`).
Query: `SELECT ... FROM portfolio_snapshots ORDER BY snapshot_date DESC, created_at DESC LIMIT 1`.
Returns `None` if the table is empty (no `/portfolio-xpost` run has ever happened).

#### 2. `src/portfolio_treemap.py` (new module) — pure delta computation

**Intent**: Parse the two snapshot rows' `positions_json` and compute each of
today's positions' value + daily delta vs. yesterday, matched by ticker. Pure
function, no BQ/network access, so it's unit-testable without mocks — mirrors the
two-layer test split already used for `portfolio_thread_composer.py`.

**Contract**: `compute_treemap_positions(today_positions_json: str, yesterday_positions_json: str | None) -> list[dict]`.
Each dict: `{"ticker": str, "position_value_pln": float, "daily_change_pln": float | None, "daily_change_pct": float | None}`.

- Parse `positions_json` as `{"positions": [{"ticker", "value", "pct"}, ...], ...}`
  — ignore `media_attached` and `pct`, only `ticker`/`value` matter here.
- For each ticker in *today's* `positions` list, look up the same ticker in
  *yesterday's* `positions` list (if `yesterday_positions_json` is not `None`).
  - Match found: `daily_change_pln = today.value - yesterday.value`;
    `daily_change_pct = daily_change_pln / yesterday.value * 100` if
    `yesterday.value != 0`, else `None` (avoid division by zero — treat as no
    comparison available).
  - No match (new ticker, or `yesterday_positions_json is None`): both deltas `None`.
- Malformed/unparseable `positions_json` (defensive — should not happen given the
  skill's write path, but the function must not crash the endpoint on bad data):
  return `[]` rather than raising.
- Output order: same order as today's `positions` list (no sorting here — sorting
  by value for layout purposes is the frontend's job in Phase 2).

#### 3. `src/api.py` — endpoint

**Intent**: Wire the new query + pure function into an admin-only endpoint
matching the ticket's exact response shape.

**Contract**: New Pydantic model `TreemapPosition(BaseModel)` with
`model_config = ConfigDict(extra="ignore")` and fields `ticker: str`,
`position_value_pln: float`, `daily_change_pln: float | None`,
`daily_change_pct: float | None` — same style as `XPostAdmin`. New route
`GET /admin/portfolio/treemap`, `role: Role = Depends(_require_admin)`, returning
`list[dict]` (via `.model_dump()`):

- Call `get_latest_snapshot()`. If `None` → return `[]` (empty portfolio / no
  upload yet — frontend renders its own empty-state message, no special signal
  needed from the backend).
- Else call `get_latest_snapshot_before(wallet, snapshot_date)` for the prior row
  (may be `None` on the very first snapshot for that wallet).
- Call `compute_treemap_positions(today["positions_json"], prior["positions_json"] if prior else None)`.
- Wrap `BigQueryError` → 500, matching every other admin endpoint's error handling.

### Success Criteria:

#### Automated Verification:

- New pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q`
- New BQ-layer test passes: `uv run pytest tests/test_bigquery.py -q -k get_latest_snapshot`
- New endpoint tests pass: `uv run pytest tests/test_api.py -q -k treemap`
- Full test suite still passes: `uv run pytest --tb=short`

#### Manual Verification:

- `curl -H "X-API-Key: <admin-key>" localhost:8000/admin/portfolio/treemap` returns
  the real latest wallet's positions with plausible deltas against yesterday's
  data.
- Same request with a `user`-role key returns 403.

---

## Phase 2: Frontend — treemap layout module

### Overview

Extract the squarified-treemap layout algorithm into its own file so it is
reachable by the browser and independently unit-testable with Node's built-in test
runner — the one piece of new serving infrastructure this plan needs.

### Changes Required:

#### 1. `static/js/treemap-layout.js` (new file)

**Intent**: Pure function turning `{ticker, position_value_pln, ...}` items + a
container size into laid-out rectangles, using the squarified treemap algorithm
(Bruls/Huizing/van Wijk 1999: sort by value descending, recursively split into
rows/columns minimizing worst aspect ratio).

**Contract**: `computeTreemapLayout(items, containerWidth, containerHeight) -> Array<{item, x, y, width, height}>`.
Pure — no DOM access. End the file with a CommonJS export guard so the same file
works both as a browser `<script>` global and as a Node `require()` target for
tests:

```js
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { computeTreemapLayout };
}
```

#### 2. `src/api.py` — static file mount

**Intent**: Make `static/js/treemap-layout.js` reachable by the browser. This is
the only new route-level change; `GET /` keeps serving `index.html` exactly as
before.

**Contract**: Add `from fastapi.staticfiles import StaticFiles` and one
`app.mount("/static", StaticFiles(directory="static"), name="static")` call in
`create_app()`, after the existing routes are defined (mount order doesn't matter
relative to `@app.get` routes, but add it near the other app-setup statements for
readability).

#### 3. `tests/test_treemap_layout.js` (new file)

**Intent**: Unit-test the layout function in isolation — area proportionality,
row/column placement, and that all items are placed with positive width/height.

**Contract**: Use Node's built-in `node:test` + `node:assert` (no new dependency).
`require('../static/js/treemap-layout.js')`. Cases: single item fills the whole
container; two equal-value items split ~50/50; relative areas are proportional to
input values within a small tolerance; zero items returns `[]`.

### Success Criteria:

#### Automated Verification:

- Layout unit tests pass: `node --test tests/test_treemap_layout.js`
- `static/js/treemap-layout.js` is reachable: `curl -I localhost:8000/static/js/treemap-layout.js` returns 200
- Existing test suite unaffected: `uv run pytest --tb=short`

#### Manual Verification:

- Loading `/` in a browser still renders the existing dashboard with no console
  errors (mount doesn't interfere with the `GET /` HTML route).

---

## Phase 3: Frontend — UI integration

### Overview

Wire the treemap into the profile menu and a dedicated view, fetch data from the
new endpoint, lay it out with `computeTreemapLayout`, and render colour-coded,
truncation-aware rectangles.

### Changes Required:

#### 1. `static/index.html` — script include + menu item + view shell

**Intent**: Follow the `x-history` precedent exactly: menu `<li>` before logout,
view inserted after the previous view, toggled via `style.display`, closing the
menu and fetching on open.

**Contract**:
- Add `<script src="/static/js/treemap-layout.js"></script>` before the main
  inline `<script>` block, so `computeTreemapLayout` is a global by the time the
  inline script runs.
- In `injectAdminOnlyChrome(r)` (`static/index.html:645-721`), following the
  `x-history-btn`/`x-history-view` pattern: add a `treemap-btn` `<li>` inserted
  into `profileMenu` before `logoutLi` (after the x-history item, so menu order is
  Historia postów X → Treemapa portfela → Wyloguj), and a `treemap-view` `<div>`
  inserted via `xView.insertAdjacentElement('afterend', view)` (i.e. after
  `x-history-view`). View content: a single empty `#treemap-container` div —
  `fetchTreemap()` sets its `innerHTML` directly to either the rendered cells or
  the empty-state text (see §2 below); no separate empty-state element.
- Extend `showAnnouncementsView()` and `showXHistoryView()` to also hide
  `treemap-view` (mirroring how `showAnnouncementsView` already hides
  `x-history-view`), and add `showTreemapView()` following `showXHistoryView`'s
  shape: hide the other two views, show `treemap-view`, close the profile menu,
  call the fetch function.
- `$('treemap-btn').addEventListener('click', showTreemapView)`.

#### 2. `static/index.html` — fetch + render

**Intent**: Fetch `GET /admin/portfolio/treemap`, lay out and render rectangles, or
show an empty-state message if the list is empty.

**Contract**: `fetchTreemap()` — same shape as `fetchXPosts()` (`X-API-Key` header,
401 → `doLogout()`, non-2xx → inline error message in `#treemap-container`). On
success: if `data.length === 0`, set `#treemap-container.innerHTML` to "Brak danych
portfela" and return. Else call `renderTreemap(data)`:

- Sort `data` by `position_value_pln` descending (squarified layout expects
  pre-sorted input).
- Measure `#treemap-container`'s actual rendered width/height (`clientWidth`/`clientHeight`)
  and call `computeTreemapLayout(data, width, height)`.
- Build all cells as one HTML string and assign it to `#treemap-container.innerHTML`
  in one shot (replacing, not appending to, any previous render — mirrors
  `renderXPostsTable`'s `tbody.innerHTML = data.map(...).join('')` pattern at
  `static/index.html:820` — this matters because `showTreemapView()` re-fetches and
  re-renders every time the menu item is clicked, so stale cells from a prior open
  must not accumulate). Each cell is an absolutely-positioned `div.treemap-cell`
  inside a `position: relative` `#treemap-container`, with inline `left/top/width/height`
  from the layout result.
- Colour class: `positive` if `item.daily_change_pln > 0`, `negative` if `< 0`,
  `neutral` if `=== 0`, `no-data` if `item.daily_change_pln === null`.
- Content: always show `item.ticker`. If `width >= 60 && height >= 30`, also show
  `daily_change_pct`/`daily_change_pln` as text (formatted with sign, e.g. `+2.3%` /
  `−150 PLN`), or "brak danych" text instead of the two numbers when
  `daily_change_pln === null`. Below the threshold, ticker only.

#### 3. `static/index.html` — CSS

**Intent**: Colour-by-class styling consistent with the rest of the file's
class-based approach (no inline colours), plus the container/cell positioning
needed for absolute layout.

**Contract**: Add `.treemap-cell` (absolute position, border, padding, overflow
hidden, small font) and four modifier classes: `.positive` (green background),
`.negative` (red background), `.neutral` (gray background), `.no-data` (gray
background + a visual affordance distinguishing it from `.neutral`, e.g. a dashed
border or diagonal-stripe pattern — pure colour alone must not be the only signal,
per research's accessibility note). `#treemap-container` gets `position: relative`
and a fixed-ish height (e.g. `min-height: 400px`) so percentage/absolute children
have something to lay out against.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- Layout unit tests still pass: `node --test tests/test_treemap_layout.js`

#### Manual Verification:

- Log in as admin, open the profile menu, see "Treemapa portfela" between "Historia
  postów X" and "Wyloguj".
- Click it: treemap renders with rectangle areas visually proportional to position
  value, green/red/gray colouring matching the sign of each position's daily
  change, and ticker + % + PLN text inside rectangles large enough to fit it.
- Shrink the browser window, then close and reopen the treemap view (no resize
  listener is wired — `showTreemapView()` re-fetches and re-measures on each
  open), or test directly with a wallet that has many positions, and confirm
  small rectangles fall back to ticker-only text.
- Log in as a non-admin user: confirm the menu item is absent and a direct
  `curl` to the endpoint with a user-role key returns 403.
- If there's currently no prior-day snapshot for the latest wallet (or a ticker is
  new today), confirm those rectangles render with the distinct "no data" gray
  styling, not plain neutral gray.

---

## Testing Strategy

### Unit Tests:

- `tests/test_portfolio_treemap.py`: matched ticker positive/negative/zero delta;
  new ticker (no match) → `None` deltas; `yesterday_positions_json is None` → all
  `None` deltas; division-by-zero guard (`yesterday.value == 0`); malformed JSON →
  `[]`.
- `tests/test_treemap_layout.js`: proportional areas, all-items-placed invariant,
  zero-items edge case.

### Integration Tests:

- `tests/test_bigquery.py`: `get_latest_snapshot()` mocked-client test (empty
  table → `None`, multiple wallets → correct tie-break on `created_at DESC`).
- `tests/test_api.py`: `GET /admin/portfolio/treemap` — 200 for admin with mocked
  DB calls, 403 for user role, 401 for missing key, empty-table → `[]`.

### Manual Testing Steps:

1. With real production data (today's `main` wallet snapshot already exists per
   research), open the treemap as admin and visually confirm proportionality and
   colouring against the known position values.
2. Confirm the empty-state message appears if you point at an environment with no
   snapshots (e.g. a fresh local BQ dataset).
3. Resize the window to trigger the small-rectangle ticker-only fallback.

## Performance Considerations

At most a few dozen positions per wallet (XTB screenshot scope) — squarified
layout is O(n log n) for the sort plus O(n) for placement, trivial at this scale.
No caching needed; endpoint does exactly two BQ row reads per request.

## Migration Notes

None — no schema or data migration; purely additive endpoint and frontend.

## References

- Related research: `context/changes/admin-ui-portfolio-treemap/research.md`
- Persist-time shape of `positions_json`: `.claude/skills/portfolio-xpost/SKILL.md:287-300`
- Menu/view precedent: `static/index.html:645-735`
- Admin endpoint precedent: `src/api.py:197-217`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backend — treemap data & endpoint

#### Automated

- [x] 1.1 New pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q` — 0c97904
- [x] 1.2 New BQ-layer test passes: `uv run pytest tests/test_bigquery.py -q -k get_latest_snapshot` — 0c97904
- [x] 1.3 New endpoint tests pass: `uv run pytest tests/test_api.py -q -k treemap` — 0c97904
- [x] 1.4 Full test suite still passes: `uv run pytest --tb=short` — 0c97904

#### Manual

- [x] 1.5 curl with admin key returns plausible positions/deltas — 0c97904
- [x] 1.6 curl with user key returns 403 — 0c97904

### Phase 2: Frontend — treemap layout module

#### Automated

- [ ] 2.1 Layout unit tests pass: `node --test tests/test_treemap_layout.js`
- [ ] 2.2 `static/js/treemap-layout.js` reachable: `curl -I localhost:8000/static/js/treemap-layout.js` returns 200
- [ ] 2.3 Existing test suite unaffected: `uv run pytest --tb=short`

#### Manual

- [ ] 2.4 `/` still renders dashboard with no console errors

### Phase 3: Frontend — UI integration

#### Automated

- [ ] 3.1 Full test suite passes: `uv run pytest --tb=short`
- [ ] 3.2 Layout unit tests still pass: `node --test tests/test_treemap_layout.js`

#### Manual

- [ ] 3.3 Menu item appears between x-history and logout
- [ ] 3.4 Treemap renders with proportional, correctly-coloured rectangles
- [ ] 3.5 Small rectangles fall back to ticker-only text (reopen view after resize)
- [ ] 3.6 Non-admin: menu item absent, endpoint returns 403
- [ ] 3.7 No-prior-data positions render with distinct "no data" styling
