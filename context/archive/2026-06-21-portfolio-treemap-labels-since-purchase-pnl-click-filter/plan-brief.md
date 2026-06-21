# Treemap labels, since-purchase P&L, hover + click-filter — Plan Brief

> Full plan: `context/changes/portfolio-treemap-labels-since-purchase-pnl-click-filter/plan.md`

## What & Why

PUL-51: follow-up to the shipped multi-wallet treemap (PUL-45/PUL-50),
requested by the user during manual verification. Adds explicit labels
(`D/D:`, `Total:`) to the two existing detail lines, a new third
since-purchase P&L line (`Zakup:` — % and PLN) per position, a bold hover
outline on cells, and click-to-filter navigation to the announcements table
pre-filtered by the clicked cell's ticker.

## Starting Point

The treemap already computes and renders daily change and wallet-share
detail lines per position, unlabeled. `pct` (cumulative return since
purchase) is already parsed and persisted in every position's
`positions_json` row but is currently dropped by
`compute_treemap_positions()`. The matching PLN amount (`profit_abs`) is
parsed by Gemini but never persisted — it must be derived from `pct` +
`value` using the exact inverse of an already-tested formula
(`_cumulative_pct` in `portfolio_thread_composer.py`). The announcements
view's ticker filter and view-switching machinery already exist and need no
changes to support click-to-filter.

## Desired End State

Every cell large enough shows three labeled lines — `D/D:` (today's
%/PLN), `Total:` (wallet share % + PLN value), `Zakup:` (since-purchase
%/PLN, or "brak danych"). Hovering any cell adds a bold white outline.
Clicking any cell jumps to the announcements table with that ticker filled
into the existing ticker filter, leaving any other active filters untouched.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Since-purchase line label | `Zakup:` | Short Polish word matching the existing two-line label rhythm | Plan |
| Line order | D/D, Total, Zakup (last) | Matches existing top-to-bottom precedence, purely additive | Plan |
| Truncation strategy | Single raised threshold (90×60), all 3 lines or none | No new branching logic — same binary behavior as today's 2-line gate | Plan |
| Missing/unusable since-purchase data | Whole line shows "brak danych" (covers missing `pct` and the `pct == -100` div-by-zero case) | Reuses the existing no-data convention; avoids a partial-data state | Plan |
| Click-to-filter scope | Overwrite only the ticker filter, keep other filters | Matches how the filter form's fields already behave independently | Plan |
| Cell accessibility | Click-only, no role/tabindex | Matches the only other clickable-row precedent in the codebase (`tr.clickable`) | Plan |
| Hover style | 3px white outline, drawn inward (`outline-offset: -3px`) | Visible against all three cell background colors without per-state recoloring | Plan |
| Field naming | `since_purchase_pct`/`since_purchase_pln` | Mirrors the existing `daily_change_pct`/`daily_change_pln` pair convention | Plan |

## Scope

**In scope:**
- `compute_treemap_positions()` derives `since_purchase_pct`/`since_purchase_pln` from the already-present `pct` field
- `TreemapPosition` gains the two new fields
- `D/D:`/`Total:`/`Zakup:` labels + new third detail line, raised truncation threshold
- Hover outline CSS
- Click handler → ticker filter + view switch, reusing existing announcements machinery

**Out of scope:**
- Any schema/ingestion change to `/portfolio-xpost` or `positions_json`
- Persisting `profit_abs` directly — stays derived, not stored
- Keyboard/role accessibility on cells
- A middle truncation tier (2-of-3 lines)
- Changes to `treemap-layout.js`'s squarified layout algorithm

## Architecture / Approach

Backend-to-frontend, three phases. Phase 1 extends the existing pure
delta-computation function with the new derived fields (no schema change,
no new query). Phase 2 is purely visual — labels + the new line + a raised
truncation threshold, with E2E fixtures gaining `pct` data. Phase 3 is purely
interactive — a CSS outline and one delegated click listener per wallet
container, reusing the announcements view's existing filter/fetch machinery
untouched.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend | `since_purchase_pct`/`since_purchase_pln` computed and exposed via the API | The `pct == -100` division-by-zero edge case, and updating existing exact-equality tests that will break once new keys appear |
| 2. Frontend — labels & line | `D/D:`/`Total:`/`Zakup:` labeled lines, raised 90×60 threshold, updated E2E fixtures | Visual overflow of a third stacked line at the new threshold — verified manually, same risk class as PUL-50's threshold tuning |
| 3. Frontend — hover & click | Bold hover outline + click-to-filter navigation | First clickable treemap cell in the codebase — delegation pattern avoids rebinding on resize but is new to this view |

**Prerequisites:** None blocking — PUL-45/PUL-50 are merged and archived; this is a pure extension of existing, working code.
**Estimated effort:** ~1 session across 3 phases.

## Open Risks & Assumptions

- Assumes the `90×60` threshold is the right size for three stacked detail
  lines without overflow — a plan-time judgment call verified manually in
  Phase 2, same tuning pattern as PUL-50's `90×46` threshold (adjustable
  without touching any other logic if it looks wrong in the browser).
- Assumes collapsing the `pct == -100` edge case to a full "brak danych" line
  (rather than showing a known % with an unknowable PLN) is acceptable —
  this is a deliberate simplification documented in the plan's Critical
  Implementation Details, not something the user was asked about directly.

## Success Criteria (Summary)

- Every cell large enough shows three correctly labeled, correctly signed
  detail lines, with "Zakup: brak danych" for positions with no usable
  since-purchase data.
- Hovering any cell shows a bold outline; clicking any cell lands on the
  announcements table filtered by that cell's ticker, with other active
  filters left untouched.
