# Admin UI portfolio treemap with daily P&L colouring — Plan Brief

> Full plan: `context/changes/admin-ui-portfolio-treemap/plan.md`
> Research: `context/changes/admin-ui-portfolio-treemap/research.md`

## What & Why

PUL-45: add a treemap visualisation of the admin's portfolio — rectangles
proportional to position value, coloured green/red/gray by daily P&L — reachable
from the profile menu. Gives the admin an at-a-glance read of which positions moved
and how much, without scanning a table.

## Starting Point

`portfolio_snapshots` (BigQuery) already stores every position per wallet per day,
written by the existing `/portfolio-xpost` skill — confirmed against live data
(today's `main` wallet snapshot has 10 positions). What's missing is per-position
*daily* delta — only a wallet-level delta exists today. The profile menu shell
(PUL-47) is already merged; PUL-43 (screenshot upload, the ticket's stated hard
dependency) is still Backlog but turns out not to block this — the same data is
already flowing in manually.

## Desired End State

Admin clicks "Treemapa portfela" in the profile menu, sees a treemap of the
latest-uploaded wallet: area ∝ position value, colour = daily direction, ticker +
daily % + daily PLN inside each rectangle (ticker-only if too small to fit text).
Works today against real data.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Schema change | None — read-time computation | Skill's write path is LLM-interpreted prose, not tested code; pushing new logic into testable Python on the read side is lower-risk | Research |
| Frontend container | Dedicated view (like x-history) | Most recent precedent (PUL-44), gives the treemap room to render legibly | Plan |
| Wallet selection | Auto-detect only, no `?wallet=` override | Matches ticket's explicit v1 scope; multi-wallet toggle is out of scope | Plan |
| No-prior-data rendering | Distinct gray "no data" state, not 0% | Avoids implying "unchanged" when there's nothing to compare against (first run, new ticker) | Plan |
| Empty state | Menu item always visible; empty message inside view | Matches existing pattern (x-history shows inline error, doesn't hide the menu item) | Plan |
| Test depth | Backend unit+endpoint tests + frontend layout-function unit test | Math-heavy logic (delta computation, squarified layout) is deterministic and worth isolating; visual correctness stays manual | Plan |
| Truncation UX | Static ticker-only, no tooltip | Ticket's acceptance criteria only requires the ticker-only fallback; no hover/tap interaction requested | Plan |
| Treemap algorithm | Hand-rolled squarified layout, no d3 | No bundler in repo; a ~50-line pure function is less total code than wiring d3's hierarchy API for a one-off chart | Research |

## Scope

**In scope:**
- `GET /admin/portfolio/treemap` endpoint (admin-only)
- Per-position daily delta computed from two existing snapshot rows
- Profile-menu entry + dedicated view
- Squarified treemap rendering with colour-by-sign and small-rectangle truncation

**Out of scope:**
- New BigQuery table or schema migration
- Multi-wallet toggle / `?wallet=` override
- Historical/time-series treemap
- Auto-refresh wiring tied to PUL-43 (deferred to PUL-43's own implementation)
- d3 or any charting library; tooltips on truncated rectangles

## Architecture / Approach

Backend: one new BQ query (`get_latest_snapshot()`, most-recent row across all
wallets) + one pure delta-matching function (`compute_treemap_positions`) + one
thin FastAPI endpoint, following existing `/admin/x-posts` conventions exactly.

Frontend: the squarified-layout algorithm lives in its own file
(`static/js/treemap-layout.js`) so it's unit-testable with Node's built-in test
runner — this requires adding the repo's first `StaticFiles` mount (currently
`index.html` is served as one inline string with no separate static routes). UI
wiring mirrors the `x-history` menu-item/view/fetch pattern exactly.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend — treemap data & endpoint | Working `GET /admin/portfolio/treemap` with full delta logic and tests | Division-by-zero / malformed `positions_json` edge cases in the delta function |
| 2. Frontend — treemap layout module | `computeTreemapLayout` pure function + `StaticFiles` mount, Node-tested | New serving infra (the mount) — small but a first for this codebase |
| 3. Frontend — UI integration | Menu item, view, fetch, colour-coded rendering | Visual correctness (proportionality, truncation) is manual-only, not asserted in CI |

**Prerequisites:** None blocking — PUL-47 (menu shell) is merged; real snapshot
data already exists from manual `/portfolio-xpost` runs.
**Estimated effort:** ~1-2 sessions across 3 phases.

## Open Risks & Assumptions

- Assumes `positions_json.positions[].value` is already in PLN (no currency
  conversion exists anywhere in this codebase) — matches the ticket's
  `position_value_pln` field name, but would need revisiting if a non-PLN wallet
  is ever onboarded.
- Visual/layout correctness (rectangle proportionality, colour, truncation
  threshold) is verified manually only — no Playwright E2E in this plan per the
  confirmed test-depth decision.

## Success Criteria (Summary)

- Admin sees correctly proportioned, correctly coloured rectangles for the latest
  uploaded wallet, with ticker-only fallback on small rectangles.
- Non-admin gets 403 on the endpoint and never sees the menu item.
- Positions with no prior-day comparison render visually distinct from genuinely
  flat positions.
