<!-- PLAN-REVIEW-REPORT -->
# Plan Review: Pagination Implementation Plan

- **Plan**: context/changes/pagination/plan.md
- **Mode**: Deep
- **Date**: 2026-06-12
- **Verdict**: REVISE → SOUND (after fixes)
- **Findings**: 1 critical  1 warning  0 observations

## Verdicts

| Dimension | Verdict |
|---|---|
| End-State Alignment | PASS |
| Lean Execution | PASS |
| Architectural Fitness | PASS |
| Blind Spots | FAIL |
| Plan Completeness | WARNING |

## Grounding

6/6 paths ✓, 3/3 symbols ✓, brief↔plan ✓. Blast radius: `list_announcements_admin/user` called only from `src/api.py` and `tests/test_bigquery.py` — no hidden callers.

## Findings

### F1 — Phase 4 conftest: TestClient nie działa z Playwright

- **Severity**: ❌ CRITICAL
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Blind Spots
- **Location**: Phase 4 — conftest.py
- **Detail**: TestClient uses in-memory ASGI transport without a real TCP socket. Playwright (a real browser) cannot connect to `http://testserver` — all E2E tests fail immediately with connection refused. The brief's Open Risks flagged "conftest fixture needs care" but the plan's contract described the wrong approach anyway.
- **Fix**: Replace TestClient with `uvicorn.Server` in a daemon thread on port 18099. Set env vars via `os.environ` before thread start (monkeypatch doesn't cross thread boundaries). Poll `/health` in a retry loop before yielding the URL.
- **Decision**: FIXED — plan updated with uvicorn.Server contract

### F2 — Phase 3: auto-refetch przy zmianie page_size nie jest wpisany w Changes Required

- **Severity**: ⚠️ WARNING
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Plan Completeness
- **Location**: Phase 3 — Changes Required 3 (JS logic)
- **Detail**: Success criterion 3.4 says "Changing page_size resets to page 1 and refetches" but Changes Required only mentioned reset on form submit. No `addEventListener('change', ...)` on `#f-page-size` was specified — implementer had to guess.
- **Fix**: Added `$('f-page-size').addEventListener('change', () => { currentPage = 1; fetchAnnouncements(); })` to Changes Required 3.
- **Decision**: FIXED — plan updated
