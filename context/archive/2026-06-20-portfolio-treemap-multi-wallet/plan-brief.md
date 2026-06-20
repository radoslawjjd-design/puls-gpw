# Portfolio treemap — main + IKZE side-by-side — Plan Brief

> Full plan: `context/changes/portfolio-treemap-multi-wallet/plan.md`

## What & Why

PUL-50: extend the existing single-wallet auto-detect portfolio treemap (PUL-45,
shipped) into a fixed two-wallet view — `main` and `ikze` rendered side by side,
each headed and labelled, with every cell additionally showing the position's
% share of that wallet's total portfolio value and its absolute PLN value. PUL-45
explicitly deferred multi-wallet display; this is that follow-up, requested by
the user during PUL-45's manual verification.

## Starting Point

The treemap endpoint, pure delta-computation function, squarified layout module,
and menu/view wiring all already exist and work for one auto-detected wallet.
`portfolio_snapshots.total_value` already exists and equals positions + cash —
exactly the denominator the new share % needs, with no schema change. The
position's absolute PLN value is already fetched by the frontend today but
never displayed.

## Desired End State

Admin clicks "Treemapa portfela" and sees "Portfel główny" and "IKZE" headers
side by side, each with its own proportionally-laid-out, colour-coded treemap.
Cells large enough show ticker + daily %/PLN change + the position's share %
of that wallet + its absolute PLN value. A wallet with no data shows its own
empty message without blocking the other. Narrow windows stack the two
treemaps vertically instead of squeezing them.

## Key Decisions Made

| Decision | Choice | Why (1 sentence) | Source |
|---|---|---|---|
| Endpoint response shape | `{main: [...], ikze: [...]}`, one request | One round trip, both wallets reflect the same fetch moment | Plan |
| Missing wallet data | Show header + empty message for that wallet only | Matches PUL-45's existing empty-state precedent, always shows both wallets per the ticket | Plan |
| Mismatched snapshot dates | Each wallet shows its own latest, independently | Matches how `/portfolio-xpost` actually runs (wallets update independently) | Plan |
| Cell content layout | Two detail lines, larger truncation threshold (90×46, up from 60×30) | Reuses the existing visual pattern, daily-change stays the primary signal | Plan |
| Share % basis | `total_value` (positions + cash), not sum-of-positions | Matches the ticket's literal wording and the skill's documented invariant | Plan |
| Responsive behaviour | Stack vertically below 768px (new breakpoint, distinct from existing 640px) | Keeps each treemap legible; existing 640px breakpoint serves a different (table-column) concern | Plan |
| Test depth | Update existing unit/integration/E2E tests + conftest fixtures in place | Old tests assert a response shape that won't exist anymore — rewriting is required either way | Plan |
| Scope cut if time-tight | Responsive breakpoint first, then nothing else (all 4 pieces are must-have) | Lowest user impact to defer; everything else is the ticket's explicit ask | Plan |

## Scope

**In scope:**
- `main` + `ikze` always rendered together, fixed order, no wallet selector
- Per-position `portfolio_share_pct` (new) + display of already-fetched `position_value_pln`
- Two headed containers, responsive stacking at 768px
- Endpoint reshape to `{wallet: [...]}`, removing the now-dead "most recent across all wallets" query

**Out of scope:**
- `short`/`long` wallets
- `?wallet=` override or wallet selector UI
- Cross-wallet date-alignment / staleness indicators
- Resize listener (re-fetch-on-reopen pattern, unchanged from PUL-45)
- New charting library, tooltips, schema/migration changes

## Architecture / Approach

Backend: replace `get_latest_snapshot()` (all-wallets auto-detect) with
`get_latest_snapshot_for_wallet(wallet)`, called once per wallet in
`("main", "ikze")`; extend `compute_treemap_positions` with a `total_value`
parameter feeding `portfolio_share_pct`; endpoint loops and returns a keyed
dict. Frontend: two headed containers replace the single one; `fetchTreemap`/
`renderTreemap` become wallet-parameterized, calling the unchanged
`computeTreemapLayout` independently per container.

## Phases at a Glance

| Phase | What it delivers | Key risk |
|---|---|---|
| 1. Backend | Per-wallet query, share-% computation, keyed endpoint response | Existing tests hardcode the old flat-array shape and the old all-wallets query — both need rewriting, not just extending |
| 2. Frontend structure | Two headed containers, responsive flex layout + 768px breakpoint | New breakpoint choice not derivable from existing code — first responsive concern of this kind in the file |
| 3. Frontend wiring | Per-wallet fetch/render, new detail line, full test-layer update (unit/integration/E2E + conftest) | Truncation threshold tuning (90×46) for two stacked detail lines is a judgment call verified manually |

**Prerequisites:** None blocking — PUL-45 is merged and archived; this is a pure extension of existing, working code.
**Estimated effort:** ~1-2 sessions across 3 phases.

## Open Risks & Assumptions

- Assumes `ikze` is the exact lowercase wallet string used by `/portfolio-xpost`
  (confirmed against `SKILL.md`'s documented wallet enum `main`/`ikze`/`short`/`long`).
- The 90×46 truncation threshold and 768px breakpoint are plan-time judgment
  calls, not user-specified pixel values — manual verification in Phase 3 is
  the actual check, and either constant can be tuned without touching any
  other logic if it looks wrong in the browser.

## Success Criteria (Summary)

- Admin sees both "Portfel główny" and "IKZE" treemaps side by side (or
  stacked on narrow viewports), each correctly proportioned and coloured.
- Every cell large enough shows daily change AND share%/absolute value
  together — never just one of the two detail lines.
- A wallet with no data never blocks the other wallet from rendering.
