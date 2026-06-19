<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Remove analysis_score from user-facing /announcements response

- **Plan**: context/changes/remove-analysis-score/plan.md
- **Scope**: Phase 1 of 1
- **Date**: 2026-06-19
- **Verdict**: APPROVED
- **Findings**: 0 critical 0 warnings 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

## Evidence

- Git scope: commits `2850cce` (test-first implementation) and `e8d8889` (epilogue) touch exactly `src/api.py`, `db/bigquery.py`, `tests/test_api.py`, `tests/test_bigquery.py` plus the change folder — matches the plan's file list, no unplanned files.
- Plan drift sub-agent: all 5 planned changes verified MATCH against actual code (`src/api.py:89-95`, `db/bigquery.py:642-671`, `tests/test_api.py:90-100,48-64`, `tests/test_bigquery.py:521-529`). All "What We're NOT Doing" guardrails respected — `AnnouncementAdmin`, `list_announcements_admin`, `static/index.html`, and the BQ schema column for `analysis_score` are untouched.
- Safety/quality sub-agent: `list_announcements_user` and `list_announcements_admin` both build queries via the shared `_build_filter_clauses` helper — fully parameterized, no SQL injection risk introduced or pre-existing. No auth, performance, or data-safety issues.
- Automated success criteria re-run live: `pytest tests/test_api.py -v` (27 passed), `pytest tests/test_bigquery.py -v` (39 passed), full suite `pytest` (217 passed).
- Manual success criteria (1.4-1.6): confirmed live in-session against real BigQuery on localhost — user key (`test1`) response had no `analysis_score` key; admin key (`test`) response retained `analysis_score` (e.g. MRB → 105.0). Not rubber-stamped — observed directly.

## Findings

### F1 — Stale analysis_score key left in user-path test fixture

- **Severity**: ℹ️ OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: tests/test_api.py:90-93
- **Detail**: `test_announcements_user_returns_subset_fields`'s `mock_rows` still included `"analysis_score": 0.7` as upstream input. The test correctly asserted the output key-set excluded it (via Pydantic's `extra="ignore"`), but the fixture no longer reflected what `list_announcements_user` actually returns post-fix (no `analysis_score` key at all).
- **Fix**: Removed `"analysis_score": 0.7` from `mock_rows` so the fixture matches the real upstream shape.
- **Decision**: FIXED
