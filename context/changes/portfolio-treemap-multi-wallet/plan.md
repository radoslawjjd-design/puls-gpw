# Portfolio treemap — main + IKZE side-by-side with portfolio-share % Implementation Plan

## Overview

Extend PUL-45's single-wallet auto-detect treemap into a fixed two-wallet view:
`main` and `ikze` rendered side by side, each under its own header, with every
cell additionally showing what % of that wallet's total portfolio value the
position represents and its absolute PLN value — on top of the existing daily
%/PLN change.

## Current State Analysis

- `GET /admin/portfolio/treemap` (`src/api.py:231-247`) currently auto-detects
  "whichever wallet was most recently uploaded, across all wallets" via
  `get_latest_snapshot()` (`db/bigquery.py:313-343`), and returns a flat
  `list[TreemapPosition]`. There is no per-wallet "give me this wallet's
  latest snapshot" query — `get_latest_snapshot_before(wallet, before_date)`
  (`db/bigquery.py:273-310`) only answers "before a given date."
- `compute_treemap_positions(today_json, yesterday_json)`
  (`src/portfolio_treemap.py`) returns `{ticker, position_value_pln,
  daily_change_pln, daily_change_pct}` per position. It has no concept of the
  wallet's total value, so it cannot compute a share-of-portfolio percentage
  today.
- `portfolio_snapshots.total_value` (`db/bigquery.py:195`) already exists and,
  per the `/portfolio-xpost` skill's documented invariant
  (`.claude/skills/portfolio-xpost/SKILL.md:175`,
  `sum(positions) + free_cash == total_value`), is exactly "the wallet's total
  portfolio value" — the correct denominator for the new share %.
- `position_value_pln` is already returned by the API today but is **not
  rendered** anywhere in the UI — only the daily delta is shown
  (`static/index.html:925-954`). Showing the absolute PLN value is therefore a
  frontend-only addition; no new backend field is needed for it.
- `static/js/treemap-layout.js`'s `computeTreemapLayout(items, width, height)`
  is a pure, container-agnostic function with no wallet awareness — it can be
  called twice (once per wallet's container) with zero changes.
- `injectAdminOnlyChrome()` (`static/index.html:660-753`) currently wires one
  menu item → one view → one `#treemap-container`. `fetchTreemap()` /
  `renderTreemap()` (`static/index.html:906-954`) assume a single flat array
  and a single container.
- Existing coverage to update in place (no new test files): `tests/test_bigquery.py`
  (`get_latest_snapshot` tests), `tests/test_portfolio_treemap.py`,
  `tests/test_api.py` (treemap section, `tests/test_api.py:273-345`),
  `tests/e2e/conftest.py` (`_FAKE_TREEMAP_LATEST`/`_FAKE_TREEMAP_PRIOR`,
  `tests/e2e/conftest.py:54-77`), `tests/e2e/test_portfolio_treemap.py`.

## Desired End State

An admin clicks "Treemapa portfela" and sees two headed treemaps side by
side: "Portfel główny" (main) on the left, "IKZE" on the right. Each
rectangle's area is proportional to position value within its own wallet;
colour reflects daily direction as today; and — when the rectangle is large
enough — a second detail line now also shows the position's % share of that
wallet's total portfolio value and its absolute PLN value, alongside the
existing daily %/PLN line. A wallet with no snapshot ever uploaded shows its
header with an empty-state message in its own container while the other
wallet renders normally. On narrow viewports the two treemaps stack
vertically instead of squeezing side by side.

Verify by: logging in as admin, opening "Treemapa portfela", and seeing both
"Portfel główny" and "IKZE" headers with independently-laid-out, correctly
proportioned and coloured rectangles, each showing ticker + daily change +
share%/value once large enough.

### Key Discoveries:

- `db/bigquery.py:195` / `SKILL.md:175` — `total_value` already equals
  positions + cash; it is the correct denominator for the new share %, no new
  computation source needed.
- `static/index.html:925-954` — `position_value_pln` is fetched but never
  displayed; showing it is a template change only.
- `static/js/treemap-layout.js` — container-agnostic, reusable per wallet with
  zero modification.
- `tests/test_api.py:275-340` and `tests/e2e/conftest.py:54-77` — both hardcode
  the current single-wallet, flat-array response shape and must be rewritten
  (not just extended) once the endpoint returns a keyed object.

## What We're NOT Doing

- No support for `short`/`long` wallets — strictly `main` and `ikze`, per the
  ticket's exact scope.
- No `?wallet=` override or wallet selector UI — both wallets always render
  together, fixed order (main then IKZE).
- No cross-wallet date-alignment logic — each wallet shows its own latest
  snapshot independently, even if the two wallets' latest dates differ (see
  Critical Implementation Details).
- No resize listener — matches PUL-45's existing pattern; the view re-fetches
  and re-measures only when reopened.
- No d3/charting library changes, no tooltip/hover detail — same constraints
  as PUL-45.
- No schema/migration changes — `total_value` already exists on
  `portfolio_snapshots`.

## Implementation Approach

Backend-to-frontend, three phases:

1. **Backend**: replace the "most recently uploaded across all wallets" query
   with a per-wallet query, extend the pure delta function with a
   share-of-portfolio computation, and reshape the endpoint to return both
   wallets' data in one response.
2. **Frontend structure**: replace the single header/container with two
   headed containers in a responsive flex layout.
3. **Frontend data wiring**: fetch/render both wallets independently
   (including independent empty states), add the new detail line with a
   larger truncation threshold, and bring all test layers up to date.

## Critical Implementation Details

**Endpoint response shape is a breaking change, intentionally.** The response
changes from `list[TreemapPosition]` to `dict[str, list[TreemapPosition]]`
keyed by wallet (`{"main": [...], "ikze": [...]}`), fixed key order `main`
then `ikze`. This is an internal admin-only endpoint with `static/index.html`
as its only consumer, both updated in this same change — there is no
backward-compatibility concern to preserve.

**Each wallet is fetched and rendered independently; missing or stale data
never blocks the other wallet.** If `ikze` has no snapshot at all,
`get_latest_snapshot_for_wallet("ikze")` returns `None` and that wallet's
list is `[]` — `main` still renders normally. If `main`'s latest snapshot is
from today and `ikze`'s is from three days ago, both still render at their
own latest date with no cross-wallet check; this matches how
`/portfolio-xpost` actually runs (wallets are uploaded independently). A
`ValidationError` while building either wallet's `TreemapPosition` list still
500s the whole endpoint, matching today's behaviour — this is a defensive-only
path that should not happen given the skill's write path.

**`portfolio_share_pct` follows the same null-on-bad-input convention as the
daily deltas.** `compute_treemap_positions` gains a required `total_value:
float` parameter; `portfolio_share_pct = value / total_value * 100`, or
`None` if `total_value == 0` (guards division by zero — there is no
"genuinely zero portfolio" case to distinguish from this, so `None` is
sufficient, no `no-data`-style dual state needed here).

**Cell truncation threshold must grow, not just gain a line.** Today's
`width >= 60 && height >= 30` threshold governs showing one detail line line. With a
second detail line added, raise the threshold to `width >= 90 && height >=
46` so both lines have room — both lines appear together or neither does;
there is no partial state where only one of the two detail lines shows.

**Responsive stacking breakpoint is `768px`, not the existing `640px`.** The
codebase's existing mobile breakpoint (`static/index.html:149,202`) hides
table columns on phone-width screens — a different concern. Two side-by-side
treemaps need materially more width than a single table to stay legible, so
this plan introduces a separate `768px` breakpoint that stacks the two wallet
sections vertically (full width each) rather than reusing `640px`.

## Phase 1: Backend — per-wallet query, share computation, endpoint reshape

### Overview

Replace the "latest across all wallets" query with a per-wallet query, add
the share-of-portfolio computation to the pure delta function, and reshape
the endpoint to return both wallets keyed in one response.

### Changes Required:

#### 1. `db/bigquery.py` — per-wallet latest snapshot query

**Intent**: Replace `get_latest_snapshot()` ("most recently uploaded across
all wallets") with `get_latest_snapshot_for_wallet(wallet)`, since the
endpoint now needs each of `main` and `ikze`'s own latest snapshot, not
whichever wallet was uploaded most recently overall. The old function becomes
dead code once the endpoint no longer auto-detects — remove it along with its
tests rather than leaving it unused.

**Contract**: `get_latest_snapshot_for_wallet(wallet: str) -> dict | None`,
same return shape as `get_latest_snapshot_before`. Query: `SELECT ... FROM
portfolio_snapshots WHERE wallet = @wallet ORDER BY snapshot_date DESC,
created_at DESC LIMIT 1`, bound via a `wallet` `ScalarQueryParameter`
(mirrors `get_latest_snapshot_before`'s parameter binding). Returns `None` if
that wallet has no rows.

#### 2. `src/portfolio_treemap.py` — portfolio-share computation

**Intent**: Add each position's % share of the wallet's total portfolio value
to the existing per-position delta computation, sourced from the snapshot's
`total_value` rather than re-deriving a total from the positions list (so the
percentage reflects cash too, matching the ticket's "total portfolio value"
wording).

**Contract**: `compute_treemap_positions(today_positions_json: str,
yesterday_positions_json: str | None, total_value: float) -> list[dict]` —
add required third parameter. Each output dict gains
`"portfolio_share_pct": float | None`, computed as `value / total_value *
100`, or `None` if `total_value == 0`. Malformed-JSON path still returns `[]`
unchanged.

#### 3. `src/api.py` — endpoint reshape

**Intent**: Loop over both wallets, fetch each one's own latest+prior
snapshot pair, and return a dict keyed by wallet instead of a flat list.

**Contract**: `TreemapPosition` gains `portfolio_share_pct: float | None =
None`. Add a module-level `_TREEMAP_WALLETS = ("main", "ikze")`. Endpoint body:
for each wallet in `_TREEMAP_WALLETS`, call `get_latest_snapshot_for_wallet(wallet)`;
if `None`, that wallet's entry is `[]`; else call `get_latest_snapshot_before(wallet,
latest["snapshot_date"])` for the prior row and
`compute_treemap_positions(latest["positions_json"], prior["positions_json"]
if prior else None, latest["total_value"])`, mapped through
`TreemapPosition(**p).model_dump()`. Return `{wallet: [...] for wallet in
_TREEMAP_WALLETS}`. `BigQueryError`/`ValidationError` handling unchanged (500
for either, regardless of which wallet triggered it).

### Success Criteria:

#### Automated Verification:

- Updated pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q`
- Updated BQ-layer tests pass: `uv run pytest tests/test_bigquery.py -q -k get_latest_snapshot`
- Updated endpoint tests pass: `uv run pytest tests/test_api.py -q -k treemap`
- Full test suite still passes: `uv run pytest --tb=short`

#### Manual Verification:

- `curl -H "X-API-Key: <admin-key>" localhost:8000/admin/portfolio/treemap` returns
  `{"main": [...], "ikze": [...]}` with plausible positions, deltas, and share
  percentages against real data.
- Same request with a `user`-role key returns 403.

---

## Phase 2: Frontend — dual containers, headers, responsive structure

### Overview

Replace the single header/container with two headed containers — "Portfel
główny" over a `main` container, "IKZE" over an `ikze` container — laid out
side by side with a responsive breakpoint that stacks them vertically on
narrow viewports.

### Changes Required:

#### 1. `static/index.html` — view markup

**Intent**: Replace the single `#treemap-container` with two labelled
sections inside `treemap-view`, keeping the existing menu-item/view-toggle
wiring (`treemap-btn`, `showTreemapView()`) unchanged.

**Contract**: Inside `treemap-view`'s `innerHTML`
(`static/index.html:746-750`), replace the single `<div
id="treemap-container">` with a flex wrapper `<div class="treemap-wallets">`
containing two `<div class="treemap-wallet">` blocks, each with an `<h3>`
header ("Portfel główny" / "IKZE") and its own container (`id="treemap-main"`,
`id="treemap-ikze"`).

#### 2. `static/index.html` — CSS

**Intent**: Lay the two wallet sections out side by side on wide viewports,
stacked on narrow ones, replacing the single `#treemap-container` rule.

**Contract**: `.treemap-wallets { display: flex; gap: 1rem; }`,
`.treemap-wallet { flex: 1 1 50%; min-width: 0; }`, each wallet's container
keeps the existing `#treemap-container` rule's properties (`position:
relative; min-height: 400px; background: #fff; border-radius: 8px;`)
generalized to a `.treemap-container` class applied to both `#treemap-main`
and `#treemap-ikze`. Add `@media (max-width: 768px) { .treemap-wallets {
flex-direction: column; } }` per the confirmed stacking decision.

### Success Criteria:

#### Automated Verification:

- Full test suite still passes: `uv run pytest --tb=short`
- Layout unit tests still pass: `node --test tests/test_treemap_layout.js`

#### Manual Verification:

- Opening the treemap view shows both "Portfel główny" and "IKZE" headers
  with two empty containers side by side (rendering/fetch wiring lands in
  Phase 3, so containers may be empty at this point — verify structure and
  responsive stacking only).
- Shrinking the browser window below ~768px stacks the two sections
  vertically; above it, they sit side by side.

---

## Phase 3: Frontend — fetch/render wiring, cell content, full test coverage

### Overview

Wire `fetchTreemap()`/`renderTreemap()` to the new keyed response, render
each wallet into its own container with its own empty state, add the new
share%/value detail line with the larger truncation threshold, and bring
`test_bigquery.py`, `test_portfolio_treemap.py`, `test_api.py`,
`tests/e2e/conftest.py`, and `tests/e2e/test_portfolio_treemap.py` up to date
for the two-wallet behaviour.

### Changes Required:

#### 1. `static/index.html` — fetch

**Intent**: Fetch the keyed response and render (or empty-state) each wallet
independently — one wallet having no data must not block the other from
rendering.

**Contract**: `fetchTreemap()` keeps its existing auth/error handling
(`X-API-Key`, 401 → `doLogout()`, non-2xx → inline error). On success, for
each `[wallet, containerId]` in `[["main", "treemap-main"], ["ikze",
"treemap-ikze"]]`: if `data[wallet].length === 0`, set that container's
`innerHTML` to "Brak danych portfela"; else call `renderTreemap(data[wallet],
$(containerId))`.

#### 2. `static/index.html` — render

**Intent**: Parameterize `renderTreemap` to target a specific container (it's
now called twice), and add the new share%/value detail line.

**Contract**: `renderTreemap(data, container)` — same sort-by-value-descending
and `computeTreemapLayout(sorted, container.clientWidth,
container.clientHeight)` as today, but operating on the passed-in `container`
instead of the module-level `$('treemap-container')`. Cell template gains a
second detail line, shown together with the existing daily-change line only
when `cell.width >= 90 && cell.height >= 46` (raised from `60`/`30` per the
confirmed cell-layout decision): `<span class="tc-detail">{share_pct.toFixed(1)}%
· {value.toFixed(0)} PLN</span>` — or "brak danych" only for the existing
daily-change line if `daily_change_pln === null`; the share%/value line shows
regardless of daily-change null-ness (it has its own, independent `None`
guard from `total_value == 0`, formatted as "—" when `null`).

#### 3. `static/index.html` — CSS for the extra line

**Intent**: No new modifier classes needed (colour-by-sign logic is unchanged
and still keyed off `daily_change_pln`) — just confirm `.tc-detail` accommodates
a second stacked line.

**Contract**: `.treemap-cell .tc-detail` already uses `display: block`; no
rule change required, only verify visually in Phase 3's manual step that two
stacked `.tc-detail` lines don't overflow the larger minimum cell size.

#### 4. `tests/test_bigquery.py` — replace `get_latest_snapshot` tests

**Intent**: Cover the new per-wallet query in place of the removed
all-wallets query.

**Contract**: Replace `test_get_latest_snapshot_returns_most_recent_row_across_wallets`
and `test_get_latest_snapshot_returns_none_when_table_empty` with
`test_get_latest_snapshot_for_wallet_returns_most_recent_row` (asserts the
query string binds `wallet` and orders by `snapshot_date DESC, created_at
DESC`) and `test_get_latest_snapshot_for_wallet_returns_none_when_no_rows`.
Update the `from db.bigquery import (...)` import list accordingly.

#### 5. `tests/test_portfolio_treemap.py` — `total_value`/share-pct cases

**Intent**: Cover the new required parameter and its derived field.

**Contract**: Update every existing call site to pass a `total_value`
argument. Add cases: share % computed correctly for a normal positive
`total_value`; `total_value == 0` → `portfolio_share_pct is None` for every
position (existing delta fields unaffected by this guard).

#### 6. `tests/test_api.py` — keyed-response treemap tests

**Intent**: Cover the new `{main: [...], ikze: [...]}` shape and independent
per-wallet behaviour.

**Contract**: Replace `_LATEST_SNAPSHOT`/`_PRIOR_SNAPSHOT` fixtures and the
five existing treemap tests (`tests/test_api.py:275-340`) with: admin request
returns both wallets' data keyed correctly (mock
`get_latest_snapshot_for_wallet` with a `side_effect` keyed by wallet
argument); a wallet with `get_latest_snapshot_for_wallet` returning `None`
yields `[]` for that wallet while the other wallet's data still renders;
user role still 403; missing key still 401; `BigQueryError` from either
wallet's call still 500; malformed position data still 500. Expected JSON
bodies must include `portfolio_share_pct`.

#### 7. `tests/e2e/conftest.py` — second wallet fixture

**Intent**: Give the E2E suite real `ikze` data so the live-server test can
assert both wallets render with real network responses, not mocks.

**Contract**: Add `_FAKE_TREEMAP_IKZE_LATEST`/`_FAKE_TREEMAP_IKZE_PRIOR`
(mirroring the existing `_FAKE_TREEMAP_LATEST`/`_FAKE_TREEMAP_PRIOR` shape,
`wallet: "ikze"`, distinct ticker/values/`total_value` so share % differs
visibly from `main`'s). Patch `get_latest_snapshot_for_wallet` with a
`side_effect` keyed by the `wallet` argument (returning the `main` or `ikze`
fixture), replacing the existing `get_latest_snapshot` patch.

#### 8. `tests/e2e/test_portfolio_treemap.py` — both-wallet assertions

**Intent**: Assert the real, full two-wallet experience end to end.

**Contract**: Update
`test_admin_can_open_treemap_and_see_positions_rendered_with_pl_deltas` to
also assert both `"Portfel główny"` and `"IKZE"` headers are visible, and
that `#treemap-ikze`'s cells render its own fixture's ticker/share%/value
text (independent assertions from `#treemap-main`'s, both within the same
test — they exercise the same "open the view once" user action). Existing
role-gating tests (`test_user_role_has_no_treemap_menu_item_or_dom_node`,
`test_user_role_never_triggers_treemap_network_request`) need no behavioural
change, only confirm they still pass against the new markup.

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest --tb=short`
- Layout unit tests still pass: `node --test tests/test_treemap_layout.js`
- E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual Verification:

- Log in as admin, open "Treemapa portfela": both "Portfel główny" and "IKZE"
  render with rectangles proportional to value within each wallet, daily-change
  colouring as before, and — on cells at least 90×46 — both the daily %/PLN
  line and the new share%/value line visible.
- Point at a wallet with no snapshot (or temporarily mock one to `None`):
  confirm its container shows "Brak danych portfela" while the other wallet
  still renders normally.
- Shrink the window below ~768px: confirm the two wallet sections stack
  vertically; reopen the view after resizing (no resize listener) and confirm
  the new layout is picked up.
- Confirm small rectangles in either wallet still fall back to ticker-only
  text below the raised threshold.
- Log in as a non-admin user: confirm the menu item is absent and a direct
  `curl` to the endpoint with a user-role key returns 403.

---

## Testing Strategy

### Unit Tests:

- `tests/test_portfolio_treemap.py`: existing delta cases re-run with the new
  `total_value` parameter threaded through; new cases for
  `portfolio_share_pct` (normal value, `total_value == 0` → `None`).
- `tests/test_treemap_layout.js`: unchanged — the layout function itself has
  no wallet awareness and needs no new cases.

### Integration Tests:

- `tests/test_bigquery.py`: `get_latest_snapshot_for_wallet()` — found row for
  the requested wallet only, `None` when that wallet has no rows, correct
  `ORDER BY` tie-break.
- `tests/test_api.py`: `GET /admin/portfolio/treemap` — 200 with both wallets
  keyed correctly for admin, one-wallet-empty-other-populated case, 403/401
  unchanged, 500 on `BigQueryError`/`ValidationError` from either wallet.

### Manual Testing Steps:

1. With real production data for both `main` and `ikze` (or whichever
   wallets currently have snapshots), open the treemap as admin and visually
   confirm both headers, proportionality, colouring, and the new
   share%/value line against known position values.
2. Confirm the per-wallet empty-state message appears for whichever wallet
   currently has no snapshot, without affecting the other wallet's render.
3. Resize the window across the 768px breakpoint to confirm stacking, and
   below the raised cell threshold to confirm ticker-only fallback still
   works per wallet.

## Performance Considerations

Still at most a few dozen positions per wallet; the endpoint now does two BQ
row reads per wallet (four total, both wallets) instead of two — still
trivial at this scale, no caching needed.

## Migration Notes

None — no schema or data migration; `total_value` already exists on
`portfolio_snapshots`.

## References

- Parent change (shipped, archived): `context/archive/2026-06-20-admin-ui-portfolio-treemap/`
- `total_value` invariant: `.claude/skills/portfolio-xpost/SKILL.md:175`
- Existing endpoint/model conventions: `src/api.py:105-110,231-247`
- Existing menu/view pattern: `static/index.html:660-753`
- Existing fetch/render pattern: `static/index.html:906-954`

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Backend — per-wallet query, share computation, endpoint reshape

#### Automated

- [x] 1.1 Updated pure-function tests pass: `uv run pytest tests/test_portfolio_treemap.py -q` — 39971fe
- [x] 1.2 Updated BQ-layer tests pass: `uv run pytest tests/test_bigquery.py -q -k get_latest_snapshot` — 39971fe
- [x] 1.3 Updated endpoint tests pass: `uv run pytest tests/test_api.py -q -k treemap` — 39971fe
- [x] 1.4 Full test suite still passes: `uv run pytest --tb=short` — 39971fe

#### Manual

- [x] 1.5 curl with admin key returns `{"main": [...], "ikze": [...]}` with plausible data — 39971fe
- [x] 1.6 curl with user key returns 403 — 39971fe

### Phase 2: Frontend — dual containers, headers, responsive structure

#### Automated

- [x] 2.1 Full test suite still passes: `uv run pytest --tb=short` — 33b6a2f
- [x] 2.2 Layout unit tests still pass: `node --test tests/test_treemap_layout.js` — 33b6a2f

#### Manual

- [x] 2.3 Both "Portfel główny" and "IKZE" headers render with two containers side by side — 33b6a2f
- [x] 2.4 Window below ~768px stacks the two sections vertically — 33b6a2f

### Phase 3: Frontend — fetch/render wiring, cell content, full test coverage

#### Automated

- [x] 3.1 Full test suite passes: `uv run pytest --tb=short`
- [x] 3.2 Layout unit tests still pass: `node --test tests/test_treemap_layout.js`
- [x] 3.3 E2E suite passes: `uv run pytest tests/e2e/test_portfolio_treemap.py -q`

#### Manual

- [x] 3.4 Both wallets render proportionally with correct colouring and the new share%/value line on large-enough cells
- [x] 3.5 A wallet with no snapshot shows its own empty state without affecting the other wallet
- [x] 3.6 Stacking/reopen behaviour confirmed across the 768px breakpoint
- [x] 3.7 Ticker-only fallback still works per wallet below the raised threshold
- [x] 3.8 Non-admin: menu item absent, endpoint returns 403
