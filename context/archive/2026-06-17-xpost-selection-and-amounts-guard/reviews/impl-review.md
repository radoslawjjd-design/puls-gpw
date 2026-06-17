<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: X-post Selection & Amounts Guard

- **Plan**: context/changes/xpost-selection-and-amounts-guard/plan.md
- **Scope**: Phase 1–2 of 2
- **Date**: 2026-06-17
- **Verdict**: APPROVED
- **Findings**: 0 critical, 0 warnings, 4 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | PASS |
| Success Criteria | PASS |

Automated criteria re-verified: 62 targeted tests passed, 173 full suite passed.
No new lint classes (only pre-existing E402 from load_dotenv-first convention).
Manual 1.4–1.6 confirmed in Phase 1 review; 2.4–2.5 confirmed this session.

## Findings

### F1 — Dedup-before-drop: first-occurrence ticker cannot be rescued

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Adherence
- **Location**: src/post_selection.py:71–73
- **Detail**: A ticker's first (highest-priority) row is marked seen before the number-check. If that row is a number-less wyniki_*, the ticker is consumed and then dropped — but a lower-ranked numbered wyniki row for the same ticker is skipped because the ticker is already seen. Already reviewed and accepted as SKIPPED in the Phase 1 impl-review (matches plan's documented dedup→drop ordering; real-world likelihood low).
- **Fix**: No change needed. Per-company "prefer numbered results row" logic would require a plan change; tracked as follow-up.
- **Decision**: SKIPPED — matches plan intent; per-company rescue is follow-up work

### F2 — LIMIT 200 bare magic number in SQL string

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py:356 + tests/test_bigquery.py:156
- **Detail**: `LIMIT 200` is embedded as a literal in the SQL string; the test asserts "LIMIT 200". Changing the cap requires editing both the query and the test with no single source of truth.
- **Fix**: Define `_FETCH_SAFETY_CAP = 200` in bigquery.py and interpolate it; update the test to check `f"LIMIT {_FETCH_SAFETY_CAP}"`.
- **Decision**: FIXED — `_FETCH_SAFETY_CAP = 200` constant added to db/bigquery.py; SQL and test updated

### F3 — _results_tweets_have_numbers: short-thread passthrough undocumented

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Safety & Quality
- **Location**: post_main.py:100
- **Detail**: `body = tweets[1:-1]` yields `[]` for a 0–2 tweet thread, returning True (passthrough). Safe in practice because `is_publishable` pre-screens short threads, but the rationale is not documented in the function.
- **Fix**: Add a one-line comment noting that `is_publishable` pre-screens short threads so an empty body is unreachable here.
- **Decision**: FIXED — comment added to post_main.py

### F4 — select_top_companies docstring implies two-pass; code is one-pass

- **Severity**: 🔭 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency
- **Location**: src/post_selection.py:55–57
- **Detail**: Docstring "dedup by ticker → drop … → take first n" reads like three sequential passes. The implementation is a single loop that interleaves dedup and drop — "first occurrence wins" with no rescue possible for a dropped ticker. A reader expecting two-pass semantics may misread the invariant.
- **Fix**: Reword to clarify single-pass first-occurrence semantics and note that a seen ticker's later rows are skipped regardless of key_numbers.
- **Decision**: FIXED — docstring in src/post_selection.py rewritten to single-pass semantics
