# Frame Brief: Dashboard refresh bug

> Framing step before /10x-plan. This document captures what is *actually*
> at issue, separated from what was initially assumed.

## Reported Observation

After refreshing the page (F5 or Ctrl+F5) in the dashboard — typically after
applying a filter or navigating to another page (`?page=N&page_size=M`) — the
announcements table goes completely empty: no header row, no data rows, no
error text, no "Brak wyników" message. Filtering or interacting with the date
field afterward has no effect (PUL-37 / GitHub #40).

## Initial Framing (preserved)

- **User's stated cause**: date-filter query-param building
  (`new Date($('f-from').value).toISOString())`, `static/index.html:347-348`)
  runs outside the `try/catch` in `fetchAnnouncements`; a stale/invalid value
  restored into `f-from`/`f-to` by the browser on reload throws
  `RangeError: Invalid time value`, aborting the function before the fetch.
- **User's proposed direction**: guard the param-building in `try/catch` (or
  validate date inputs before `toISOString()`); optionally read `page`/
  `page_size` from the URL on initial load so refresh keeps position.
- **Pre-dispatch narrowing** (user's own repro detail, given live in this
  session): the table vanishes on **any** refresh "regardless of which filter
  you set (ticker etc.)" — i.e. reproduces with a plain ticker filter, no date
  field ever touched. Also new: after it breaks, the date field "stops
  working" / can't be set at all, and the symptom is identical on Ctrl+F5
  (hard reload, bypasses cache).

## Dimension Map

1. **Script execution order (TDZ on top-level `const`)** — the init call
   `if (apiKey && role) showDashboard(role)` (`static/index.html:228`) runs
   synchronously, before the script reaches the `_ADMIN_COLS`/`_USER_COLS`
   `const` declarations (`:314`, `:324`) that `renderHeaders()` depends on. ←
   reframed root cause
2. **Date-filter RangeError outside try/catch** — initial framing; assumes
   the throw aborts the rest of the script.
3. **Pagination position lost on refresh** — initial framing's "secondary";
   `currentPage` always resets to 1, URL's `page`/`page_size` only read on
   `popstate`, never on initial load.
4. **Stale cached `index.html`** — would explain refresh-only breakage if the
   browser served an old/broken cached copy.
5. **Silent re-auth/401 falling back to login state** — would explain a fully
   "reset" looking dashboard if `sessionStorage` got cleared mid-flow.

## Hypothesis Investigation

| Hypothesis | Evidence | Verdict |
| --- | --- | --- |
| TDZ ordering bug (`_ADMIN_COLS`/`_USER_COLS` used before declared) | Live Playwright repro (this session): reloading an already-authenticated session threw `pageerror`: `Cannot access '_ADMIN_COLS' before initialization` (×2). `#table-head` and `#table-body` both empty post-reload. `#f-from` stayed `type="text"` after a click (focus listener never attached). Clicking "Filtruj" fired **zero** requests to `/announcements` (submit listener never attached). Reproduced using only a ticker filter — no date field touched. | **STRONG** |
| Date RangeError outside try/catch (initial framing) | `fetchAnnouncements` is declared `async function` (`static/index.html:339`) and called without `await`/`.catch` from `showDashboard` (`:310`). Per JS semantics, any throw inside an async function body — even before its own `try`/`await` — is automatically converted into a rejected promise, not a synchronous exception; it cannot propagate to the caller or halt the rest of the top-level script. Live repro reproduced the full failure (vanished header+rows, dead listeners) using a ticker-only filter, never touching `f-from`/`f-to`. | **NONE** as explanation of the reported symptom |
| Pagination position lost (initial framing's secondary) | `static/index.html:225` (`let currentPage = 1` re-inits every load), `:257-265` (URL `page`/`page_size` restored only on `popstate`), `:366` (`pushState` only). Confirmed accurate by code reading. | **STRONG**, but answers a different, smaller question — not the "stops loading" bug |
| Stale cached `index.html` | User: identical failure on Ctrl+F5, which bypasses cache. | **NONE** |
| Silent re-auth/401 | Live repro: `#dashboard-screen` computed `display: block`, `#login-screen` `display: none` after reload — UI stays on the dashboard, not the login screen. | **NONE** |

## Narrowing Signals

- User: failure happens "regardless of which filter you set (ticker etc.)" —
  rules out any hypothesis scoped to the date fields specifically.
- User: date field "stops working" / can't be set after the break — points
  to listener non-attachment, not a per-request failure.
- Live repro: exact browser-thrown error names the real defect
  (`Cannot access '_ADMIN_COLS' before initialization`) and fires only on
  the refresh-with-existing-session path, never on fresh login.
- Live repro: zero `/announcements` requests fired when clicking "Filtruj"
  post-refresh — confirms the throw aborted the rest of the top-level
  script (listener registration), not just one fetch call.

## Cross-System Convention

`git log -L 220,230:static/index.html` shows the synchronous bootstrap line
(`if (apiKey && role) showDashboard(role)`) has existed unchanged since the
dashboard's very first commit (`b9cbdfc`, "auth-public-url: frontend panel —
login, RBAC dashboard, filters"), predating the pagination feature
(`b4703ec`, PUL-23) entirely. This is a latent day-one bug, not a pagination
regression — it never fired during development because a fresh login calls
`showDashboard()` later, from a click handler, well after the whole script
(including the `const` declarations) has already run top-to-bottom. The
general JS convention this code violates: hoisted function declarations are
safe to call early, but top-level `const`/`let` bindings those functions
close over must be reached in execution order before any code path that
might invoke them runs.

## Reframed Problem Statement

> **The actual problem to plan around is**: the dashboard's "resume session"
> bootstrap runs `showDashboard()` synchronously before the script reaches
> the `const` declarations (`_ADMIN_COLS`/`_USER_COLS`) it transitively
> depends on, throwing a `ReferenceError` on every reload of an
> already-authenticated session and aborting all subsequent listener
> registration (filters, date-toggle, pagination buttons).

This has nothing to do with date parsing or pagination state. The proposed
fix (try/catch around date params) would not touch this code path at all and
would ship without fixing the reported bug. The actual fix is a script
load-order change: declare `_ADMIN_COLS`/`_USER_COLS` (and verify no other
`const`/`let` has the same problem) before the `if (apiKey && role)
showDashboard(role)` call, or — more robustly — move the whole bootstrap
call to run last, after every declaration in the script.

## Confidence

**HIGH** — live, reproducible evidence with the exact browser-thrown error
text and line-precise mechanism; alternative hypotheses were not just
unconfirmed but actively disproven (JS async-exception semantics, Ctrl+F5
ruling out caching, live DOM state ruling out silent logout); cross-checked
against git history showing this predates the feature the issue suspected.

## What Changes for /10x-plan

Scope the plan around reordering the script's declaration/bootstrap sequence
(or deferring the resume-session call to run last), not around try/catch-ing
the date-param builder. Treat the pagination-position-loss
(`page`/`page_size` not read from URL on initial load) as a separate,
smaller, optional UX fix — decide explicitly whether to bundle it into the
same plan or split it into its own change.

## References

- Source: `static/index.html:225-372` (full init/fetch/listener flow),
  `:314`, `:324` (`_ADMIN_COLS`/`_USER_COLS` declarations)
- Live repro performed in this session via a temporary Playwright script
  using the existing `tests/e2e/conftest.py` fixture pattern (script deleted
  after use, not committed)
- Git history: `git log -L 220,230:static/index.html` —
  `b9cbdfc` (dashboard first introduced), `b4703ec` (pagination, unrelated)
- Tracking: Linear PUL-37, GitHub #40
