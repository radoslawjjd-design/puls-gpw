<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: My Wallet — Personal Watchlist

- **Plan**: context/changes/my-wallet-watchlist/plan.md
- **Scope**: Full plan (Phases 1-3)
- **Date**: 2026-06-23
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 1 observation

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS |
| Scope Discipline | PASS |
| Safety & Quality | PASS |
| Architecture | PASS |
| Pattern Consistency | WARNING |
| Success Criteria | PASS |

## Findings

### F1 — Company→ticker resolution can false-negative on edge inputs

- **Severity**: WARNING
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency / Data safety
- **Location**: static/index.html — `_resolveTickerForCompany()`
- **Detail**: Resolves a company name to its ticker by calling the existing `GET /announcements?company=...&page_size=20` (a LIKE/substring server-side filter) and exact-matching the `company` field client-side. Two edge cases produce a false "Nie znaleziono tickera dla tej spółki" even though the company exists: (1) a company whose substring collides with >20 other companies' announcements before the exact match appears in the page, and (2) free-typed input with different case/whitespace than the stored value, since the match is case-sensitive `===`. Importantly: this never resolves to the WRONG ticker — it fails closed (blocks the add with a visible error), it just occasionally under-resolves a valid company. The dropdown-driven path (the intended UX) is unaffected since it places the exact stored string.
- **Fix**: Raise `page_size` in the lookup (e.g. 100) and/or compare case-insensitively, to shrink the false-negative window.
- **Decision**: FIXED — page_size raised to 100, company match now case-insensitive (static/index.html `_resolveTickerForCompany()`)

### F2 — Watchlist startup hook uses deprecated on_event

- **Severity**: OBSERVATION
- **Impact**: LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Architecture
- **Location**: src/api.py:146
- **Detail**: The startup table-creation hook uses FastAPI's deprecated `@app.on_event("startup")` (confirmed via DeprecationWarning in test output). It's the right place to run it — the plan explicitly chose API-startup over the pipeline entry points since watchlist is API-only — but it's the only `on_event` usage in the file and carries a deprecation warning into brand-new code.
- **Fix**: Use a `lifespan` async context manager instead. Out of scope for this change (pre-existing FastAPI app has no lifespan manager to extend yet) — fine to defer.
- **Decision**: SKIPPED — pre-existing FastAPI pattern question, not introduced by this feature; defer to a dedicated refactor

## Success criteria verified

- `uv run pytest tests/test_bigquery.py tests/test_api.py` — 106 passed
- `uv run pytest` (full suite) — 312 passed
- `uv run python -m py_compile src/api.py` — OK
- Manual Progress items 3.2-3.10 — confirmed by product owner, with observable evidence in the diff
