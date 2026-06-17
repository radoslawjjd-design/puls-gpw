<!-- PLAN-REVIEW-REPORT -->
# Plan Review: X-post Selection & Amounts Guard

- **Plan**: `context/changes/xpost-selection-and-amounts-guard/plan.md`
- **Mode**: Deep
- **Date**: 2026-06-17
- **Verdict**: REVISE (all findings triaged → SOUND)
- **Findings**: 0 critical, 2 warnings, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | WARNING |
| Blind Spots | WARNING |
| Plan Completeness | PASS |

## Grounding
6/6 paths ✓, 3/3 symbols ✓, brief↔plan ✓ (contract-surfaces.md absent → skipped).

## Findings

### F1 — Selection helper in post_generator drags genai into the DB layer

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Architectural Fitness
- **Location**: Phase 1, changes #1-2
- **Detail**: db/bigquery.py imports only stdlib today (verified lines 22-28, no src.* imports); post_generator.py imports `google.genai` + `gemini_client` at module load. Calling the helper from there reverses the dependency direction and drags the genai import chain into the BQ module. The helper only needs json5.
- **Fix A ⭐ Recommended**: New `src/post_selection.py` (json5 + logging only); db/bigquery.py imports from it.
  - Strength: Keeps db→src pointed at a leaf module with no genai; helper stays pure + testable; honors the settled "composed by fetch_top_n_for_window" decision.
  - Tradeoff: One new small module file.
  - Confidence: HIGH — db/bigquery.py verified to import no src module today.
  - Blind spot: None significant.
- **Fix B**: Keep it in post_generator, accept the coupling.
  - Strength: No new file.
  - Tradeoff: db layer imports genai transitively; dependency direction inverts.
  - Confidence: MED.
  - Blind spot: Import-time genai cost in BQ-only contexts unmeasured.
- **Decision**: FIXED via Fix A — helper moved to `src/post_selection.py`; `NUMBER_DEPENDENT_EVENT_TYPES` exported there; db/bigquery.py + Phase 2 belt import from it; test file `tests/test_post_selection.py`.

### F2 — No real-BigQuery round-trip for the SQL change (lessons.md rule)

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1 Success Criteria (Manual Verification)
- **Detail**: Phase 1 rewrites hand-written SQL but verifies via mocked pytest only. lessons.md ("BigQuery — reserved keywords + mocked-test limits", PUL-29) is an accepted rule: mocked BQ tests don't exercise the SQL parser; a real round-trip via scripts/test_bq.py is mandatory for SQL changes. The query-string regression test was present; the round-trip step was missing.
- **Fix**: Add a Manual Verification bullet — real-BQ round-trip via scripts/test_bq.py — and keep the query-string assertion as an automated test.
- **Decision**: FIXED — added Phase 1 manual bullet 1.6 (scripts/test_bq.py round-trip); query-string assertion clarified in Testing Strategy.

### F3 — Second caller of fetch_top_n_for_window not mentioned

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Blind Spots
- **Location**: Phase 1
- **Detail**: `_run_generate_post.py:162` also calls fetch_top_n_for_window(n=4). Return contract unchanged, so safe — worth noting the blast radius is exactly two callers.
- **Fix**: Note the second caller in References; no code change needed.
- **Decision**: FIXED — blast-radius note added to References.

### F4 — Progress phase headings drop the parenthetical suffix

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: ## Progress
- **Detail**: Plan headers read "Phase 1: … (Defects A + B)" / "Phase 2: … (Defect B defense-in-depth)" but Progress headings omitted the suffix. Parses fine (by number) but exact-match is cleaner.
- **Fix**: Align the two Progress headings with the plan phase titles.
- **Decision**: FIXED — both Progress headings now carry the parenthetical suffix.

### F5 — Belt's "\d present" is a coarse proxy for "has an amount"

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: End-State Alignment
- **Location**: Phase 2
- **Detail**: A results tweet with only "Q1"/"2025" would pass the \d check. Acceptable because Phase 1's selection drop guarantees non-empty key_numbers (real figures) for surviving wyniki_* rows, so the belt is defense-in-depth, not the primary guarantee.
- **Fix**: Note in the plan that \d is intentionally coarse.
- **Decision**: FIXED — Phase 2 contract now states the coarseness is a conscious choice.
