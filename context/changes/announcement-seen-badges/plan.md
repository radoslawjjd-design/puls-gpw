# Per-item "new" badge clearing (PUL-94) — Implementation Plan

## Overview

Rework the "NOWE since last visit" badge semantics in the single-file SPA (`static/index.html`): stop advancing the per-view threshold at first render; instead advance it on **leave events** (navigate-away, logout, pagehide/visibilitychange), and add a small **per-item seen-set** so opening an announcement's popup clears its badge immediately and persistently. Applies to both Ogłoszenia and Obserwowane. Frontend-only; no API changes; no new deps.

## Current State Analysis

- `_seenThreshold(key)` (`static/index.html:2137-2144`) reads `localStorage['faro_seen_<key>']` into in-memory cache `_seenThresholds` and **immediately overwrites storage with `Date.now()`** at the first `renderTable()` of a view per page load. Keys: `faro_seen_announcements`, `faro_seen_my_wallet`. First-ever visit (no key) → `null` → no badges (deliberate).
- Badge computed in the single shared renderer `renderTable(data, r, containerId)` at `static/index.html:4346` (threshold lookup) and `4376`: `seenTs && row.published_at > seenTs` → `<span class="new-badge">NOWE</span>` in the date cell (admin branch `4385`, user branch `4396`). CSS `:913` (light), `:965` (dark).
- Row click → `openModal(tr.dataset)` (`4411-4413`) — same path for both announcement tables; `openModal(d)` at `4430`, announcement branch `4453-4483`; the admin X-posts table also calls `openModal` (`3931-3933`) with `d.kind === 'xpost'`.
- `modalAttrs` (`4377-4381`) does **not** carry any row identifier; only the admin delete button has `data-id` (`4392`). Raw `row.published_at` is not in the dataset either (only the formatted date).
- Central router `_navigateToView(view)` (`2543-2559`) sets `currentView` (declared `1293`) then dispatches; `_applyUrlState()` (`2608-2642`) is a second entry path (init + `popstate` `1764-1781`) that **bypasses** the router.
- `doLogout()` (`1352-1387`) is the single session-end funnel (manual, idle `1330`, all 401/403 handlers); it does not touch `faro_*` keys.
- **No** `pagehide`/`visibilitychange`/`beforeunload` listeners exist anywhere in the file.
- Rows land in module globals `_annData` (`2717`) / `_wlData` (`2809`).
- E2E: zero badge coverage; no test opens the announcement popup; conftest fixtures make badges unrenderable today (`_FAKE_ADMIN_ROWS` hardcoded `published_at=2026-01-01` at `tests/e2e/conftest.py:87-98`; `_FAKE_WATCHLIST_ANNOUNCEMENT` fresh `now-1d` at `:221-229`; first visit → no `faro_seen_*` key → threshold null).
- Full context: `context/changes/announcement-seen-badges/research.md`.

## Desired End State

- Opening an announcement's detail popup removes that item's NOWE badge immediately and the item stays non-new across reloads (per-item seen-set in localStorage).
- Switching away from Ogłoszenia/Obserwowane (nav tabs, deep-link/popstate), logging out, closing the tab, or hiding the page advances that view's threshold, so those announcements are not "new" on the next render/visit.
- First-ever visit still shows no badges. Both views, both roles, light+dark, no console errors, no new deps.

Verification: e2e suite covers badge render, popup-clear + reload persistence, navigate-away clear, logout clear; manual headed-browser check for pagehide/visibilitychange.

### Key Discoveries:

- One shared renderer + one shared modal → the change lands in exactly one render path and one click path (`static/index.html:4344`, `4430`).
- No per-item id reaches the frontend for user rows and all my-wallet rows (`db/bigquery.py:1796-1804`, `1853-1856`) → synthetic client key `ticker|published_at` chosen (decision, see brief).
- `_applyUrlState` bypasses `_navigateToView` — any leave-hook must cover both (lesson: cover every SPA entry/exit path, `context/foundation/lessons.md`).
- localStorage writes are synchronous → `pagehide`/`visibilitychange:hidden` suffice; no keepalive/network needed.

## What We're NOT Doing

- No backend/API changes (no `announcement_id` exposure for user/my-wallet rows).
- No per-user scoping of seen-state — keys stay browser-global like `faro_theme` (two users sharing a browser share seen-state; accepted).
- No "only actually rendered rows" precision on navigate-away — leaving a view advances the whole view's threshold, including unvisited pagination pages (accepted).
- No forced re-render when returning to a still-open **browser tab** after `visibilitychange:hidden` — badges already in the DOM stay until the next natural re-render. (Re-entering a *view* inside the SPA does re-render from cache — Phase 1 change #7.)
- No nav-tab counters or other new badge surfaces; no changes to the X-posts table or its modal branch.
- No dedicated e2e for pagehide/visibilitychange (hard to drive reliably in Playwright) — manual verification.

## Implementation Approach

Hybrid model (decision): keep the coarse per-view threshold but move its advancement from first-render to leave events, plus a per-item seen-set overlay for popup-open clearing.

- Badge condition becomes: `seenTs && published_at > seenTs && !seenItems.has(key)` where `key = ticker + '|' + published_at` (raw ISO string from the API).
- `_seenThreshold(key)` becomes read-only; new `_markViewSeen(viewKey)` writes `Date.now()` to **both** localStorage and the in-memory cache (so a re-render in the same session sees the new value) and prunes the seen-set.
- The per-item set is shared across views — the same announcement appearing in both Ogłoszenia and Obserwowane clears in both when opened in either (desirable: it is the same announcement).
- Prune keeps the set bounded for free: an entry whose `published_at` ≤ the older of the two known thresholds can never badge again → drop it. Plus a hard cap (drop oldest by `published_at` beyond 500 entries) as a safety net.

## Critical Implementation Details

- **False-leave guard (rendered flag)**: `currentView` initializes to `'announcements'` (`1293`) and has 9 writers (incl. `.pp-ticker-link` at `3259-3268` bypassing the router, and the `doLogout` reset at `1362`). A naive "leaving currentView → mark seen" hook would spuriously mark a view that never rendered (fresh deep-link `?view=my-wallet`, every popstate, logout ordering). Therefore: `renderTable` sets `_viewRendered[viewKey] = true`; `_markViewSeen` is a no-op unless the flag is set, and clears it. Hooks may then fire liberally — marking is gated by "badges were actually on screen".
- **Shared modal guard**: `openModal` also serves the admin X-posts table (`d.kind === 'xpost'`); mark-seen must run only in the announcement branch (or be keyed off the presence of `data-seen-key`, which only announcement rows will carry).
- **No refetch on view re-entry**: `showAnnouncementsView` is display-toggling only (explicit comment at `2540-2542`: "x-history always fetches on show, announcements doesn't") and Obserwowane is gated by `_watchlistFetched` to one fetch per session — so navigate-away clearing is only visible if re-entry re-renders from cache (Phase 1 change #7). Re-render must NOT fetch — `tests/e2e/test_watchlist_guard.py` asserts 3× navigation = 1 fetch.
- **In-memory cache coherence**: `_markViewSeen` must update `_seenThresholds[key]` as well as localStorage — `renderTable` reads the cache, and the navigate-away e2e scenario (leave → return → refetch → re-render) only passes if the cache reflects the advance.
- **Dual entry paths**: the "leaving current view" hook must fire both in `_navigateToView` (before `currentView` is reassigned) and on the `_applyUrlState` path (popstate/deep-link switches views without the router). Extract one helper and call it from both.
- **visibilitychange fires on browser tab switch too** — accepted decision: switching tabs counts as "plausibly seen". Guard the listener so it only acts when a session is active and `currentView` maps to a seen-key (never on the landing/login screens).
- **E2E localStorage seeding**: `add_init_script` re-runs on every navigation — seed `faro_seen_*` with an *if-absent* guard (`if (!localStorage.getItem(k)) localStorage.setItem(k, v)`) so logout/leave advances survive test reloads instead of being clobbered back to the old value.

## Phase 1: Seen-state rework in `static/index.html`

### Overview

All mechanism changes: read-only threshold, leave-event advancement, per-item seen-set, badge condition, popup hook, page-close listeners.

### Changes Required:

#### 1. Threshold becomes read-only + `_markViewSeen`

**File**: `static/index.html` (around `2134-2144`)

**Intent**: `_seenThreshold(key)` stops writing `Date.now()` on first read — it only loads the stored value into `_seenThresholds`. New `_markViewSeen(viewKey)` performs the advance at leave time.

**Contract**: `_markViewSeen(viewKey)` for `viewKey ∈ {'announcements','my_wallet'}`: **no-op unless `_viewRendered[viewKey]` is set** (F2 — false-leave guard); otherwise sets `Date.now()` into `localStorage['faro_seen_'+viewKey]` **and** `_seenThresholds[viewKey]`, clears `_viewRendered[viewKey]`, then calls the seen-set prune. First-ever visit still yields `null` threshold (no badges) until the first leave event after a render seeds the key. Update the explanatory comment at `2134-2135` to describe the new lifecycle.

#### 2. Per-item seen-set

**File**: `static/index.html` (new helpers near the threshold block)

**Intent**: Persist the set of individually-opened announcements so their badges stay cleared across reloads.

**Contract**: localStorage key `faro_seen_items` holding a JSON object `{ "<ticker>|<published_at>": 1 }` (raw ISO `published_at` as delivered by the API). Lazy-loaded once into an in-memory `Set`; `_markItemSeen(key)` adds + writes through. Malformed/absent stored JSON → empty set (try/catch). Prune (called from `_markViewSeen`): drop entries whose `published_at` segment ≤ the older non-null of the two view thresholds; then hard-cap at 500 entries dropping oldest by `published_at`.

#### 3. Badge condition + row seen-key

**File**: `static/index.html` (`renderTable`, `4344-4396`; `modalAttrs` `4377-4381`)

**Intent**: Badge renders only for items that are both newer than the view threshold and not individually seen; each announcement row carries its synthetic key for the click path.

**Contract**: add `data-seen-key="${esc(row.ticker + '|' + row.published_at)}"` to `modalAttrs` — **only when both `row.ticker` and `row.published_at` are present** (both fields are `| None` in the API models; a row without `published_at` never badges anyway, F5); badge condition extends to `... && !_seenItemsHas(key)`. Both admin and user branches inherit it (shared date-cell `newBadge` variable — one change point). `renderTable` also sets `_viewRendered[viewKey] = true` for the view it just rendered (F2).

#### 4. Popup-open hook

**File**: `static/index.html` (row click `4411-4413` and/or `openModal` `4430-4483`)

**Intent**: Opening an announcement's popup marks it seen and removes its badge from the visible row immediately (no re-render).

**Contract**: on announcement rows only (guard: `d.seenKey` present / not the xpost branch): `_markItemSeen(d.seenKey)` + remove the `.new-badge` element from the clicked `<tr>` if present.

#### 5. Leave-view hook (router + URL-state path)

**File**: `static/index.html` (`_navigateToView` `2543-2559`, `_applyUrlState` `2608-2642`)

**Intent**: Leaving Ogłoszenia/Obserwowane for any other view advances that view's threshold.

**Contract**: helper `_leaveCurrentView(nextView)` — if `currentView` maps to a seen-key (`announcements` → `announcements`, my-wallet view → `my_wallet`) and `nextView !== currentView`, call `_markViewSeen(mappedKey)`. Called at the top of `_navigateToView` (before `currentView` reassignment) and on the `_applyUrlState` view-switch path. Spurious calls (deep-link boot, repeated popstate, `.pp-ticker-link` at `3259-3268` writing `currentView` directly) are harmless — `_markViewSeen` no-ops without the rendered flag (F2). Implementer verifies the exact `currentView` string values used by the router before mapping.

#### 6. Session-end + page-close hooks

**File**: `static/index.html` (`doLogout` `1352-1387`; net-new listeners near the popstate listener `1764`)

**Intent**: Logout, tab close, and page hide mark the currently active announcements view as seen — covering the "viewed but never clicked, then left" case.

**Contract**: in `doLogout` (before the `currentView` reset at `1362`): mark the active view if it maps to a seen-key. Net-new `window 'pagehide'` + `document 'visibilitychange'` (act on `document.visibilityState === 'hidden'`) listeners doing the same, guarded with `if (!role) return` — the global `role` is the codebase's session-active idiom (popstate precedent at `1764-1781`; `hasSession` is NOT equivalent — it's true on the landing screen during the boot probe) (F3); the rendered flag (F2) is the second line of defense. Synchronous localStorage writes only — no network.

#### 7. Re-render from cache on view re-entry

**File**: `static/index.html` (`showAnnouncementsView` `2304-2313` / router announcements branch; `showMyWalletView` `2416-2423`)

**Intent**: Navigate-away clearing must be visible when the user returns in the same session — but neither view re-fetches nor re-renders on re-entry today (F1), so the stale badges would linger in the DOM until the next fetch.

**Contract**: on re-entry, when cached data exists, re-render without fetching: announcements — `renderTable(_annData, role)` if `_annData` non-empty; Obserwowane — `renderTable(_wlData, role, 'my-wallet-table-body')` if `_watchlistFetched && _wlData` non-empty. **No new fetches** — `tests/e2e/test_watchlist_guard.py` asserts 3× navigation = 1 `/watchlist` fetch. Guard against rendering before the first fetch (empty cache → leave DOM untouched).

### Success Criteria:

#### Automated Verification:

- Full test suite passes: `uv run pytest`
- Wiring sanity: `_markViewSeen` referenced from `_navigateToView`/`_applyUrlState` path, `doLogout`, `pagehide` and `visibilitychange` listeners; `_seenThreshold` no longer writes to localStorage (grep)

#### Manual Verification:

- Headed browser (local uvicorn): badge renders for a fresh announcement with a pre-seeded old threshold; opening its popup clears it instantly and after reload
- Switching Ogłoszenia → Obserwowane → back clears announcements badges; logout → login clears; closing/hiding the tab (visibilitychange) advances `faro_seen_*` in devtools
- Both views, light + dark, no console errors

**Implementation Note**: After completing this phase and all automated verification passes, pause for manual confirmation before proceeding to Phase 2.

---

## Phase 2: E2E coverage (4 core scenarios)

### Overview

New Playwright spec + fixture groundwork so badges are renderable and deterministic in e2e.

### Changes Required:

#### 1. Fixture groundwork

**File**: `tests/e2e/conftest.py` (fake rows `87-98`, `221-229`) and/or per-test `add_init_script`

**Intent**: Make a badge renderable in e2e: at least one announcements row with fresh `published_at`, and a pre-seeded old `faro_seen_*` threshold.

**Contract**: (a) **bump one existing `_FAKE_ADMIN_ROWS` entry's `published_at` to fresh — do NOT append a row** (F4: `test_refresh` asserts `#table-body tr` count == 20, and the mock is `return_value=` ignoring paging, so exactly 20 rows is load-bearing for the "Strona 2" tests). Verified safe: nothing in the pipeline sorts, and no existing test asserts dates, row order, or first-row content. The my-wallet fake row is already fresh (`now-1d`). (b) helper that seeds `faro_seen_announcements`/`faro_seen_my_wallet` via `context.add_init_script` with the if-absent guard (see Critical Implementation Details). Ogłoszenia badge scenarios run as admin (`list_announcements_user → []` — user's announcements table is empty in e2e); Obserwowane scenario may use either role.

#### 2. Badge spec

**File**: `tests/e2e/test_new_badges.py` (new)

**Intent**: Lock in the four acceptance behaviors.

**Contract**: four independent tests (each with its own setup, per e2e rules — `getByRole`/`getByText` locators, no `waitForTimeout`):
1. Pre-seeded old threshold → fresh row shows `NOWE` (Ogłoszenia as admin; assert badge count/row).
2. Open that row's popup → badge disappears from the row without reload; reload (init-script re-seeds old threshold, if-absent guard keeps it) → badge still gone (per-item set persisted).
3. Badge visible → navigate to Obserwowane → back to Ogłoszenia (re-render from cache on re-entry, Phase 1 change #7) → badge gone.
4. Badge visible → logout → login again (same document, no reload) → badge gone.

### Success Criteria:

#### Automated Verification:

- New spec passes: `uv run pytest tests/e2e/test_new_badges.py`
- Full suite (unit + all e2e) passes: `uv run pytest` — no regressions from fixture changes

#### Manual Verification:

- Verify on prod after deploy: fresh announcement badges behave per acceptance criteria (open/navigate/logout)

---

## Testing Strategy

### Unit Tests:

- None — the change is vanilla-JS inside `static/index.html`; the project has no JS unit-test infra. Behavior is locked by e2e.

### Integration Tests:

- The four e2e scenarios above (Phase 2), on the mocked `live_server_url` app.

### Manual Testing Steps:

1. Local headed browser: pre-seed `faro_seen_announcements` with an old epoch in devtools, add a fresh announcement (or use my-wallet's fresh fixture), verify badge → popup-clear → reload persistence.
2. Hide the tab (switch browser tab), check devtools localStorage: `faro_seen_<active view>` advanced.
3. Dark mode: badge styling unchanged; console clean throughout.
4. Prod (post-deploy): same pass over gpw.okiem.ai with a real fresh announcement.

## Performance Considerations

Negligible: one extra `Set.has` per rendered row; seen-set bounded by prune + 500-entry cap; all writes synchronous localStorage (small payloads).

## Migration Notes

Existing `faro_seen_announcements`/`faro_seen_my_wallet` keys keep their meaning (epoch-ms threshold) — only the advancement moment changes. `faro_seen_items` is net-new; absent/corrupt values degrade to an empty set. No cleanup of old keys needed.

## References

- Related research: `context/changes/announcement-seen-badges/research.md`
- Ticket: Linear PUL-94 / GitHub #185
- Lessons applied: SPA sibling view functions / dual entry paths; conftest shared-state audit (`context/foundation/lessons.md`, memory PUL-90)

## Progress

> Convention: `- [ ]` pending, `- [x]` done. Append ` — <commit sha>` when a step lands. Do not rename step titles. See `references/progress-format.md`.

### Phase 1: Seen-state rework in `static/index.html`

#### Automated

- [x] 1.1 Full test suite passes: `uv run pytest` — 3b97ebc
- [x] 1.2 Wiring sanity: `_markViewSeen` hooked in router/url-state/doLogout/pagehide/visibilitychange; `_seenThreshold` read-only (grep) — 3b97ebc

#### Manual

- [x] 1.3 Headed browser: badge render → popup-clear (instant + after reload) — 3b97ebc
- [x] 1.4 Navigate-away / logout / tab-hide advance thresholds correctly — 3b97ebc
- [x] 1.5 Both views, light + dark, no console errors — 3b97ebc

### Phase 2: E2E coverage (4 core scenarios)

#### Automated

- [x] 2.1 New spec passes: `uv run pytest tests/e2e/test_new_badges.py`
- [x] 2.2 Full suite passes with fixture changes: `uv run pytest`

#### Manual

- [ ] 2.3 Prod verification after deploy (badge lifecycle on gpw.okiem.ai)
