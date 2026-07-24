<!-- PLAN-REVIEW-REPORT -->
# Plan Review: PUL-91 — second "Wszystkie" chart + dynamic titles

- **Plan**: context/changes/pul-91-kalendarz-wszystkie-chart-dynamic-titles/plan.md
- **Mode**: Deep
- **Date**: 2026-07-24
- **Verdict**: REVISE (all findings LOW-impact; fixed in triage)
- **Findings**: 0 critical, 2 warnings, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | WARNING |
| Plan Completeness | WARNING |

## Grounding
5/5 paths ✓, symbols ✓ (28 hits in static/index.html), backend all-mode confirmed (src/api.py:1008), conftest fake confirmed (tests/e2e/conftest.py:359), brief↔plan ✓.

## Findings

### F1 — Duplicate SVG gradient id across the two charts

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1, change #5 (parameterize _renderPortfolioHistory)
- **Detail**: `gid = 'pp-hist-grad-' + (pnl?'p':'v')` (static/index.html:4064) is a fixed string; both charts share `_ppHistMetric`, so non-all-mode emits two `<linearGradient>` with the same DOM id. Invalid HTML; `url(#…)` resolves to first match. Latent correctness/a11y smell.
- **Fix**: Namespace `gid` per target (incorporate `chartEl.id`) + vary SVG `aria-label` per chart.
- **Decision**: FIXED (plan change #5 updated)

### F2 — Null-portfolio empty-state target ambiguous after removing #pp-history-chart

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 1, change #6 (orchestrator)
- **Detail**: Guard message was written to the removed `#pp-history-chart` id; plan didn't say where it goes now.
- **Fix**: Null case hides both chart blocks.
- **Decision**: FIXED (plan change #6 updated)

### F3 — Aggregate title text appears in two states → scope e2e locators

- **Severity**: 🔷 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Plan Completeness
- **Location**: Phase 2 (e2e)
- **Detail**: "Wartość wszystkich portfeli w czasie" appears as single chart (all-mode) and chart #2 (non-all-mode); bare page-level getByText is ambiguous.
- **Fix**: Scope assertions within block containers (#pp-history-block-active / -all).
- **Decision**: FIXED (plan Phase 2 change #2 updated)
