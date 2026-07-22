<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Portfolio Value-History Endpoint (PUL-79 / FARO-5)

- **Plan**: context/changes/pul-79-portfolio-value-history/plan.md
- **Mode**: Deep
- **Date**: 2026-07-22
- **Verdict**: REVISE → SOUND (after fixes)
- **Findings**: 1 critical, 1 warning, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | FAIL → PASS (F1 fixed) |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | PASS (folded into F1) |
| Plan Completeness | WARNING → PASS (F2 fixed) |

## Grounding

5/5 paths ✓, 4/4 symbols ✓, brief↔plan ✓, blast radius ✓ (adds a sibling; `get_portfolio_calendar_data` untouched), dateutil/relativedelta absent from repo → F2.

## Findings

### F1 — 0-fill on price gaps produces a spurious value/PnL curve

- **Severity**: ❌ CRITICAL
- **Impact**: 🔬 HIGH — architectural stakes; think carefully before deciding
- **Dimension**: End-State Alignment
- **Location**: Phase 1 — New BQ function (query design)
- **Detail**: The plan reused the calendar CTE, which 0-fills a position's value when its close is NULL (`db/bigquery.py:422`). Verified on the live owner portfolio: coverage is 9/12 for most of July then 12/12 from 2026-07-17, so 0-fill yields a fake ~25% value step and a deeper `pnl` dip (full 12-position cost basis subtracted on 9-priced days). The "plausible ascending value series" success criterion could not be met.
- **Fix A ⭐ Recommended**: Forward-fill (LOCF) + clamp series start to first fully-covered day; pnl over the same priced set.
  - Strength: Removes both the daily flicker and the onset step → genuinely plausible curve; artifact confirmed real on live data.
  - Tradeoff: A `1y` request may return <1y of points when a holding's price history is short.
  - Confidence: HIGH — coverage pattern confirmed on live data.
  - Blind spot: LOCF query is more complex than the calendar's exact-day join; needs its own unit test.
- **Fix B**: LOCF only, keep full range, exclude unpriced positions from both value and cost basis.
  - Strength: Preserves range length; smaller change; fixes the pnl double-count.
  - Tradeoff: Onset step in value_pln remains when a ticker's history starts mid-range.
  - Confidence: MED.
  - Blind spot: Frontend rendering of the residual step unverified.
- **Decision**: FIXED via Fix A (LOCF + clamp to full-coverage day; pnl over same priced set). Edits: Key Discoveries gap note, new Critical Implementation Details bullet, Phase 1 function Contract query spec, Phase 1 test contract + Testing Strategy (query-structure assertions), Phase 1 manual criterion 1.4 (no-spurious-step check).

### F2 — Range resolver: month math undefined, no dateutil in repo

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 — Range resolver
- **Detail**: Plan wrote "1m→−1mo, 3m→−3mo, 1y→−1y" but stdlib `date` has no month arithmetic and `dateutil`/`relativedelta` is not a repo dependency. Implementer would add a dep or guess at day counts.
- **Fix**: Specify day-based floors — 1w=7, 1m=30, 3m=90, 1y=365 days via `date.today() - timedelta(days=N)`; no new dependency.
- **Decision**: FIXED. Edit: Phase 2 range-resolver Contract now specifies day-based floors and forbids adding dateutil.
