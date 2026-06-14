<!-- PLAN-REVIEW-REPORT -->
# Plan Review: PUL-29 — Extract x_posts table, remove post_text duplication

- **Plan**: context/changes/pul-29-bq-x-posts-table/plan.md
- **Mode**: Deep
- **Date**: 2026-06-14
- **Verdict**: REVISE → SOUND (after fixes)
- **Findings**: 1 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL → PASS (F1 fixed) |
| Plan Completeness | WARNING → PASS (F2 fixed) |

## Grounding

8/8 paths ✓, symbols ✓, brief↔plan ✓ — except the plan referenced an "API startup hook"
that does not exist in src/api.py (no lifespan/on_event/startup). Confirmed
create_table_if_not_exists/ensure_schema_current are called only in main.py:39-40.

## Findings

### F1 — x_post_id column never migrates outside the scraper

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 1 #1 / Phase 2 #1-#2 / Migration Notes
- **Detail**: The plan relied on ensure_schema_current() to add announcements.x_post_id, but
  that runs only in main.py (scraper). post_main.py and src/api.py never ensure schema. After
  a deploy, if post_main runs before the next scraper cycle, save_x_post's UPDATE on
  a.x_post_id hits a missing column → BigQueryError; same for the admin LEFT JOIN.
- **Fix ⭐ Recommended**: Run full schema-ensure in post_main.main() — add
  create_table_if_not_exists() + ensure_schema_current() + create_x_posts_table_if_not_exists()
  at the top of post_main.main(), making the post job self-sufficient (mirrors main.py:38-40).
  - Strength: post_main is the only writer; removes the cross-job ordering dependency.
  - Tradeoff: one extra get_table/update_table per run (idempotent, ≤3×/day).
  - Confidence: HIGH — mirrors main.py:39-40.
  - Blind spot: admin read can race a brand-new deploy until any job runs once; acceptable.
- **Decision**: FIXED (Phase 2 #1 contract, Phase 2 #2 note, Migration Notes updated)

### F2 — Plan references a non-existent "API startup hook"

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 2 #2 "Startup parity"
- **Detail**: Phase 2 #2 told the implementer to add the helper "next to … the API startup".
  No API startup/lifespan exists in src/api.py — would cause confusion or an invented
  FastAPI lifespan (unplanned scope).
- **Fix**: Reword Phase 2 #2 to target main.py only (and post_main per F1); drop the API
  reference and add an explicit "do not add a lifespan" note.
- **Decision**: FIXED (Phase 2 #2 reworded)

### F3 — JOIN column qualification (confirmation, no change needed)

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architectural Fitness
- **Location**: Phase 1 #4
- **Detail**: _build_filter_clauses columns (analysis_approved, ticker, company, event_type,
  published_at) do not exist in x_posts → unambiguous under the JOIN. Only
  post_text/posted_at/supervisor_attempts overlap and are COALESCE'd. No filter-clause change.
- **Fix**: Clarify in Phase 1 #4 that _build_filter_clauses needs no change and must not be
  over-qualified.
- **Decision**: FIXED (Phase 1 #4 contract clarified)
