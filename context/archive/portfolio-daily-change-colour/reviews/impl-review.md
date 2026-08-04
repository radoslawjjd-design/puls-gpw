<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Colour the daily-change column in the portfolio table

- **Plan**: `context/changes/portfolio-daily-change-colour/plan.md`
- **Scope**: Phase 1 of 1
- **Date**: 2026-08-04
- **Verdict**: APPROVED (after F1 fixed in review)
- **Findings**: 0 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS (automated); manual pending human check |

## Findings

### F1 — A move below the printed resolution got a colour contradicting its text

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: `static/index.html:3844` (as first implemented)
- **Detail**: The first implementation classified on the raw `daily_change_pct`
  but printed `toFixed(2)`. A value of `0.004` rendered as `+0.00%` tinted green,
  and `-0.004` as `-0.00%` tinted red — the cell asserted "flat" and "up" at the
  same time. This is the mirror image of the defect the ticket set out to prevent:
  the ticket forbade collapsing zero into unknown, and this collapsed a non-zero
  into zero *in text only*, leaving colour as the sole dissenting signal.
- **Fix**: Derive one `shown = Number(pct.toFixed(2))` and drive both the sign
  prefix and the class from it, so text and colour cannot disagree.
  - Strength: Two decimals is the resolution the cell publishes, so it is the
    honest basis for the colour; removes the contradiction class entirely rather
    than narrowing it.
  - Tradeoff: A sub-0.005% move now reads as flat. It already *printed* as flat —
    only the tint claimed otherwise — so nothing visible is lost.
  - Confidence: HIGH — `(-0).toFixed(2)` is `"0.00"` in JS, so the negative-zero
    path prints cleanly with no extra guard.
  - Blind spot: None significant. `zmiana_procentowa` from BigQuery is normally
    already 2dp, so the case is rare rather than impossible.
- **Decision**: FIXED — `static/index.html`, plus a fixture row (`OPL`, `0.004`)
  and `test_a_move_too_small_to_print_is_not_coloured_as_a_gain`.

### F2 — Zysk/strata carries the same rounding asymmetry

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: `static/index.html:3849-3853`
- **Detail**: The neighbouring `pnl` block classifies on raw `pnl_pln` while
  printing `toFixed(2)`, so it has the F1 defect too: a P&L of `0.004 PLN` prints
  `0.00 PLN` in green. It is pre-existing and outside this change's scope, and the
  amounts involved are sub-grosz, so the visible stakes are lower than for a
  percentage.
- **Fix**: Apply the same `shown` treatment to the pnl pair.
- **Decision**: SKIPPED — out of scope for PUL-123 part 1; recorded here so the
  asymmetry is a known choice rather than an oversight.

## Success Criteria

| Check | Result |
|-------|--------|
| New e2e tests fail before, pass after | PASS — verified RED then GREEN for all three |
| `uv run pytest --tb=short` | PASS — 1010 passed |
| `uv run ruff check` | PASS — all checks passed |
| Manual verification (1.4–1.6) | PENDING — human check on the local build |

## Notes

- The plan's central claim — that the mobile card layout needs no separate work
  because it restyles the same `<td>` — is confirmed by
  `test_daily_change_colour_survives_dark_mode_and_a_phone` passing at 360 px in
  both themes with no CSS added.
- Extending `_FAKE_PORTFOLIO_POSITIONS` from 2 to 5 rows broke nothing: the full
  suite went 1009 → 1010 passing with no other test touched.
- No security surface: `dailyClass` comes from a closed literal set, never from
  user input, and no new interpolation reaches the DOM unescaped.
