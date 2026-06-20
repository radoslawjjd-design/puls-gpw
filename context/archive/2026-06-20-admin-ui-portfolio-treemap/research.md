---
date: 2026-06-20T00:00:00+02:00
researcher: Claude
git_commit: b531268afa0fa44728143821b62ca99cb49f166e
branch: radoslawjjd/pul-45-admin-ui-portfolio-treemap-position-proportional-rectangles
repository: radoslawjjd-design/puls-gpw
topic: "Admin UI portfolio treemap with daily P&L colouring (PUL-45)"
tags: [research, codebase, admin-ui, portfolio, treemap, bigquery, fastapi]
status: complete
last_updated: 2026-06-20
last_updated_by: Claude
---

# Research: Admin UI portfolio treemap with daily P&L colouring (PUL-45)

**Date**: 2026-06-20
**Researcher**: Claude
**Git Commit**: b531268afa0fa44728143821b62ca99cb49f166e
**Branch**: radoslawjjd/pul-45-admin-ui-portfolio-treemap-position-proportional-rectangles
**Repository**: radoslawjjd-design/puls-gpw

## Research Question

PUL-45 asks for a treemap visualization of the admin's portfolio (rectangles proportional to
position value, coloured green/red/gray by daily change, accessible from the profile menu,
refreshing after each XTB screenshot upload). The ticket states it depends on PUL-43 (admin UI
XTB screenshot upload), which is still in Backlog with no code. The user suspected the existing
`/portfolio-xpost` skill might not persist all portfolio positions to BigQuery (since the
generated X post itself only shows a few "leader" positions), and proposed adding a new
`wallet_treemap` BigQuery table to fix this. This research verifies that suspicion against the
live system and investigates what's actually needed to build the treemap.

## Summary

The user's suspicion was checked against live production data and is **not correct**: the
`portfolio_snapshots` BigQuery table already stores **all** extracted positions per wallet per
day, not just the leaders shown in the X post — confirmed by querying the live `main` wallet
row from 2026-06-19, which has 10 stored positions while the X post text shows at most 3
leaders. The leader-filtering (`_select_leaders`, `_LEADER_MAX_COUNT=3`,
`_LEADER_MIN_PROFIT_ABS=500.0`) only affects the composed tweet text in
`src/portfolio_thread_composer.py`; it never touches what gets persisted.

**The real gap** is that `portfolio_snapshots` only stores a **wallet-level** daily delta
(`day_change_abs`, `day_change_pct`) — there is no per-position daily change anywhere in the
schema or in any derived data. That per-position delta is what PUL-45's red/green/gray colouring
requires, and it doesn't exist yet.

**Decision (confirmed with user): Option B — read-time computation, no schema changes.** The
new `GET /admin/portfolio/treemap` endpoint will fetch the two most recent `portfolio_snapshots`
rows for the relevant wallet, match positions by ticker between them, and compute
`daily_change_pln`/`daily_change_pct` per ticker on the fly. **No new BigQuery table, no schema
migration, and no changes to the existing (already working, agent-interpreted) `/portfolio-xpost`
skill.** This was chosen over extending the skill to pre-compute and persist per-position deltas
at write time (Option A), because the skill's logic lives in `SKILL.md` prose interpreted by an
LLM agent each run rather than in tested code — pushing new logic there is riskier than adding it
as plain, unit-testable Python in the API layer, and the skill already works in production.

A secondary, good-news finding: since `portfolio_snapshots` is already populated by manual
`/portfolio-xpost` runs, **the treemap does not actually need to wait for PUL-43** — it can read
today's real data immediately. PUL-45's stated dependency on PUL-43 is soft, not blocking.

## Detailed Findings

### Data layer — `portfolio_snapshots` already has full position data

- Schema: `db/bigquery.py:191-201` — `snapshot_id`, `wallet`, `snapshot_date`, `total_value`,
  `currency`, `day_change_abs`, `day_change_pct`, `positions_json` (STRING, JSON-encoded list of
  `{ticker, value, pct}`), `created_at`. No per-position change field exists.
- `save_portfolio_snapshot()` (`db/bigquery.py:226-270`) and `get_latest_snapshot_before(wallet,
  before_date)` (`db/bigquery.py:273-310`, strict `<` on date, `ORDER BY snapshot_date DESC LIMIT 1`)
  are the only read/write functions; both operate on **one wallet at a time**. There is no
  existing function to find "the most recently uploaded wallet across all wallets" — a new small
  query (`SELECT wallet, snapshot_date FROM portfolio_snapshots ORDER BY snapshot_date DESC,
  created_at DESC LIMIT 1`) will be needed for PUL-45's "whichever wallet was last uploaded" v1
  scope.
- **Live verification** (queried via `get_latest_snapshot_before('main', date(2026,6,20))`):
  the 2026-06-19 `main` wallet row has **10 positions** in `positions_json` (Digital Network,
  Elektrotim, Kruk, Passus, Synektik, Toya, Votum, WIG20TR, XTB, sWIG80TR) — full position list,
  not the 3-leader subset.
- Leader filtering only happens in `src/portfolio_thread_composer.py:19-20,83-86`
  (`_LEADER_MIN_PROFIT_ABS = 500.0`, `_LEADER_MAX_COUNT = 3`, `_select_leaders()`), and only
  affects the text composed for the X post (`_compose_leaders_tweet`, lines 89-106) — it never
  mutates `WalletThreadData.positions`, which stays the full list end to end.
- Design intent is confirmed in the archived plan:
  `context/archive/2026-06-17-portfolio-xpost-skill/plan.md:62` explicitly documents
  `positions_json` as "JSON-encoded list of `{ticker, value, pct}`" with no mention of
  leader-only filtering at persistence time.
- Note: `PortfolioPosition.profit_abs` (`src/gemini_client.py:104-108`) is **not** persisted into
  `positions_json` (only `ticker`, `value`, `pct` are kept at save time per
  `SKILL.md`'s Step 5 template) — irrelevant to PUL-45 (which needs *daily* change, not
  cumulative profit), but worth knowing if a future feature wants cumulative P&L per position too.

### Backend — endpoint and auth conventions (`src/api.py`)

- Admin auth: `Role = Literal["admin","user"]` (`src/api.py:42`), `_get_role()` validates
  `X-API-Key` against `ADMIN_API_KEY`/`USER_API_KEY` env vars (`src/api.py:45-50`),
  `_require_admin()` enforces `role == "admin"` or raises 403 (`src/api.py:53-56`).
- Existing admin endpoints follow an `/admin/...` path prefix and take
  `role: Role = Depends(_require_admin)` as a parameter, e.g. `GET /admin/x-posts`
  (`src/api.py:197-217`, returns `[XPostAdmin(**r).model_dump() for r in rows]`) and
  `DELETE /announcements/{announcement_id}` (`src/api.py:219-227`, 204 response).
- Response models use `model_config = ConfigDict(extra="ignore")` and `| None` typed fields
  (`AnnouncementAdmin` at `src/api.py:68-87`, `XPostAdmin` at `src/api.py:90-99`) — the new
  endpoint should add a `TreemapPosition` model in the same style (`ticker`, `value_pln`,
  `daily_change_pln`, `daily_change_pct`).
- Test conventions in `tests/test_api.py:124-137` mock the DB-layer function via
  `patch("src.api.<function>", return_value=...)` and assert `200`/`403` for admin/non-admin
  `X-API-Key` headers; `tests/test_bigquery.py:316-356` mock `_get_client()` for the BQ layer
  itself. The new endpoint and its delta-computation helper should follow the same two-layer
  test split: one set of tests for the pure ticker-matching/delta-math function (no mocks needed,
  it's pure), one set for the endpoint wiring (mocked DB calls, 403/200 assertions).

### Frontend — admin UI menu/view wiring (`static/index.html`)

- The most recent precedent for "add a feature reachable from the profile menu" is the
  "Historia postów X" feature, in `injectAdminOnlyChrome()` (`static/index.html:645-721`):
  - Menu `<li>` is created and inserted **before** the logout `<li>` via
    `profileMenu.insertBefore(menuItem, logoutLi)` (`static/index.html:658-661`).
  - The view itself is a **separate full section** (`#x-history-view`), not the generic modal —
    created once and inserted via `insertAdjacentElement('afterend', ...)` after
    `#announcements-view` (`static/index.html:663-700`), mirroring the announcements view's
    filters → table → pagination structure.
  - Toggle is plain `style.display` swapping between views, closing the profile menu and
    triggering a fetch (`showXHistoryView()`, `static/index.html:730-735`).
  - Data fetch (`fetchXPosts()`, `static/index.html:779-815`) sends `X-API-Key` header, handles
    401 by logging out, and shows an inline error row on failure.
- A separate **generic modal** pattern also exists (`#modal-overlay`/`#modal-box`,
  `static/index.html:302-312`) and is used elsewhere (announcement detail popups) — available as
  an alternative container if the treemap is better suited to a dialog than a full view, but the
  most recent precedent (x-history) used a dedicated view, not the modal.
- **No existing "refresh after a backend action" pattern exists yet** (checked all refresh paths:
  filter submit, pagination, page-size change, `popstate`, "home" click — all are manual
  button/form-triggered fetches, `static/index.html:555-593`). PUL-45 (and PUL-43, when built)
  will need to invent this — e.g. the upload-completion handler calling the treemap's fetch
  function directly, or a small custom-event dispatch the treemap view listens for. Since PUL-43
  doesn't exist yet, the treemap's own "fetch on view-open" (mirroring `showXHistoryView`) is
  sufficient for v1; the "auto-refresh after upload" wiring becomes PUL-43's responsibility to
  call into once that endpoint exists.
- Menu-item/view naming convention from `context/archive/2026-06-19-profile-menu-dropdown/plan.md`:
  `[feature]-btn` for the menu item, `[feature]-view` for the content section — e.g.
  `treemap-btn` / `treemap-view`.
- CSS conventions: profile menu styles at `static/index.html:74-87`; views reuse `.filters`,
  `.table-wrap`-style containers (`static/index.html:89-231`) and a single global
  `@media (max-width: 640px)` breakpoint — no separate mobile stylesheet.

### Frontend — rendering the treemap itself (no bundler)

- The repo's served frontend has **no root `package.json`** and no build step — only nested tool
  directories (e.g. `tools/ai-code-reviewer`) have their own `node_modules`. Any visualization
  code must be plain `<script>`-loadable or self-contained.
- Recommendation: **hand-roll the squarified treemap algorithm** (~40-60 lines, Bruls/Huizing/van
  Wijk 1999 — sort by value descending, normalize to area, greedily lay out rows/columns
  minimizing worst aspect ratio) rather than pulling in `d3-hierarchy`/`d3-treemap` via CDN. For
  ≤20 positions, one-off, no other chart in the app, a small vendored pure function
  (`items + container size → rectangles`) is less total code than correctly wiring d3's hierarchy
  API, has no CDN/version dependency, and is trivially unit-testable in isolation.
- "Too small to fit" text fallback: a cheap width/height threshold per rectangle (e.g. skip the
  daily-%/PLN lines below ~60px width / ~30px height, show ticker only) is sufficient at this
  scale — no canvas `measureText` precision needed.
- Colour-by-sign: use a CSS class per rectangle (`.treemap-cell.positive/.negative/.neutral`)
  rather than inline styles, consistent with the rest of the file's class-based styling. Worth a
  one-line accessibility note: pure red/green alone is a known colour-blind failure mode — the
  spec's requirement to also show the numeric daily % and PLN change as text already mitigates
  this (colour reinforces, isn't the sole signal).

## Code References

- `db/bigquery.py:189-310` — `portfolio_snapshots` schema, `save_portfolio_snapshot`,
  `get_latest_snapshot_before`
- `src/gemini_client.py:103-178` — `PortfolioPosition`/`PortfolioExtraction`, `extract_portfolio_snapshot`
- `src/portfolio_thread_composer.py:19-20,83-106` — leader selection/filtering (post text only)
- `.claude/skills/portfolio-xpost/SKILL.md` (Step 1.3, Step 5.1) — extraction and persistence flow
- `src/api.py:41-56` — `Role`, `_get_role`, `_require_admin`
- `src/api.py:68-99,197-227` — `AnnouncementAdmin`/`XPostAdmin` models, existing admin endpoints
- `tests/test_api.py:124-137` — admin endpoint test conventions (403/200)
- `tests/test_bigquery.py:316-356` — BQ layer mock test conventions
- `static/index.html:645-735` — `injectAdminOnlyChrome`, x-history menu item/view/toggle pattern
- `static/index.html:779-815` — `fetchXPosts`, admin data-fetch convention
- `static/index.html:555-593` — existing manual refresh triggers (no auto-refresh precedent)
- `static/index.html:74-87,302-312` — profile-menu CSS, generic modal pattern

## Architecture Insights

- **Write-side vs. read-side separation already exists and should be preserved.** The
  `/portfolio-xpost` skill is the only writer of `portfolio_snapshots`; it is prose interpreted by
  an LLM agent, not deterministic code, and is already validated in production (live run
  2026-06-18, per project memory). New read-only consumers (like the treemap) should compute
  derived values (per-position deltas) entirely on the read side, in testable Python, rather than
  asking the skill to grow new responsibilities.
- **Two view-container patterns coexist** in `static/index.html`: a dedicated full-section view
  (`x-history-view`, most recent precedent) and a generic modal overlay. The treemap should follow
  the more recent dedicated-view precedent unless there's a specific reason to prefer a modal.
- **No auto-refresh mechanism exists anywhere in the frontend yet.** PUL-45's "refreshes after
  each upload" requirement has no prior art to copy — and since PUL-43 (the upload feature) isn't
  built, this wiring is naturally deferred to PUL-43's implementation; PUL-45's v1 only needs
  fetch-on-view-open.
- **No new BigQuery table or schema migration is needed for PUL-45.** The per-position daily
  delta is a pure computation over two already-existing rows, not new persisted state.

## Historical Context (from prior changes)

- `context/archive/2026-06-17-portfolio-xpost-skill/plan.md` — original design and contract for
  `portfolio_snapshots` (schema, `save_portfolio_snapshot`, `get_latest_snapshot_before`),
  confirming `positions_json` was always intended to hold the full position list.
- `context/archive/2026-06-19-profile-menu-dropdown/plan.md` — establishes the profile-menu
  dropdown itself and the `[feature]-btn`/`[feature]-view` naming convention, focus management,
  and outside-click/Escape handling that any new menu item (including the treemap) must follow.
- `context/archive/2026-06-19-admin-ui-x-post-history/` — most recent applied instance of that
  convention (`x-history-btn`/`x-history-view`), used directly as the template for this feature's
  frontend wiring.
- PUL-43 (`admin-ui-xtb-screenshot-upload-portfolio-post-generation-review`) — still `Backlog` in
  Linear, no corresponding code or `context/changes/` folder exists yet. PUL-45 lists it as a hard
  dependency in the ticket text, but the data it would eventually write
  (`portfolio_snapshots` rows) is already being written today by the manual `/portfolio-xpost`
  skill — so PUL-45 is buildable now against real data, independent of PUL-43's timeline.

## Related Research

- None yet under `context/changes/**/research.md` specific to this topic prior to this document.

## Open Questions

- **Wallet selection for v1**: when multiple wallets have snapshots, "whichever wallet was last
  uploaded" (per ticket) needs the new "most recent row across all wallets" query — should ties
  (same `snapshot_date`, different `created_at`) break on `created_at DESC`, and should the
  endpoint accept an optional `?wallet=` override for testing/debugging, or strictly auto-detect?
  Not yet decided — defer to `/10x-plan`.
- **First-run-for-a-wallet edge case**: if there is no prior-day snapshot for the detected wallet
  (`get_latest_snapshot_before` returns `None`), every position's daily change is undefined —
  should those render as gray/neutral (no data) rather than defaulting to 0, to avoid implying "no
  change" when it's actually "no comparison available"? Recommend gray/neutral with a visual
  affordance (covered in plan, not research).
- **New tickers appearing only in today's snapshot** (no match in yesterday's `positions_json`):
  same "no prior data" treatment as above — gray/neutral, not a 100%/undefined spike.
- Exact frontend container choice (dedicated view vs. modal) is a plan-time decision, not fully
  settled here — recommendation given, not mandated.
