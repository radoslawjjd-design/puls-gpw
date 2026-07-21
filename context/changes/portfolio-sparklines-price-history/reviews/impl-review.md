<!-- IMPL-REVIEW-REPORT -->
# Implementation Review (Full Plan): Portfolio sparklines — price_history

- **Plan**: context/changes/portfolio-sparklines-price-history/plan.md
- **Scope**: Phase 1 + 2 of 2 (full plan)
- **Date**: 2026-07-21
- **Verdict**: APPROVED
- **Findings**: 0 critical  0 warnings  1 observation (+ prior F1 resolved)

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS (F1 fixed in 8afcc56) |
| Success Criteria | PASS (650 passed) |

## Findings

### F4 — Sparkline located via CSS attribute selector (aria-hidden svg)

- **Severity**: 🔍 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/e2e/test_portfolio_wallets.py:83-89
- **Detail**: The e2e locates the sparkline via `td[data-label="30 dni"]` + `svg polyline`. CLAUDE.md prefers getByRole/getByText, but the sparkline is a decorative aria-hidden `<svg>` with no accessible role/text. The locators are consistent with this file's id/CSS convention (#pp-tbody, .pp-portfolio-tab) and the row is filtered by has_text ("PKO"/"CDR"). Correct pragmatic choice for a decorative element.
- **Decision**: NOTED — no action.

## Prior findings (phase-1 review, reviews/impl-review-phase-1.md)

- **F1** (WARNING, Pattern Consistency) — `_WL_` prefix on portfolio constants → RESOLVED in 8afcc56 (renamed to `_PRICE_HISTORY_*`).
- **F2** (OBSERVATION, Scope Discipline) — e2e fake signature pulled from Phase 2 into Phase 1 → NOTED, documented, no action.
- **F3** (OBSERVATION, Safety & Quality) — empty-history None-vs-[] not verified end-to-end → NOTED, functionally identical (both render "—").

## Commits reviewed

- c95728e — feat Phase 1 (price_history[] on positions API, test-first)
- 8afcc56 — refactor impl-review F1 rename
- 441662a — test Phase 2 (e2e sparkline render + fallback)
- d020409 — chore epilogue
