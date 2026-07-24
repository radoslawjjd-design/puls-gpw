---
date: 2026-07-24T17:41:43+02:00
researcher: Claude (Fable 5)
git_commit: 4a0bde8bfb86b6730c6d47308c48fe146cef95b0
branch: pul-94-announcement-seen-badges
repository: puls-gpw
topic: "Per-item \"new\" badge clearing for announcements: current mechanism, lifecycle hook points, identifiers, history, test coverage (PUL-94)"
tags: [research, codebase, spa, index-html, badges, localStorage, announcements, watchlist]
status: complete
last_updated: 2026-07-24
last_updated_by: Claude (Fable 5)
---

# Research: Per-item "new" badge clearing (PUL-94)

**Date**: 2026-07-24T17:41:43+02:00
**Researcher**: Claude (Fable 5)
**Git Commit**: 4a0bde8
**Branch**: pul-94-announcement-seen-badges
**Repository**: puls-gpw

## Research Question

How does the current "new since last visit" (`NOWE`) badge mechanism work in `static/index.html`, and where are the lifecycle hook points (popup open, navigate-away, logout, page close) and data prerequisites (per-item identifiers, per-user scoping) needed to implement per-item badge clearing per PUL-94 / GH #185?

## Summary

- **Current mechanism is a single coarse per-view timestamp.** `_seenThreshold(key)` (`static/index.html:2137-2144`) reads `localStorage['faro_seen_<key>']` into an in-memory cache and *immediately* overwrites storage with `Date.now()` — read-and-advance in one shot, lazily, on the first `renderTable()` of a view per page load. Keys: `faro_seen_announcements`, `faro_seen_my_wallet`. First-ever visit (no key) → `null` → no badges by design.
- **One shared renderer for both views.** `renderTable(data, r, containerId)` (`static/index.html:4344`) serves both Ogłoszenia (`#table-body`) and Obserwowane (`#my-wallet-table-body`); badge computed at line 4376: `row.published_at > seenTs` → `<span class="new-badge">NOWE</span>` appended to the date cell (admin branch 4385, user branch 4396). CSS `:913`, dark-mode `:965`. No other reader of `faro_seen`/`_seenThreshold` exists anywhere in the repo.
- **Critical gap: no per-item identifier reaches the frontend for most rows.** `announcement_id` = SHA256 of the announcement URL (`db/bigquery.py:112-114`). Admin `/announcements` rows include it (`src/api.py:181-183`, `db/bigquery.py:1640`) but the frontend only uses it for the delete button (`data-id`, `index.html:4392`). User `/announcements` rows (`AnnouncementUser`, `src/api.py:267-273`; query `db/bigquery.py:1796-1804`) and **all** `/announcements/my-wallet` rows (`db/bigquery.py:1853-1856`) do **not** carry `announcement_id`. A per-item seen-set needs either (a) adding `announcement_id` to those queries/models, or (b) a synthetic client-side key (e.g. `ticker + published_at`).
- **Popup open**: shared `openModal(d)` (`index.html:4430`, announcement branch 4453-4483) fired from `tr.addEventListener('click', () => openModal(tr.dataset))` (`4411-4413`) — same path for both views. The id is **not** in `tr.dataset` today; `modalAttrs` (4377-4381) would need a `data-id`.
- **Navigate-away**: central router `_navigateToView(view)` (`index.html:2543-2559`) — hook "leaving `currentView`" at the top, before `currentView` (declared `1293`) is reassigned. Must be mirrored in `_applyUrlState()` (`2608-2642`, used by init + `popstate` at `1764-1781`) which enters views bypassing `_navigateToView`.
- **Logout**: single funnel `doLogout()` (`index.html:1352-1387`) covers manual, idle-timeout (`1330`) and all ~25 401/403-driven logouts. Uses `fetch(..., {keepalive:true})` (`1358`) — the established pattern if a network flush were ever needed.
- **Page close**: **no** `pagehide`/`visibilitychange`/`beforeunload` listeners exist anywhere in the file — a close hook is net-new.
- **Per-user scoping**: all `faro_*` localStorage keys are global per browser profile; `doLogout` does not touch them. `user_id`/`email` are returned by `GET /api/auth/me` (`src/auth.py:607-620`) but discarded — `_bootProbeSession()` (`index.html:1494-1521`) keeps only `role`. API-key sessions have no user identity at all.
- **Zero test coverage** of the badge mechanism, and no e2e opens the announcement detail popup (only the X-posts variant of the shared modal is tested). Conftest dates make badges never render in e2e today.

## Detailed Findings

### 1. Current badge mechanism (`static/index.html`)

```js
// index.html:2134-2144
// „nowe od ostatniej wizyty" — per-view timestamp w localStorage; pierwsza
// wizyta w ogóle (brak klucza) nie pokazuje badge'y.
const _seenThresholds = {};
function _seenThreshold(key) {
  if (!(key in _seenThresholds)) {
    const stored = localStorage.getItem('faro_seen_' + key);
    _seenThresholds[key] = stored ? Number(stored) : null;
    localStorage.setItem('faro_seen_' + key, String(Date.now()));
  }
  return _seenThresholds[key];
}
```

- Threshold advances at **first render of the view in a page load**, not on leave/logout/close — hence the PUL-94 complaint (badges reflect the previous visit, clear all-at-once next visit).
- In-memory cache keeps badges stable across re-renders (sorting, pagination, refetch) within one page load. Badges appear on every page and under any filter; sorting re-renders from `_annData` (`renderHeaders` → `renderTable` at `2179-2188`).
- The two view keys advance independently, each at its own first render.
- Badge render (`4346`, `4376`): compared field is `row.published_at` (ISO string from API), `new Date(row.published_at).getTime() > seenTs`.
- Exhaustive grep: `faro_seen` only at 2139/2141; `_seenThreshold` only at 2137 (def) and 4346 (sole call site). No nav counters/tab badges elsewhere.

### 2. Data flow and identifiers

| View | Fetch fn | Endpoint | Rows carry `announcement_id`? |
|---|---|---|---|
| Ogłoszenia (admin) | `fetchAnnouncements` (`index.html:2703-2728`) | `GET /announcements` | YES (`db/bigquery.py:1640`, `src/api.py:181-183`) |
| Ogłoszenia (user) | same | same | **NO** (`AnnouncementUser` `src/api.py:267-273`; query `db/bigquery.py:1796-1804` doesn't SELECT it) |
| Obserwowane (both roles) | `fetchMyWalletAnnouncements` (`index.html:2791-2818`) | `GET /announcements/my-wallet` | **NO** (`list_announcements_for_watchlist` selects only company, ticker, event_type, structured_analysis, published_at, analysis_score — `db/bigquery.py:1853-1856`) |

- `announcement_id` = SHA256 hex of URL — `db/bigquery.py:112-114` (`announcement_id_for_url`).
- Pagination state: `currentPage` (announcements, `index.html:1290`, buttons `1805-1806`) vs `wlPage` (my-wallet, fixed page_size 20, `_myWalletParams` `2784-2789`).
- Obserwowane fetch is lazy (`_watchlistFetched` gate, `2416-2423`) and refires after watchlist add/remove (`3013-3015`, `3034-3036`).
- Frontend rows land in module-globals `_annData` (`2717`) and `_wlData` (`2809`) — a navigate-away hook can read "items currently shown" from these.

### 3. Lifecycle hook points

**Popup open (per-item clear):**
- Row click → `openModal(tr.dataset)` — `index.html:4411-4413` (shared by both tables; the `openModal` at `3931-3933` is the unrelated admin X-posts table).
- `openModal(d)` — `4430`, announcement branch `4453-4483`; `closeModal()` `4485-4488`.
- **Prerequisite**: add `data-id` (announcement id or synthetic key) to `modalAttrs` (`4377-4381`) so it's readable as `d.id` inside `openModal` — today only the admin delete button carries `data-id` (`4392`).

**View switching (navigate-away clear):**
- Central router `_navigateToView(view)` — `2543-2559`; `currentView` global `1293` (reset in `doLogout` `1362`). Hook point: top of `_navigateToView`, before reassignment.
- Second entry path bypassing the router: `_applyUrlState()` — `2608-2642` (init + `popstate` `1764-1781` → call at `1780`) — must be covered too.
- All `show*View` functions: `showAnnouncementsView` `2304`, `_showXHistoryViewDom` `2318`, `showXHistoryView` `2334`, `_showMyWalletViewDom` `2399`, `showMyWalletView` `2416`, `_showSettingsViewDom` `2515`, `showSettingsView` `2532`, `showPortfolioPositionsView` `3849`. No existing "leaving view" teardown besides `stopPortfolioTreemapResize()`/`closeProfileMenu()`.
- Lesson (lessons.md "SPA single-file"): every new view must be hidden by ALL sibling show* functions — analogous discipline applies to any leave-hook: cover every entry path.

**Logout / session end:**
- `doLogout()` — `1352-1387`: `stopIdleTracking()`, `closeModal()`, keepalive POST `/api/auth/logout` (`1358`), removes `hasSession`, `sessionStorage.clear()`, resets watchlist state, `history.replaceState`, `showLogin()`. Does **not** touch `faro_*` keys.
- Single funnel: manual button (`1702-1705`), idle timeout (`1330`), ~25 401/403 handlers all call `doLogout()`.

**Page close / hide:**
- No `pagehide`/`visibilitychange`/`beforeunload`/`unload` listeners exist (verified by grep). Net-new listener required for the "viewed but didn't click, then closed tab" case. Since seen-state lives in localStorage (synchronous writes), a network keepalive is NOT needed — a synchronous `localStorage.setItem` in `pagehide`/`visibilitychange:hidden` suffices.

### 4. localStorage conventions and per-user scoping

- Convention: persistent prefs use `faro_` prefix (`faro_theme` `1718/1721/2093`, `faro_seen_*` `2139-2141`); flags without prefix: `hasSession` (`1359/1490/1597`), `gdpr_consent_v1` (`1417-1420`), legacy `watchlist_client_id` removed at boot (`1484`). Auth runtime state in sessionStorage (`apiKey` `1288/1532/1602`, `role` `1289/1505/1533/1598`).
- **No JSON-serialized localStorage values exist today**; a per-item seen-set (JSON array/object under e.g. `faro_seen_ids`) would be the first — no expiry/TTL precedent either (a cap/prune policy should be considered so the set doesn't grow unbounded).
- **All keys are global, not per-user**; two users sharing a browser share thresholds; logout doesn't reset them. Identity is available but discarded: `/api/auth/me` returns `{user_id, email, role}` (`src/auth.py:607-620`) but `_bootProbeSession` (`1494-1521`) keeps only `role`; `_enterUserSession` (`1595`) sets `hasSession`/`role` only. API-key sessions (`1524-1537`) have no user identity (only role from `/auth/role`).

### 5. Test coverage

- **No tests at all** for `NOWE`/`new-badge`/`faro_seen` (grep across `tests/` — only unrelated `#role-badge`, scraper fixtures).
- **No e2e opens the announcement detail popup**; the shared modal is exercised only via X-posts history (`tests/e2e/test_x_post_history.py:97,113`).
- Views themselves are well covered: pagination (`tests/e2e/test_pagination.py`), autocomplete, refresh, URL routing (`tests/e2e/test_url_routing.py` — wiele asercji na `#announcements-view`), my-wallet (`tests/e2e/test_my_wallet.py`), watchlist guard (`test_watchlist_guard.py:22` — 3× nawigacja = 1 fetch), sentiment bar (`test_watchlist_sentiment.py`). Logout covered in `test_landing_auth.py:97`, `test_idle_timeout.py:57,65`, `test_url_routing.py:120,194,217`, `test_profile_menu.py:36`.
- Conftest (`tests/e2e/conftest.py:519-652`) patches `src.api.*`/`src.auth.*` import sites: `list_announcements_admin → _FAKE_ADMIN_ROWS` (20 rows, **hardcoded old** `published_at = 2026-01-01`, conftest:87-98), `list_announcements_user → []`, `list_announcements_for_watchlist → _FAKE_WATCHLIST_ANNOUNCEMENT` (**fresh** `now-1d`, conftest:221-229, chosen for the sentiment 7-day window).
- Badge determinism in e2e: first visit → no `faro_seen_*` key → threshold `null` → badges never render today. New e2e will need to pre-seed `localStorage['faro_seen_*']` (e.g. `add_init_script`) and/or fresh `published_at` rows to make badges appear at all.

## Code References

- `static/index.html:2134-2144` — `_seenThreshold` + `faro_seen_<key>` (core mechanism)
- `static/index.html:4344-4346` — `renderTable`, threshold lookup per containerId
- `static/index.html:4376` — badge computation (`row.published_at > seenTs`)
- `static/index.html:4385, 4396` — badge in date cell (admin/user branches)
- `static/index.html:4377-4381` — `modalAttrs` (no id today); `4392` — admin delete `data-id`
- `static/index.html:4411-4413` — row click → `openModal(tr.dataset)`
- `static/index.html:4430-4488` — `openModal` (announcement branch 4453) / `closeModal`
- `static/index.html:2543-2559` — `_navigateToView` (central router; hook point)
- `static/index.html:2608-2642` — `_applyUrlState` (bypass path; must mirror hook)
- `static/index.html:1764-1781` — `popstate` listener
- `static/index.html:1352-1387` — `doLogout` (single session-end funnel; keepalive pattern at 1358)
- `static/index.html:1293` — `currentView`; `2717`/`2809` — `_annData`/`_wlData`
- `static/index.html:913, 965` — `.new-badge` CSS (light/dark)
- `db/bigquery.py:112-114` — `announcement_id_for_url` (SHA256 of URL)
- `db/bigquery.py:1640` — admin query selects `announcement_id`; `1796-1804` — user query doesn't; `1853-1856` — my-wallet query doesn't
- `src/api.py:181-183` — `AnnouncementAdmin.announcement_id`; `267-273` — `AnnouncementUser` (no id)
- `src/auth.py:607-620` — `/api/auth/me` returns `user_id`/`email`/`role`
- `tests/e2e/conftest.py:87-98, 221-229, 519-652` — fake rows + mock wiring

## Architecture Insights

- Single-file vanilla-JS SPA; per-view state in module globals; one shared table renderer and one shared modal for both announcement views — a per-item change lands in exactly one render path and one click path.
- Established patterns to mirror: `faro_` localStorage prefix; read-once-into-in-memory-cache (`_seenThresholds`); keepalive fetch on logout; central router + `_applyUrlState` dual entry (any view-lifecycle hook must cover both).
- Design decision axis for planning (flagged in PUL-94): **per-item seen-set** (needs row identifiers — API additions or synthetic key `ticker+published_at`) vs **threshold advanced on leave** (no API change; per-view granularity but satisfies all three acceptance criteria if advanced at navigate-away/logout/pagehide instead of first render; per-item "clear on open" still needs an item-level overlay).
  - Hybrid worth considering: keep the coarse threshold but move its advancement from first-render to leave-events, plus a small per-item "seen ids" overlay for popup-open clearing.
- Unbounded-growth concern for any per-item set: no TTL precedent in the app; prune (e.g. by `published_at` older than the threshold, or cap N entries).
- localStorage writes are synchronous — `pagehide`/`visibilitychange:hidden` suffice for the close case; no keepalive/network needed since state is client-only.

## Historical Context (from prior changes)

- Badge introduced in commit `1c92625` — `feat(ui): promote faro-v2 design as the main UI (#135)`, 2026-07-17 — bundled in the faro-v2 redesign; **no dedicated research/plan artifact exists** for its design. Only rationale: inline comment at `index.html:2134-2135` (first visit shows no badges — deliberate).
- `git log -S "faro_seen"` shows only `1c92625` plus pass-through refactors (`bc7034a` PUL-72, `a97e82b` PUL-84).
- `context/changes/announcement-seen-badges/change.md` — the PUL-94 problem statement and acceptance criteria (this change).
- Relevant lessons (`context/foundation/lessons.md`): "SPA single-file: new view must be hidden by ALL sibling show*View functions" (cover every entry path when adding lifecycle hooks); "SPA pagination out-of-order fetch guard" (per-slot request-sequence pattern already used elsewhere).

## Related Research

- None on this topic — first research artifact touching the badge mechanism.

## Open Questions

1. **Per-item key choice**: extend API to expose `announcement_id` on user + my-wallet rows (touches `db/bigquery.py` queries, `src/api.py` models — backend surface) vs synthetic client key `ticker|published_at` (frontend-only; collision risk effectively nil for same ticker+timestamp, but survives URL changes poorly — id is SHA256(url) anyway, so synthetic key is arguably as stable). Decide in planning.
2. **Per-user scoping**: PUL-94 doesn't demand it; current keys are global. Should the seen-state stay browser-global (simplest, consistent with `faro_theme`) or become user-scoped via `user_id` from `/api/auth/me` (needs plumbing; API-key sessions have no identity)? Recommend: keep global, note as non-goal.
3. **"Shown" semantics for navigate-away**: only rows actually rendered (`_annData`/`_wlData`, current page) or "everything up to now" (advance the coarse threshold)? Advancing the threshold on leave is simpler and matches AC wording ("the announcements shown there stop being new").
4. **e2e determinism**: badge tests need pre-seeded `faro_seen_*` + fresh `published_at` fixtures; my-wallet fake row is already fresh (`now-1d`), announcements fakes are old (2026-01-01) — plan should account for fixture additions without breaking existing tests (strict-mode lesson from PUL-90).
