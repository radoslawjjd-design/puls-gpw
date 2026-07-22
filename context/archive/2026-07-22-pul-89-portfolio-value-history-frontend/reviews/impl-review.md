<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: FARO-5 Frontend — Value-History Chart

- **Plan**: context/changes/pul-89-portfolio-value-history-frontend/plan.md
- **Scope**: Full plan (Phase 1–2, all Progress [x])
- **Date**: 2026-07-23
- **Verdict**: APPROVED (1 minor warning, fixed)
- **Findings**: 0 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | WARNING |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Findings

### F1 — Range switcher has no out-of-order fetch guard

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality (Reliability)
- **Location**: static/index.html — range handler (`value-history range switcher`) → `fetchPortfolioHistory`
- **Detail**: Fast range clicks (1M then 3M) fire overlapping async fetches; each resolves with `_ppHistData = data; _renderPortfolioHistory(data)`. If the earlier request resolves last, the chart renders the stale range while the active button + URL show the newer one — the documented "SPA out-of-order fetch" lesson. The URL stays correct (written synchronously from `_ppHistRange`); only the rendered chart can desync. Low likelihood, read-only, no data loss.
- **Fix**: Added a request-sequence guard in `fetchPortfolioHistory` (`_ppHistReqSeq`): `const seq = ++_ppHistReqSeq;` at entry, `if (seq !== _ppHistReqSeq) return;` after each await before rendering/erroring. ~5 lines; discards stale responses. Matches lessons.md option 2.
- **Decision**: FIXED (commit pending) — validated: JS syntax OK, 3 E2E green after fix.

### F2 — Empty-state not covered at browser level

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Success Criteria
- **Location**: tests/e2e/test_portfolio_value_history.py
- **Detail**: The empty-range "Brak danych dla tego zakresu" branch is verified by the unit-level render check, not E2E — the single conftest fake portfolio always returns data. A conftest fixture branch returning `[]` would be needed to drive it in-browser. Noted in the plan; no action required.
- **Decision**: ACCEPTED (no action)

## Notes

- Both relevant lessons.md entries were checked: "SPA out-of-order fetch" → F1 (fixed); "new view hidden by ALL show*View" → N/A (the chart embeds inside the existing `pp-calendar-wrap`, no new top-level view).
- The design revision (chart moved from a standalone tab to under the calendar, per user feedback) is documented in the plan's Overview revision note; not scope creep.
- Success criteria verified: `node --check` on inline JS passes; `/health` + `/` return 200 via TestClient; 3 E2E tests green and deliberate-break-verified.
