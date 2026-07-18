<!-- IMPL-REVIEW-REPORT -->
# Implementation Review: Onboarding Landing + Login/Register (PUL-72)

- **Plan**: context/changes/login-register-landing/plan.md
- **Scope**: Phase 1 of 3 (commit 74361de)
- **Date**: 2026-07-18
- **Verdict**: APPROVED
- **Findings**: 0 critical, 1 warning, 3 observations

## Verdicts

| Dimension | Verdict |
|-----------|---------|
| Plan Adherence | PASS (4/4 MATCH) |
| Scope Discipline | PASS (1 benign extra: generic-500 hardening test) |
| Safety & Quality | WARNING (F1 — fixed in triage) |
| Architecture | PASS |
| Pattern Consistency | WARNING (F3/F4 — fixed in triage) |
| Success Criteria | PASS (485 unit + 68 e2e, re-run during review) |

## Findings

### F1 — No negative caching: BQ load unbounded during error conditions

- **Severity**: ⚠️ WARNING
- **Impact**: 🔎 MEDIUM — real tradeoff; pause to reason through it
- **Dimension**: Safety & Quality
- **Location**: src/api.py:309-317
- **Detail**: 60s cache only bounded BQ calls on the success path; during a BQ
  outage every unauthenticated landing request fired a fresh query.
- **Fix A ⭐ (chosen)**: on BigQueryError, cache `[]` under the main key and
  return 200+[] — outage costs ≤1 query/60s/instance; landing hides the strip
  on an empty list. Trade: cards return ≤60s after recovery; no 500 for monitoring.
- **Fix B**: separate error key with 15s TTL, keep 500 semantics.
- **Decision**: FIXED via Fix A (handler + test renamed to
  `test_public_top_announcements_bq_error_serves_empty_and_negative_caches`)

### F2 — Blocking sync BQ call inside async def on a public route

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW — quick decision; fix is obvious and narrowly scoped
- **Dimension**: Pattern Consistency / Reliability
- **Location**: src/api.py (public_top_announcements)
- **Detail**: Repo-wide pattern (only treemap uses asyncio.to_thread); first
  unauthenticated route with this property. Mitigated by the 60s cache.
- **Decision**: SKIPPED (consistent with repo; revisit if landing traffic grows)

### F3 — Missing query-timing debug log (sibling pattern)

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py (list_top_announcements_public)
- **Detail**: Siblings wrap queries in `_t = time.time()` + `logger.debug`.
- **Decision**: FIXED (2 lines added per sibling pattern)

### F4 — Docstring placeholder renders literally

- **Severity**: 💡 OBSERVATION
- **Impact**: 🏃 LOW
- **Dimension**: Pattern Consistency
- **Location**: db/bigquery.py (list_top_announcements_public docstring)
- **Detail**: `{_ANNOUNCEMENTS_DEFAULT_DAYS}` in a plain string.
- **Decision**: FIXED ("90 days (see _ANNOUNCEMENTS_DEFAULT_DAYS)")

## Triage summary

- Fixed: F1 (Fix A), F3, F4
- Skipped: F2
- Post-fix verification: unit suite 485 passed
