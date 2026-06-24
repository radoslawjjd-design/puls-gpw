<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Companies Dictionary Table (ticker, name, hop_url, isin)

- **Plan**: context/changes/companies-dictionary-table/plan.md
- **Mode**: Deep
- **Date**: 2026-06-23
- **Verdict**: REVISE → all findings fixed during triage
- **Findings**: 1 critical, 2 warnings, 0 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | WARNING |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

6/6 paths ✓, 8/8 symbols ✓, brief↔plan ✓

## Findings

### F1 — First-ever MERGE statement isn't round-tripped against real BigQuery until Phase 3

- **Severity**: ❌ CRITICAL
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1 — Companies table schema + CRUD (Manual Verification)
- **Detail**: `upsert_company()` is the first `MERGE` statement anywhere in this codebase (confirmed via grep — none exist in `db/bigquery.py`). Phase 1's Manual Verification only exercised table creation, not the MERGE itself; the SQL wasn't exercised against real BigQuery until Phase 3, two phases later — repeating the shape of a documented past incident (`lessons.md`, PUL-29: mocked tests never send the query string to BigQuery's parser).
- **Fix**: Add a manual verification bullet to Phase 1 — round-trip `upsert_company()` against real BigQuery (insert path, then update path on the same ticker) before moving to Phase 2.
- **Decision**: FIXED — added to Phase 1 Manual Verification + Progress 1.5.

### F2 — Success criterion promises coverage the plan's own architecture can't deliver for delisted tickers

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: End-State Alignment
- **Location**: Desired End State / Migration Notes
- **Detail**: Plan promised every ticker in `list_distinct_tickers()` gets a `companies` row, via the unverified assumption "GPW-listed companies with historical announcements are a subset of the full listing." False for delisted/merged tickers with no future announcements — Phase 3 only writes forward, Phase 4's seed only sees the current live listing.
- **Fix A ⭐ Recommended**: Narrow Desired End State / Success Criteria wording to scope the guarantee to current GPW-listed companies plus tickers with future announcements; name the delisted-ticker gap as accepted, out-of-scope risk in Migration Notes.
  - Strength: Matches what the four phases can actually deliver; no extra work.
  - Tradeoff: Follow-up daily company-stats job may assume full coverage and hit the same gap later.
  - Confidence: HIGH — GPW delistings/mergers are routine.
  - Blind spot: Real count of affected tickers in this project's `announcements` table is unmeasured.
- **Fix B**: Add a post-Phase-4 reconciliation step logging tickers present in `list_distinct_tickers()` but absent from `companies`.
  - Strength: Makes the gap size visible without committing to closing it.
  - Tradeoff: Extra Phase 4 work for a possibly-small number.
  - Confidence: MEDIUM.
  - Blind spot: Same as Fix A.
- **Decision**: FIXED via Fix A — Desired End State and Migration Notes reworded.

### F3 — Plan's "keeps its existing signature" claim contradicts its own return-type change

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2, item 2 (`_extract_ticker_company` refactor)
- **Detail**: Contract claimed `_extract_ticker_company` "keeps its existing signature" while also changing its return type to a 4-tuple — self-contradictory. Also omitted the one call site that must change, `src/parser.py:50`'s 2-tuple unpack (confirmed via grep as the only caller).
- **Fix**: Reworded to "keeps its existing parameters; return type changes..." and explicitly listed the `src/parser.py:50` call-site update.
- **Decision**: FIXED.
